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
| `-x, --exclude PATTERN` | Exclude paths. Without `/` it matches any directory or file name (`node_modules`, `*.min.js`); with `/` it matches from the repository root (`docs/api`, or `/loc.svg` for a file at the root). Repeatable. |
| `--no-default-excludes` | Don't apply the default exclusions (`package-lock.json`, `node_modules`, and `*.po`/`*.pot` translation catalogs). |
| `--dedupe` | Count identical files (same content and language) only once per commit, like cloc does. Useful when a repository carries copies of the same files, such as backup folders. |
| `-l, --include-lang LIST` | Report only these languages (`Python,C`). Applied when writing outputs; it does not invalidate the cache. |
| `--json FILE` | Data as JSON, one point per line. |
| `--js FILE` | The same data as `globalThis.LOC_DATA = …` (`--js-var` changes the name, `--js-esm` emits an ES module). |
| `--svg FILE` | Stacked area chart by language. `--theme auto` follows `prefers-color-scheme`; `light` and `dark` are also available. `--style lines` draws the same stacking as one line per language instead of filled areas, and `--style total` draws only the total as a line. Counts are drawn as steps, since they only change at commits; `--slopes` joins commits with straight lines instead. See also `--top N`, `--x-axis time\|commits`, `--title`, `--date-format`. |
| `-j, --jobs N` | Processes counting new blobs in parallel (default: all CPU cores). Each one keeps its own `git cat-file`; small incremental runs stay in a single process. |
| `--check` | Recount the tree at `--rev` from scratch and compare it with the incremental result. |
| `-q, --quiet` | No messages. Otherwise, when stderr is a terminal, a progress line shows the current phase, how far along it is and the estimated time left. |

## GitHub Action

The repository is also a composite action. This workflow keeps
`.github/loc-history.svg` up to date on every push to `main`:

```yaml
name: Lines of code
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: write  # to commit the chart

concurrency: lines-of-code  # one run at a time, so pushes do not race

jobs:
  chart:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # the action fetches the full history anyway, if needed
      - uses: Jo-Con-El/counting-lines@main
        with:
          svg: .github/loc-history.svg
          commit: true
```

Then show it in your README with `![Lines of code](.github/loc-history.svg)`.

- The cache is kept between runs with `actions/cache`, so each run only
  processes the new commits.
- The generated files are never counted themselves, and a run whose last commit
  only touches them (such as a scheduled run right after the previous chart
  commit) changes nothing.
- The chart commit is made by `github-actions[bot]` with `[skip ci]`; pushes
  made with the default `GITHUB_TOKEN` do not trigger new workflow runs.
- The `changed` output says whether any generated file changed, for workflows
  that publish the files some other way (`commit: false`, the default).

Inputs mirror the command line options: `path`, `rev`, `svg`, `json`, `js`,
`theme`, `style`, `slopes`, `top`, `title`, `x-axis`, `date-format`, `exclude`
(one pattern per line), `no-default-excludes`, `include-lang`, `dedupe`,
`commit` and `commit-message`. See [action.yml](action.yml) for their defaults.

## Data format

```json
{"generator": "counting-lines", "rev": "<sha>",
 "languages": ["C", "Python"],
 "points": [
  {"sha": "…", "date": "2024-01-01T00:00:00+00:00", "code": 120, "comment": 30, "blank": 15,
   "langs": {"C": [100, 25, 10], "Python": [20, 5, 5]}}
 ]}
```

`languages` is ordered by lines of code at the last commit. Each `langs` entry
is `[code, comment, blank]`.

## Comparison with tokei and cloc

Measured on an Apple M5 (10 cores) with git 2.55, tokei 15.0.0 and cloc 2.10.

### Whole history

Walking the first-parent chain. The checkout-per-commit columns are the
approach of loc-graph-action (`git checkout` + counting the tree for every
commit), estimated from a sample of sequential steps spread over the history
(20 for tokei, 6 for cloc) in a local clone.

| Repository | Commits | counting-lines, cold | Cold, `-j 1` | No new commits | One new commit | Checkout + tokei per commit | Checkout + cloc per commit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| e2fsprogs | 5,554 | 1.8 s | 6.2 s | 0.22 s | 0.24 s | ~7 min | ~37 min |
| klipper | 6,342 | 2.0 s | 6.5 s | 0.29 s | 0.31 s | ~22 min | ~3.1 h |
| BambuStudio | 10,554 | 7.7 s | 36 s | 0.68 s | 0.72 s | ~53 min | ~8.5 h |

A cold run counts each distinct blob once, spread over all CPU cores (`-j 1`
uses a single process); after that, only new commits are processed.

### A single tree

Counting the tree at `HEAD` once (best of three runs; cloc ran once).

| Repository | tokei | cloc | counting-lines | counting-lines, `-j 1` |
| --- | ---: | ---: | ---: | ---: |
| e2fsprogs | 0.02 s | 0.64 s | 0.18 s | 0.18 s |
| klipper | 0.29 s | 3.92 s | 1.22 s | 2.76 s |
| BambuStudio | 0.14 s | 4.42 s | 0.88 s | 1.58 s |

tokei (Rust, multi-threaded) is much faster on one tree. counting-lines is
Python; it wins over a history because it never recounts an unchanged file.
Trees with fewer than 1,000 files (e2fsprogs) are counted in a single process.

### Agreement with tokei

`HEAD` tree, no exclusions, compared file by file.

| Repository | tokei code lines | counting-lines code lines | Files counted by both | Same code/comment/blank split | Same total lines per file |
| --- | ---: | ---: | ---: | ---: | ---: |
| e2fsprogs | 278,209 | 283,931 | 552 | 93.1% | 100% |
| klipper | 2,395,685 | 2,411,643 | 2,163 | 93.8% | 100% |
| BambuStudio | 3,182,856 | 3,161,310 | 8,436 | 95.1% | 100% |

Where they differ:

- **Which files are counted.** tokei treats generic `*.in` templates as
  Autoconf and reads compiled `.mo` catalogs as Modelica source; counting-lines
  skips both, and counts some files tokei ignores (Perl `.t` tests, generated
  `configure` scripts).
- **Classification choices.** tokei counts Markdown prose and blank lines
  inside block comments as comments, and Python docstrings as code;
  counting-lines follows cloc (code, blank and comment respectively).
- **Parser edge cases.** tokei mistakes a `"` in a JavaScript regex literal
  or in a C character literal (`'"'`) for the start of a string, and does not
  close `/*/ … //*/` toggled blocks; counting-lines handles these.

## Limitations

- Only the first-parent chain is walked: a merged branch shows up as a single
  step at the merge commit.
- The comment classifier is heuristic, like cloc's: comment markers inside
  string literals are not recognised as such, and Python docstrings count as
  comments.
- Files without an extension are identified by their shebang (`#!/bin/sh`,
  `#!/usr/bin/env python3`...). Other files with unknown extensions, binaries,
  symlinks and submodules are not counted.
- `.fs` files are counted as GLSL shaders; F# is not supported.

## Tests

```bash
python3 -m unittest discover -s tests
```
