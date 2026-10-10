#!/usr/bin/env bash
# Reports whether the generated files changed and, if asked, publishes them:
# - with INPUT_BRANCH, as the only commit of that branch (replaced on each update);
# - with INPUT_COMMIT=true, as a new commit on the current branch.
set -euo pipefail

BOT_NAME="github-actions[bot]"
BOT_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"
MESSAGE="${INPUT_COMMIT_MESSAGE:-Update lines-of-code chart [skip ci]}"

files=()
for f in "${INPUT_SVG:-}" "${INPUT_JSON:-}" "${INPUT_JS:-}"; do
  [ -n "$f" ] && files+=("$f")
done

report() {
  echo "changed=$1" >> "${GITHUB_OUTPUT:-/dev/null}"
  echo "Generated files changed: $1"
}

if [ "${#files[@]}" -eq 0 ]; then
  report false
  exit 0
fi

if [ -n "${INPUT_BRANCH:-}" ]; then
  branch="$INPUT_BRANCH"
  top="$(git rev-parse --show-toplevel)"

  # Build the tree in a scratch index: no checkout, the workspace is untouched.
  scratch="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/counting-lines.XXXXXX")"
  trap 'rm -rf "$scratch"' EXIT
  export GIT_INDEX_FILE="$scratch/index"
  git read-tree --empty
  for f in "${files[@]}"; do
    f="${f#./}"
    case "$f" in /*) abs="$f" ;; *) abs="$PWD/$f" ;; esac
    case "$abs" in
      "$top"/*) ;;
      *) echo "::error::$f is outside the repository; it cannot be published to a branch"; exit 1 ;;
    esac
    blob="$(git hash-object -w -- "$abs")"
    git update-index --add --cacheinfo "100644,$blob,${abs#"$top"/}"
  done
  tree="$(git write-tree)"

  # Only ever replace a branch made of a single parentless commit, as this
  # action creates: never force-push over a branch with history of its own.
  if git ls-remote --exit-code --heads origin "$branch" > /dev/null; then
    git fetch --quiet origin "refs/heads/$branch"
    # Read the raw commit object: shallow grafts could hide its parents.
    if git cat-file commit FETCH_HEAD | sed '/^$/q' | grep -q '^parent '; then
      echo "::error::Branch '$branch' has history of its own; refusing to replace it. Choose another branch name."
      exit 1
    fi
    if [ "$(git rev-parse 'FETCH_HEAD^{tree}')" = "$tree" ]; then
      report false
      exit 0
    fi
  fi

  report true
  commit="$(GIT_AUTHOR_NAME="$BOT_NAME" GIT_AUTHOR_EMAIL="$BOT_EMAIL" \
            GIT_COMMITTER_NAME="$BOT_NAME" GIT_COMMITTER_EMAIL="$BOT_EMAIL" \
            git commit-tree "$tree" -m "$MESSAGE")"
  git push --quiet --force origin "$commit:refs/heads/$branch"
  echo "Published to branch $branch: ${files[*]}"
  exit 0
fi

changed=false
if [ -n "$(git status --porcelain -- "${files[@]}")" ]; then
  changed=true
fi
report "$changed"

if [ "$changed" = "true" ] && [ "${INPUT_COMMIT:-false}" = "true" ]; then
  git add -- "${files[@]}"
  git -c user.name="$BOT_NAME" -c user.email="$BOT_EMAIL" commit --quiet -m "$MESSAGE"
  git push --quiet
  echo "Committed and pushed: ${files[*]}"
fi
