import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime

from counting_lines import cli, export, git, history, languages, svg
from counting_lines.progress import Progress


class Repo:
    """A throwaway git repository with deterministic commits."""

    def __init__(self, path: str):
        self.path = path
        self.tick = 0
        os.makedirs(path)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        self.tick += 1
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_AUTHOR_DATE": f"{1704067200 + self.tick * 3600} +0000",
            "GIT_COMMITTER_DATE": f"{1704067200 + self.tick * 3600} +0000",
        }
        return subprocess.run(["git", "-C", self.path, *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    def write(self, rel: str, content: str | bytes) -> None:
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(full, mode) as f:
            f.write(content)

    def commit(self, msg: str = "c") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")


class CountTest(unittest.TestCase):
    def check(self, lang, text, expected):
        self.assertEqual(languages.count(text.encode(), lang).as_list(), expected)

    def test_c(self):
        self.check("C", "int a; // x\n\n// c\n/* a\n b */\nint b; /* open\n still */ int c;\n", [3, 3, 1])

    def test_python_docstring(self):
        self.check("Python", '"""Doc.\n\nMore."""\nimport os  # x\n\n# c\n', [1, 3, 2])

    def test_comment_markers_inside_strings(self):
        # From e2fsprogs contrib/fsstress.c: "/*" inside a string opens nothing.
        self.check("C", 'sprintf(cmd, "rm -rf %s/*", d);\nsystem(cmd);\n', [2, 0, 0])
        self.check("C", "if (ch != '\"') {\n/* c */\n}\n", [2, 1, 0])
        self.check("C", 'puts("a\\"/*"); /* open\nstill */\n', [1, 1, 0])
        self.check("JavaScript", "x = `/*`;\ny();\n", [2, 0, 0])
        self.check("Python", "q = \"'''\"\nx = 1\n", [2, 0, 0])
        # An unterminated quote runs to the end of the line, hiding later markers.
        self.check("Rust", "fn f<'a>() {} /* c\n*/\n", [1, 1, 0])

    def test_toggle_comment_trick(self):
        # From BambuStudio CutSurface.cpp: "/*/" opens, "//*/" closes.
        self.check("C++", "/*/\nold();\n//*/\nnew_();\n", [1, 3, 0])

    def test_no_trailing_newline(self):
        self.check("Python", "a = 1\nb = 2", [2, 0, 0])

    def test_detect(self):
        self.assertEqual(languages.detect("src/Makefile"), "make")
        self.assertEqual(languages.detect("a/b.PY"), "Python")
        self.assertIsNone(languages.detect("README"))
        self.assertEqual(languages.detect("resources/shaders/gouraud.fs"), "GLSL")
        self.assertEqual(languages.detect("lib/Makefile.in"), "make")
        self.assertEqual(languages.detect("configure.ac"), "Autoconf")
        self.assertEqual(languages.detect("src/BUILD.bazel"), "Starlark")
        self.assertEqual(languages.detect("qhull/qhull.pro"), "qmake")

    def test_shebang(self):
        sb = languages.detect_shebang
        self.assertEqual(sb(b"#!/bin/sh\necho\n"), "Bourne Shell")
        self.assertEqual(sb(b"#!/usr/bin/env python3.12\n"), "Python")
        self.assertEqual(sb(b"#!/usr/bin/env -S LC_ALL=C perl -w\n"), "Perl")
        self.assertEqual(sb(b"#! /usr/bin/node\n"), "JavaScript")
        self.assertIsNone(sb(b"#!/usr/bin/env\n"))
        self.assertIsNone(sb(b"Copyright (c) 2024\n"))
        self.assertTrue(languages.may_have_shebang("tools/run"))
        self.assertTrue(languages.may_have_shebang(".bashrc"))
        self.assertFalse(languages.may_have_shebang("Makefile"))
        self.assertFalse(languages.may_have_shebang("notes.txt"))

    def test_m4_and_batch_comments(self):
        self.check("M4", "dnl c\nAC_INIT(x)\n# c\n", [1, 2, 0])
        self.check("Batch", "@echo off\nREM c\n:: c\n", [1, 2, 0])

    def test_exclude(self):
        ex = history.is_excluded
        self.assertTrue(ex("a/node_modules/x.js", ["node_modules"]))
        self.assertTrue(ex("x/package-lock.json", ["package-lock.json"]))
        self.assertTrue(ex("docs/api/x.md", ["docs/api"]))
        self.assertFalse(ex("src/docs/api/x.md", ["docs/api"]))
        self.assertTrue(ex("a/gen_x.c", ["gen_*"]))
        self.assertTrue(ex("po/es.po", history.DEFAULT_EXCLUDE))


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(os.path.join(self.tmp.name, "repo"))
        self.cache_path = os.path.join(self.tmp.name, "cache.json.gz")

    def tearDown(self):
        self.tmp.cleanup()

    def run_update(self, rev="HEAD", exclude=None, cache_path=None, dedupe=False):
        exclude = exclude if exclude is not None else []
        path = cache_path or self.cache_path
        cache = history.Cache.load(path, exclude, dedupe)
        target = git.rev_parse(self.repo.path, rev)
        points = history.update(self.repo.path, target, cache, log=lambda _: None)
        cache.save(path)
        return [p.to_json() for p in points]

    def assert_matches_snapshots(self, points, exclude=()):
        for p in points:
            self.assertEqual(p["langs"], history.snapshot(self.repo.path, p["sha"], list(exclude)), p["sha"])

    def build_history(self):
        r = self.repo
        r.write("a.py", "import os\n\n# hi\nprint(1)\n")
        r.write("lib/x.h", "int f(void);\n")
        r.write("tools/run", "#!/bin/sh\necho hi\necho there\n")
        r.write("LICENSE", "Permission is hereby granted\n")
        r.commit("one")
        r.git("checkout", "-q", "-b", "feature")
        r.write("lib/y.c", "int y;\n/* c */\n")
        r.commit("feature 1")
        r.write("lib/y.c", "int y;\nint z;\n")
        r.commit("feature 2")
        r.git("checkout", "-q", "main")
        r.write("a.py", "import os\nprint(2)\n")
        r.commit("main 2")
        r.git("merge", "-q", "--no-ff", "feature", "-m", "merge")
        r.git("mv", "lib/x.h", "lib/x.c")              # same blob, new language
        r.write("bin.c", b"\x00\x01binary\n")           # binary with a code extension
        os.symlink("a.py", os.path.join(r.path, "link.py"))
        r.write("node_modules/dep.js", "var a;\n")
        r.write("notes.txt", "not code\n")
        r.write("tools/run", "#!/usr/bin/env python3\nprint(1)\n")  # sh -> Python
        r.commit("rename and odd files")
        r.commit("empty")
        os.remove(os.path.join(r.path, "a.py"))
        r.write("sub/a.py", "import os\nprint(2)\n")    # moved file: same blob
        r.commit("move")

    def test_full_history_matches_snapshots(self):
        self.build_history()
        points = self.run_update()
        self.assertEqual(len(points), 6)  # first-parent chain only
        self.assert_matches_snapshots(points)
        self.assertEqual(points[0]["langs"]["Bourne Shell"], [2, 1, 0])
        self.assertEqual(points[-1]["langs"], {"C": [3, 0, 0], "JavaScript": [1, 0, 0], "Python": [3, 1, 0]})

    def test_excludes(self):
        self.build_history()
        points = self.run_update(exclude=["node_modules", "lib/*.c"])
        self.assert_matches_snapshots(points, ["node_modules", "lib/*.c"])
        self.assertEqual(points[-1]["langs"], {"Python": [3, 1, 0]})

    def test_resume_equals_cold_run(self):
        self.build_history()
        self.run_update("HEAD~3")
        resumed = self.run_update()
        cold = self.run_update(cache_path=os.path.join(self.tmp.name, "other.json.gz"))
        self.assertEqual(resumed, cold)

    def test_older_rev_is_a_prefix_and_keeps_cache(self):
        self.build_history()
        full = self.run_update()
        older = self.run_update("HEAD~2")
        self.assertEqual(older, full[:-2])
        self.assertEqual(history.Cache.load(self.cache_path, []).head, full[-1]["sha"])

    def test_rewritten_history_restarts(self):
        self.build_history()
        self.run_update()
        self.repo.git("reset", "-q", "--hard", "HEAD~2")
        self.repo.write("c.go", "package main\n")
        self.repo.commit("diverge")
        points = self.run_update()
        self.assertEqual(len(points), 5)
        self.assert_matches_snapshots(points)

    def test_dedupe_counts_identical_files_once(self):
        r = self.repo
        dump = "".join(f'{{"k": {i}}}\n' for i in range(100))
        r.write("scene.json", dump)
        r.write("backup/v1/scene.json", dump)
        r.write("backup/v2/scene.json", dump)
        r.write("a.py", "x = 1\n")
        r.write("b/a.py", "x = 1\n")
        r.commit("three copies")
        r.write("backup/v2/scene.json", dump + '{"k": 100}\n')
        r.commit("one copy diverges")
        r.git("rm", "-q", "-r", "backup/v1")
        r.commit("a duplicate goes away")
        r.git("rm", "-q", "scene.json")
        r.commit("the last copy of the original goes away")

        points = [p["langs"] for p in self.run_update(dedupe=True)]
        self.assertEqual([p["JSON"][0] for p in points], [100, 201, 201, 101])
        self.assertEqual(points[0]["Python"], [1, 0, 0])
        for p, sha in zip(points, self.repo.git("rev-list", "--reverse", "HEAD").split()):
            self.assertEqual(p, history.snapshot(self.repo.path, sha, [], dedupe=True))

        plain = [p["langs"]["JSON"][0] for p in self.run_update(dedupe=False)]
        self.assertEqual(plain, [300, 301, 201, 101])

    def test_parallel_counting_matches_sequential(self):
        self.build_history()
        sequential = self.run_update()
        old_min, old_chunk = history.PARALLEL_MIN_BLOBS, history.CHUNK_SIZE
        history.PARALLEL_MIN_BLOBS, history.CHUNK_SIZE = 0, 2
        self.addCleanup(setattr, history, "PARALLEL_MIN_BLOBS", old_min)
        self.addCleanup(setattr, history, "CHUNK_SIZE", old_chunk)
        cache = history.Cache([])
        target = git.rev_parse(self.repo.path, "HEAD")
        parallel = history.update(self.repo.path, target, cache, log=lambda _: None, jobs=2)
        self.assertEqual([p.to_json() for p in parallel], sequential)
        self.assertEqual(history.snapshot(self.repo.path, target, [], jobs=2),
                         history.snapshot(self.repo.path, target, []))

    def test_cache_head_missing_from_repository(self):
        self.build_history()
        self.run_update()
        other = Repo(os.path.join(self.tmp.name, "other"))
        other.write("main.go", "package main\n")
        other.commit()
        self.repo = other  # same cache file, unrelated repository
        points = self.run_update()
        self.assertEqual(len(points), 1)
        self.assert_matches_snapshots(points)

    def test_exclude_change_invalidates_history(self):
        self.build_history()
        self.run_update()
        points = self.run_update(exclude=["*.py", "tools"])
        self.assertNotIn("Python", points[-1]["langs"])
        self.assert_matches_snapshots(points, ["*.py", "tools"])


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(os.path.join(self.tmp.name, "repo"))
        self.repo.write("a.py", "x = 1\n")
        self.repo.commit()
        self.repo.write("b.c", "int b;\n")
        self.repo.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_outputs(self):
        out = lambda name: os.path.join(self.tmp.name, name)
        rc = cli.main([self.repo.path, "-q", "--cache", out("c.json.gz"), "--check",
                       "--json", out("d.json"), "--js", out("d.js"), "--svg", out("d.svg"),
                       "--include-lang", "Python"])
        self.assertEqual(rc, 0)
        with open(out("d.json")) as f:
            data = json.load(f)
        self.assertEqual(data["languages"], ["Python"])
        self.assertEqual([p["code"] for p in data["points"]], [1, 1])
        with open(out("d.js")) as f:
            self.assertTrue(f.read().startswith("globalThis.LOC_DATA = {"))
        with open(out("d.svg")) as f:
            self.assertIn("<svg", f.read())

    def test_time_ticks_are_round_dates_that_fit(self):
        def ticks(start, end, room=832):
            lo = datetime.fromisoformat(start).timestamp()
            hi = datetime.fromisoformat(end).timestamp()
            found, fmt = svg.time_ticks(lo, hi, room)
            return [t.strftime(fmt) for t in found]

        months = ticks("2025-11-21T18:33:24-06:00", "2026-10-09T12:00:00-05:00")
        self.assertEqual(months[0], "2025-12")
        self.assertEqual(len(months), 11)
        self.assertEqual(ticks("2014-08-23T00:00:00+00:00", "2026-08-21T00:00:00+00:00")[:3],
                         ["2015", "2016", "2017"])
        self.assertEqual(ticks("1997-04-26T00:00:00+00:00", "2026-01-01T00:00:00+00:00")[:2],
                         ["1998", "2000"])
        self.assertEqual(ticks("2025-11-21T06:00:00+00:00", "2025-11-21T18:00:00+00:00")[:2],
                         ["06:00", "07:00"])
        # Less room, fewer labels.
        self.assertLess(len(ticks("2025-11-21T00:00:00+00:00", "2026-10-09T00:00:00+00:00", 300)), 11)

    def test_svg_steps_or_slopes(self):
        data = export.build_data([
            history.Point("a" * 40, "2024-01-01T00:00:00+00:00", {"C": [10, 0, 0]}),
            history.Point("b" * 40, "2024-03-01T00:00:00+00:00", {"C": [30, 0, 0]}),
        ], "x")
        path = lambda doc: doc.split('<path class="s0" d="M', 1)[1].split('"', 1)[0]
        steps = path(svg.render(data)).split(" Z")[0].split(" L")
        slopes = path(svg.render(data, slopes=True)).split(" Z")[0].split(" L")
        # Upper edge then lower edge: a step adds a corner to each.
        self.assertEqual(len(steps), 6)
        self.assertEqual(len(slopes), 4)

    def test_svg_total_style(self):
        data = export.build_data([
            history.Point("a" * 40, "2024-01-01T00:00:00+00:00", {"C": [10, 0, 0], "Go": [5, 0, 0]}),
            history.Point("b" * 40, "2024-03-01T00:00:00+00:00", {"C": [30, 0, 0], "Go": [5, 0, 0]}),
        ], "x")
        doc = svg.render(data, style="total")
        self.assertEqual(doc.count("<path "), 1)
        self.assertIn('class="ln-total"', doc)
        self.assertNotIn("<rect class=\"s", doc)  # no legend
        self.assertIn("35 lines", doc)
        one = export.build_data([history.Point("a" * 40, "2024-01-01T00:00:00+00:00", {"C": [1, 0, 0]})], "x")
        self.assertIn('class="ln-total"', svg.render(one, style="total"))

    def test_svg_handles_edge_cases(self):
        empty = {"languages": [], "points": []}
        self.assertIn("No data", svg.render(empty))
        one = export.build_data([history.Point("a" * 40, "2024-01-01T00:00:00+00:00", {"C": [5, 0, 0]})], "x")
        self.assertIn("<path", svg.render(one))


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class ProgressTest(unittest.TestCase):
    def test_redraws_in_place_and_keeps_finished_phases(self):
        out = FakeTTY()
        p = Progress(out, interval=0)
        p("reading history", 1)
        p("reading history", 2, 2)
        p.log("2 new commits")
        p("counting blobs", 1, 3)
        p.log("note")  # mid-phase: the unfinished line is cleared, not kept
        p("counting blobs", 3, 3)
        p.finish()
        self.assertEqual(out.getvalue(), (
            "\r\x1b[Kreading history: 1"
            "\r\x1b[Kreading history: 2/2 (100%)\n"
            "2 new commits\n"
            "\r\x1b[Kcounting blobs: 1/3 (33%)"
            "\r\x1b[Knote\n"
            "\r\x1b[Kcounting blobs: 3/3 (100%)\n"
        ))

    def test_silent_when_not_a_terminal(self):
        out = io.StringIO()
        p = Progress(out, interval=0)
        p("counting blobs", 1, 2)
        p.finish()
        self.assertEqual(out.getvalue(), "")

    def test_update_reports_every_phase(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(os.path.join(tmp.name, "repo"))
        repo.write("a.py", "x = 1\n")
        repo.commit()
        repo.write("b.py", "y = 2\n")
        repo.commit()
        calls = []
        history.update(repo.path, git.rev_parse(repo.path, "HEAD"), history.Cache([]),
                       log=lambda _: None, progress=lambda *a: calls.append(a))
        self.assertEqual(calls[-3:], [("counting blobs", 2, 2), ("replaying commits", 1, 2),
                                      ("replaying commits", 2, 2)])
        self.assertIn(("reading history", 2, 2), calls)


if __name__ == "__main__":
    unittest.main()
