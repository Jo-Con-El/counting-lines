# counting-lines

Lines of code over a git repository's history, computed incrementally and
without spawning a process per commit.

Inspired by [loc-graph-action](https://github.com/botforge-pro/loc-graph-action),
which runs `git checkout` and `cloc` over the whole tree for every commit.

## How it works

- A single `git log --first-parent --raw` reports, for every commit on the
  first-parent chain, which blobs changed relative to its first parent.
- A single `git cat-file --batch` reads only blobs that have never been
  counted. Each blob is counted once (code, comment and blank lines, cloc
  style), even if it appears in a thousand commits or moves around.
- Per-language totals are kept by subtracting the old blob and adding the new
  one, so the cost is proportional to the changes, not to the size of the tree.
- The cache (`.git/counting-lines/cache.json.gz` by default) stores the last
  processed commit, the tree state and per-blob stats. The next run only
  processes what is new. If history was rewritten, it starts over but reuses
  the blob stats.

No dependencies: just Python ≥ 3.10 and git ≥ 2.31.

## Usage

```bash
python3 -m counting_lines [REPO] [options]
```

With no outputs, it prints a per-language summary of the last commit:

```bash
python3 -m counting_lines ~/src/project
```

Generate data and a chart:

```bash
python3 -m counting_lines --json loc.json --js loc.js --svg loc.svg
```

When installed (`pip install .`), the same is available as the `counting-lines`
command.

Main options:

| Option | What it does |
| --- | --- |
| `-r, --rev REV` | Last commit to process (default `HEAD`). If it is already in the cached history, nothing is recomputed. |
| `--cache FILE` / `--no-cache` | Where to keep the cache, or don't use one. |
| `-x, --exclude PATTERN` | Exclude paths. Without `/` it matches any directory or file name (`node_modules`, `*.min.js`); with `/` it matches from the repository root (`docs/api`). Repeatable. |
| `--no-default-excludes` | Don't apply the default exclusions (`package-lock.json`, `node_modules`). |
| `-l, --include-lang LIST` | Report only these languages (`Python,C`). Applied when writing outputs; it does not invalidate the cache. |
| `--json FILE` | Data as JSON, one point per line. |
| `--js FILE` | The same data as `globalThis.LOC_DATA = …` (`--js-var` changes the name, `--js-esm` emits an ES module). |
| `--svg FILE` | Stacked area chart by language. `--theme auto` follows `prefers-color-scheme`; `light` and `dark` are also available. See also `--top N`, `--x-axis time\|commits`, `--title`, `--date-format`. |
| `--check` | Recount the tree at `--rev` from scratch and compare it with the incremental result. |

## Data format

```json
{"generator": "counting-lines", "generated_at": "…", "rev": "<sha>",
 "languages": ["C", "Python"],
 "points": [
  {"sha": "…", "date": "2024-01-01T00:00:00+00:00", "code": 120, "comment": 30, "blank": 15,
   "langs": {"C": [100, 25, 10], "Python": [20, 5, 5]}}
 ]}
```

`languages` is ordered by lines of code at the last commit. Each `langs` entry
is `[code, comment, blank]`.

## Limitations

- Only the first-parent chain is walked: a merged branch shows up as a single
  step at the merge commit.
- The comment classifier is heuristic, like cloc's: comment markers inside
  string literals are not recognised as such, and Python docstrings count as
  comments.
- Files with unknown extensions, binaries, symlinks and submodules are not
  counted.

## Tests

```bash
python3 -m unittest discover -s tests
```
