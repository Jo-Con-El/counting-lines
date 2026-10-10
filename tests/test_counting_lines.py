import json
import os
import subprocess
import tempfile
import unittest

from counting_lines import cli, export, git, history, languages, svg


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

    def run_update(self, rev="HEAD", exclude=None, cache_path=None):
        exclude = exclude if exclude is not None else []
        path = cache_path or self.cache_path
        cache = history.Cache.load(path, exclude)
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
        self.assertEqual(points[-1]["langs"], {"C": [3, 0, 0], "JavaScript": [1, 0, 0], "Python": [2, 0, 0]})

    def test_excludes(self):
        self.build_history()
        points = self.run_update(exclude=["node_modules", "lib/*.c"])
        self.assert_matches_snapshots(points, ["node_modules", "lib/*.c"])
        self.assertEqual(points[-1]["langs"], {"Python": [2, 0, 0]})

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

    def test_exclude_change_invalidates_history(self):
        self.build_history()
        self.run_update()
        points = self.run_update(exclude=["*.py"])
        self.assertNotIn("Python", points[-1]["langs"])
        self.assert_matches_snapshots(points, ["*.py"])


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

    def test_svg_handles_edge_cases(self):
        empty = {"languages": [], "points": []}
        self.assertIn("No data", svg.render(empty))
        one = export.build_data([history.Point("a" * 40, "2024-01-01T00:00:00+00:00", {"C": [5, 0, 0]})], "x")
        self.assertIn("<path", svg.render(one))


if __name__ == "__main__":
    unittest.main()
