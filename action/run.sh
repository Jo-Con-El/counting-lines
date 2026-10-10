#!/usr/bin/env bash
# Runs counting-lines with the action's inputs (passed as INPUT_* variables).
set -euo pipefail

repo="${INPUT_PATH:-.}"
if [ "$(git -C "$repo" rev-parse --is-shallow-repository)" = "true" ]; then
  echo "Shallow clone: fetching the full history"
  git -C "$repo" fetch --quiet --unshallow
fi

# The generated files, relative to the repository root.
top="$(git -C "$repo" rev-parse --show-toplevel)"
outputs=()
for f in "${INPUT_SVG:-}" "${INPUT_JSON:-}" "${INPUT_JS:-}"; do
  [ -n "$f" ] || continue
  f="${f#./}"
  case "$f" in /*) abs="$f" ;; *) abs="$PWD/$f" ;; esac
  case "$abs" in "$top"/*) outputs+=("${abs#"$top"/}") ;; esac
done

# A commit touching only the generated files (typically the previous run's own
# commit) adds nothing to chart: leave everything as it is.
rev="$(git -C "$repo" rev-parse --verify "${INPUT_REV:-HEAD}^{commit}")"
if [ "${#outputs[@]}" -gt 0 ] && git -C "$repo" rev-parse --quiet --verify "$rev^" > /dev/null; then
  touched="$(git -C "$repo" -c core.quotePath=false diff-tree --no-commit-id --name-only -r "$rev^" "$rev")"
  only_outputs=true
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    match=false
    for o in "${outputs[@]}"; do [ "$path" = "$o" ] && match=true; done
    [ "$match" = "true" ] || only_outputs=false
  done <<< "$touched"
  if [ -n "$touched" ] && [ "$only_outputs" = "true" ]; then
    echo "The last commit only touches the generated files; nothing new to chart."
    exit 0
  fi
fi

args=("$repo" --rev "$rev" --cache "$CACHE_FILE")
# Never count the generated files themselves (the SVG is XML, the data JSON).
for o in ${outputs[@]+"${outputs[@]}"}; do
  args+=(--exclude "/$o")
done
[ -n "${INPUT_SVG:-}" ] && args+=(--svg "$INPUT_SVG")
[ -n "${INPUT_JSON:-}" ] && args+=(--json "$INPUT_JSON")
[ -n "${INPUT_JS:-}" ] && args+=(--js "$INPUT_JS")
args+=(--theme "${INPUT_THEME:-auto}" --style "${INPUT_STYLE:-areas}" --top "${INPUT_TOP:-6}"
       --title "${INPUT_TITLE:-Lines of code over time}" --x-axis "${INPUT_X_AXIS:-time}")
args+=(--project "${INPUT_PROJECT:-${GITHUB_REPOSITORY##*/}}")
[ -n "${INPUT_DATE_FORMAT:-}" ] && args+=(--date-format "$INPUT_DATE_FORMAT")
[ -n "${INPUT_INCLUDE_LANG:-}" ] && args+=(--include-lang "$INPUT_INCLUDE_LANG")
[ "${INPUT_SLOPES:-false}" = "true" ] && args+=(--slopes)
[ -n "${INPUT_LINE_WIDTH:-}" ] && args+=(--line-width "$INPUT_LINE_WIDTH")
[ "${INPUT_DEDUPE:-false}" = "true" ] && args+=(--dedupe)
[ "${INPUT_NO_DEFAULT_EXCLUDES:-false}" = "true" ] && args+=(--no-default-excludes)

# One exclusion pattern per line; surrounding blanks and empty lines are ignored.
while IFS= read -r pattern; do
  pattern="${pattern#"${pattern%%[![:space:]]*}"}"
  pattern="${pattern%"${pattern##*[![:space:]]}"}"
  [ -n "$pattern" ] && args+=(--exclude "$pattern")
done <<< "${INPUT_EXCLUDE:-}"

PYTHONPATH="$ACTION_PATH${PYTHONPATH:+:$PYTHONPATH}" python3 -m counting_lines "${args[@]}"
