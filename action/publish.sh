#!/usr/bin/env bash
# Reports whether the generated files changed and, if asked, commits and pushes them.
set -euo pipefail

files=()
for f in "${INPUT_SVG:-}" "${INPUT_JSON:-}" "${INPUT_JS:-}"; do
  [ -n "$f" ] && files+=("$f")
done

changed=false
if [ "${#files[@]}" -gt 0 ] && [ -n "$(git status --porcelain -- "${files[@]}")" ]; then
  changed=true
fi
echo "changed=$changed" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "Generated files changed: $changed"

if [ "$changed" = "true" ] && [ "${INPUT_COMMIT:-false}" = "true" ]; then
  git add -- "${files[@]}"
  git -c user.name="github-actions[bot]" \
      -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit --quiet -m "${INPUT_COMMIT_MESSAGE:-Update lines-of-code chart [skip ci]}"
  git push --quiet
  echo "Committed and pushed: ${files[*]}"
fi
