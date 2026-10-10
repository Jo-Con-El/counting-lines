"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import export, git, history, svg
from .progress import Progress


def default_cache_path(repo: str) -> str:
    common = git.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.join(common, "counting-lines", "cache.json.gz")


def positive_float(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError(f"must be a positive number: {text}")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="counting-lines",
        description="Lines of code over a repository's history, computed incrementally.",
    )
    p.add_argument("repo", nargs="?", default=".", help="repository path (default: .)")
    p.add_argument("-r", "--rev", default="HEAD",
                   help="last commit to process (default: HEAD); its first-parent chain is walked")
    p.add_argument("--cache", help="cache file (default: <git-dir>/counting-lines/cache.json.gz)")
    p.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    p.add_argument("-x", "--exclude", action="append", metavar="PATTERN",
                   help="exclude paths: a pattern without '/' matches any directory or file name, "
                        "one with '/' matches from the repo root; repeatable "
                        f"(default: {', '.join(history.DEFAULT_EXCLUDE)})")
    p.add_argument("--no-default-excludes", action="store_true",
                   help="do not apply the default exclusions")
    p.add_argument("--dedupe", action="store_true",
                   help="count identical files (same content and language) once per commit, like cloc")
    p.add_argument("-l", "--include-lang", metavar="LANGS",
                   help="comma-separated languages to report (e.g. 'Python,C'); default: all")

    out = p.add_argument_group("outputs")
    out.add_argument("--json", metavar="FILE", help="write the data as JSON")
    out.add_argument("--js", metavar="FILE", help="write the data as a JavaScript file")
    out.add_argument("--js-var", default="LOC_DATA", help="variable name for --js (default: LOC_DATA)")
    out.add_argument("--js-esm", action="store_true", help="emit an ES module instead of a global")
    out.add_argument("--svg", metavar="FILE", help="write an SVG chart")
    out.add_argument("--theme", choices=["auto", "light", "dark"], default="auto",
                     help="SVG colours; 'auto' follows prefers-color-scheme (default)")
    out.add_argument("--top", type=int, default=6, help="languages shown in the SVG before 'Other'")
    out.add_argument("--x-axis", choices=["time", "commits"], default="time",
                     help="SVG horizontal axis: commit time or commit index (default: time)")
    out.add_argument("--project", metavar="NAME",
                     help="project name for the SVG title and the data "
                          "(default: from the origin remote, else the directory; '' for none)")
    out.add_argument("--title", default="Lines of code over time", help="SVG title")
    out.add_argument("--date-format", help="strftime format for SVG date labels")
    out.add_argument("--style", choices=["areas", "lines", "total"], default="areas",
                     help="SVG: areas stacked by language (default), the same as lines, "
                          "or one line with the total")
    out.add_argument("--line-width", type=positive_float, default=2, metavar="PX",
                     help="SVG line thickness in pixels for --style lines/total (default: 2)")
    out.add_argument("--slopes", action="store_true",
                     help="join commits with straight lines instead of steps in the SVG")
    p.add_argument("--check", action="store_true",
                   help="recount the tree at --rev from scratch and compare with the history")
    p.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1,
                   help="processes counting new blobs in parallel (default: all CPUs)")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress messages")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    progress = None if args.quiet else Progress()
    log = progress.log if progress else (lambda _msg: None)

    exclude = ([] if args.no_default_excludes else list(history.DEFAULT_EXCLUDE)) + (args.exclude or [])
    include = [s.strip() for s in args.include_lang.split(",") if s.strip()] if args.include_lang else None

    try:
        target = git.rev_parse(args.repo, args.rev)
        cache_path = None if args.no_cache else (args.cache or default_cache_path(args.repo))
        cache = (history.Cache.load(cache_path, exclude, args.dedupe) if cache_path
                 else history.Cache(exclude, args.dedupe))

        started = time.monotonic()
        points = history.update(args.repo, target, cache, log=log, progress=progress,
                                jobs=max(1, args.jobs))
        if progress:
            progress.finish()
        log(f"done in {time.monotonic() - started:.2f}s, {len(points)} commit(s) in history")
        if cache_path:
            cache.save(cache_path)
        if args.check:
            expected = history.snapshot(args.repo, target, exclude, args.dedupe, jobs=max(1, args.jobs))
            actual = points[-1].langs if points else {}
            if expected != actual:
                print(f"counting-lines: check failed at {target[:12]}:\n"
                      f"  history: {actual}\n  recount: {expected}", file=sys.stderr)
                return 1
            log(f"check OK at {target[:12]}")
    except git.GitError as e:
        if progress:
            progress.finish()
        print(f"counting-lines: {e}", file=sys.stderr)
        return 2

    project = args.project
    if project is None:
        try:
            project = git.project_name(args.repo)
        except git.GitError:
            project = ""
    data = export.build_data(points, target, include, project)
    if args.json:
        export.write_json(data, args.json)
    if args.js:
        export.write_js(data, args.js, var=args.js_var, esm=args.js_esm)
    if args.svg:
        os.makedirs(os.path.dirname(os.path.abspath(args.svg)), exist_ok=True)
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write(svg.render(data, theme=args.theme, top=args.top, x_axis=args.x_axis,
                               title=args.title, date_format=args.date_format,
                               slopes=args.slopes, style=args.style,
                               line_width=args.line_width))
    if not (args.json or args.js or args.svg):
        print_summary(data)
    return 0


def print_summary(data: dict) -> None:
    if not data["points"]:
        print("no commits")
        return
    last = data["points"][-1]
    print(f"{last['sha'][:12]}  {last['date']}")
    print(f"{'language':<20} {'code':>10} {'comment':>10} {'blank':>10}")
    for lang in data["languages"]:
        c, cm, b = last["langs"].get(lang, [0, 0, 0])
        if c or cm or b:
            print(f"{lang:<20} {c:>10,} {cm:>10,} {b:>10,}")
    print(f"{'total':<20} {last['code']:>10,} {last['comment']:>10,} {last['blank']:>10,}")
