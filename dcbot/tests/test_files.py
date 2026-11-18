"""The file browser: reading (filecore), what a folder or file looks like, links to mentioned files."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import filecore, files
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Core(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name, text in (("a.py", "print(1)\n" * 100), ("notes.md", "# hi\n"), ("sub/deep/thing.txt", "x"),
                           (".hidden", "h"), ("img.png", "")):
            path = os.path.join(self.dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        with open(os.path.join(self.dir, "blob.bin"), "wb") as f:
            f.write(b"\0\1\2")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_list_puts_folders_first_and_hides_dotfiles(self):
        r = filecore.op({"op": "list", "path": self.dir})
        self.assertEqual([e["n"] for e in r["entries"]][:2], ["sub", "a.py"])
        self.assertNotIn(".hidden", [e["n"] for e in r["entries"]])
        self.assertEqual(r["hidden"], 1)
        self.assertIn(".hidden", [e["n"] for e in filecore.op({"op": "list", "path": self.dir, "hidden": True})["entries"]])

    def test_read_pages_and_kinds(self):
        r = filecore.op({"op": "read", "path": os.path.join(self.dir, "a.py"), "start": 95, "count": 10})
        self.assertEqual((r["start"], r["total"], len(r["lines"])), (95, 100, 6))
        past = filecore.op({"op": "read", "path": os.path.join(self.dir, "a.py"), "start": 500, "count": 10})
        self.assertEqual(past["start"], 91, "past the end shows the last lines")
        self.assertEqual(filecore.op({"op": "read", "path": os.path.join(self.dir, "blob.bin")})["kind"], "binary")
        self.assertEqual(filecore.op({"op": "read", "path": os.path.join(self.dir, "img.png")})["kind"], "image")
        self.assertTrue(filecore.op({"op": "read", "path": os.path.join(self.dir, "sub")}).get("dir"))
        raw = filecore.op({"op": "raw", "path": os.path.join(self.dir, "notes.md")})
        self.assertEqual(base64.b64decode(raw["b64"]), b"# hi\n")

    def test_find_matches_words_and_folders(self):
        r = filecore.op({"op": "find", "root": self.dir, "query": "thing"})
        self.assertEqual(r["matches"][0], "sub/deep/thing.txt")
        self.assertIn("sub/deep/", filecore.op({"op": "find", "root": self.dir, "query": "deep"})["matches"])
        self.assertEqual(filecore.op({"op": "find", "root": self.dir, "query": "sb dp thg"})["matches"][0], "sub/deep/thing.txt")
        self.assertEqual(filecore.op({"op": "find", "root": self.dir, "query": "zzz"})["matches"], [])

    def test_secrets_are_never_shown(self):
        with mock.patch.object(filecore, "home", return_value=self.dir):
            os.makedirs(os.path.join(self.dir, ".config", "dcbot"))
            open(os.path.join(self.dir, ".config", "dcbot", "env"), "w").close()
            open(os.path.join(self.dir, ".env"), "w").close()
            open(os.path.join(self.dir, "server.pem"), "w").close()
            os.symlink(os.path.join(self.dir, ".config", "dcbot", "env"), os.path.join(self.dir, "innocent"))
            self.assertIn("error", filecore.op({"op": "list", "path": os.path.join(self.dir, ".config")}))
            self.assertIn("error", filecore.op({"op": "read", "path": os.path.join(self.dir, ".config/dcbot/env")}))
            self.assertIn("error", filecore.op({"op": "read", "path": os.path.join(self.dir, "innocent")}), "a link to a secret")
            self.assertIn("error", filecore.op({"op": "raw", "path": os.path.join(self.dir, "server.pem")}))
            names = [e["n"] for e in filecore.op({"op": "list", "path": self.dir, "hidden": True})["entries"]]
            for n in (".config", ".env", "server.pem", "innocent"):
                self.assertNotIn(n, names)
            self.assertEqual(filecore.op({"op": "resolve", "cwd": self.dir, "paths": [".env", "a.py"]})["found"][0][0], "a.py")
            self.assertNotIn(".env", filecore.op({"op": "find", "root": self.dir, "query": "env", "hidden": True})["matches"])


class Render(unittest.TestCase):
    def test_folder_page_and_controls(self):
        entries = [{"n": "d%02d" % i, "d": True, "s": 0, "c": 1} for i in range(30)] + [{"n": "f.py", "d": False, "s": 10}]
        data = {"path": "/h/proj", "home": "/h", "entries": entries, "git": {"branch": "main", "dirty": 2, "marks": {"f.py": "M"}}}
        b = {"mode": "temp", "root": "/h/proj", "page": 1}
        content, comps = files.render_dir(data, b)
        self.assertIn("~/proj", content)
        self.assertIn("第 2/2 頁", content)
        self.assertIn("f.py", content)
        actions = dict((c[1], c) for c in comps)
        self.assertEqual(len(actions["open"][3]), 6)
        self.assertTrue(actions["next"][3], "last page: ▶ disabled")
        self.assertTrue(actions["home"][3], "at the root already")
        self.assertIn("close", actions)
        self.assertLess(len(content), 2000)
        pick, comps = files.render_dir(data, {"mode": "pick", "root": "/", "page": 1})
        self.assertNotIn("f.py", pick, "the picker shows folders only")
        self.assertIn("use", [c[1] for c in comps])
        files.view(comps)  # Discord accepts it

    def test_file_page_fits_and_numbers(self):
        data = {"path": "/h/a.py", "home": "/h", "size": 99, "kind": "text", "start": 1, "total": 500,
                "lines": ["x = '%s'" % ("y" * 300)] * 60}
        b = {"mode": "temp"}
        content, comps = files.render_file(data, b)
        self.assertLessEqual(len(content), 2000)
        self.assertIn("```py", content)
        self.assertEqual(b["line"], 1)
        self.assertTrue(b["last"] < 60, "long lines: fewer of them")
        self.assertTrue(dict((c[1], c) for c in comps)["prev"][3])
        back = {"mode": "temp"}
        files.render_file(dict(data, start=41, lines=["l"] * 59), back, from_end=True)
        self.assertEqual(back["last"], 99)
        md, comps = files.render_file({"path": "/h/r.md", "home": "/h", "size": 5, "kind": "text", "start": 1, "total": 1,
                                       "lines": ["# t", "```", "x"]}, {"md": False})
        self.assertNotIn("```\n```", md)
        self.assertIn("md", [c[1] for c in comps])
        files.view(comps)

    def test_mentioned_paths(self):
        got = files.mentioned("改了 `bot/setup.py:142` 和 dcbot/CLAUDE.md，見 https://x.io/a.py，版本 2.1.289")
        self.assertEqual(got[:2], [("bot/setup.py", 142), ("dcbot/CLAUDE.md", None)])
        self.assertNotIn(("a.py", None), got)
        self.assertEqual(files.fit("長長的名字abc", 6), "長長… ")


class FakeMsg(object):
    def __init__(self, mid):
        self.id, self.view = mid, None

    async def edit(self, view=None):
        self.view = view


class FakeChannel(object):
    id, name = 7, "files"

    def __init__(self):
        self.sent = []

    async def send(self, content, view=None, file=None):
        self.sent.append((content, view, file))
        return FakeMsg(100 + len(self.sent))


class FakeBot(object):
    def __init__(self):
        self.channels_state = {}

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"cwd": os.path.dirname(os.path.dirname(os.path.abspath(__file__)))})

    def save(self):
        pass


class Browser(unittest.TestCase):
    def test_open_at_a_file_line_and_links(self):
        async def go():
            bot, ch = FakeBot(), FakeChannel()
            f = files.Files(bot)
            msg = await f.open(ch, None, bot.chan(ch)["cwd"], target="bot/files.py:100")
            b = bot.chan(ch)["browsers"][str(msg.id)]
            self.assertEqual((b["view"], b["line"]), ("file", 95))
            self.assertIn("files.py", ch.sent[-1][0])
            reply = FakeMsg(5)
            await f.add_links(ch, bot.chan(ch), reply, "看 `bot/files.py:10` 跟 nothing/here.py")
            self.assertEqual(len(reply.view.children), 1)
            self.assertEqual(bot.chan(ch)["links"]["5"][0][1], 10)
            from bot import ledger  # (both reviews on 99392598: the 📂 edit keeps the reply's status line on record)
            ledger.note(ch.id, 6, "reply", framed=("status",))
            await f.add_links(ch, bot.chan(ch), FakeMsg(6), "看 `bot/files.py:10`")
            self.assertEqual(ledger.entry(6, ch.id)["framed"], ["status"])
        asyncio.run(go())

    def test_files_channel_typing_searches_then_jumps(self):
        async def go():
            bot, ch = FakeBot(), FakeChannel()
            st = bot.chan(ch)
            st.update({"kind": "files", "files": {"host": None, "root": st["cwd"]}})
            f = files.Files(bot)
            await f.start_channel(ch)
            await f.typed(ch, "filecore")
            b = st["browsers"][str(st["files"]["browser"])]
            self.assertEqual(b["view"], "find")
            self.assertTrue(b["matches"][0].endswith("bot/filecore.py"))
            await f.typed(ch, "bot/main.py:20")
            b = st["browsers"][str(st["files"]["browser"])]
            self.assertEqual((b["view"], b["line"]), ("file", 15))
            self.assertEqual(len(st["browsers"]), 1, "the old browser is replaced")
        asyncio.run(go())


def refused(r):
    """A result that carries nothing of the file (its assertions never print what came back: it may be a token)."""
    return isinstance(r, dict) and "error" in r and not any(k in r for k in ("b64", "lines", "entries", "matches"))


def soon(fn, *args, limit=5):
    """fn(*args) in a thread, failing the test when it is not back within `limit` seconds (a FIFO read that hangs)."""
    import threading
    out = []

    def run():
        try:
            out.append((True, fn(*args)))
        except BaseException as e:
            out.append((False, e))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(limit)
    if t.is_alive():
        raise AssertionError("did not come back within %s s" % limit)
    ok, got = out[0]
    if not ok:
        raise got
    return got


class SystemTrees(unittest.TestCase):
    """A review on 2026-10-21: /proc/self/environ was read and sent whole (filecore.raw: 1692 characters of
    base64, its size said 0): the environment of every process of this user, tokens among them, is there. /proc, /sys
    and /dev are no place the browser shows, whatever way leads there; and only regular files are read."""
    LINUX = os.path.exists("/proc/self/environ")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="files trees ")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.fifo = os.path.join(self.dir, "pipe")
        os.mkfifo(self.fifo)
        with open(os.path.join(self.dir, "plain.txt"), "w") as f:
            f.write("fine\n")
        for name, to in (("env-link", "/proc/self/environ"), ("proc-link", "/proc"), ("zero-link", "/dev/zero"),
                         ("sys-link", "/sys")):
            os.symlink(to, os.path.join(self.dir, name))

    def secret_paths(self):
        paths = ["/dev/zero", "/dev/null", "/dev", self.fifo, os.path.join(self.dir, "zero-link")]
        if self.LINUX:
            paths += ["/proc/self/environ", "/proc/%d/environ" % os.getpid(), "/proc/self/cmdline", "/proc/self/maps",
                      "/proc", "/proc/self", os.path.join(self.dir, "env-link"), os.path.join(self.dir, "proc-link"),
                      os.path.join(self.dir, "sub", "..", "env-link")]
        if os.path.isdir("/sys/kernel"):
            paths += ["/sys", "/sys/kernel", os.path.join(self.dir, "sys-link")]
        return paths

    def test_every_operation_refuses_them(self):
        for path in self.secret_paths():
            for req in ({"op": "read", "path": path}, {"op": "raw", "path": path}, {"op": "list", "path": path},
                        {"op": "find", "root": path, "query": "e"}):
                self.assertTrue(refused(soon(filecore.op, req)), "%s %s" % (req["op"], path))
            self.assertEqual(filecore.op({"op": "resolve", "cwd": self.dir, "paths": [path]})["found"],
                             [] if path not in (self.fifo,) else filecore.op({"op": "resolve", "cwd": self.dir, "paths": [path]})["found"],
                             path)
        self.assertTrue(refused(filecore.op({"op": "raw", "path": "/proc/self/environ"})) or not self.LINUX,
                        "its size is 0: not taken for an empty file")

    def test_the_folder_listing_leaves_the_links_out(self):
        names = [e["n"] for e in filecore.op({"op": "list", "path": self.dir, "hidden": True})["entries"]]
        for n in ("env-link", "proc-link", "zero-link", "sys-link"):
            self.assertNotIn(n, names)
        self.assertIn("plain.txt", names)
        self.assertEqual(base64.b64decode(filecore.op({"op": "raw", "path": os.path.join(self.dir, "plain.txt")})["b64"]), b"fine\n")

    def test_only_regular_files_are_opened(self):
        """A FIFO or a device swapped in after the checks: open_regular() refuses it without waiting for a writer."""
        for path in (self.fifo, "/dev/zero"):
            with self.assertRaises(filecore.NotRegular, msg=path):
                soon(filecore.open_regular, path)
        with soon(filecore.open_regular, os.path.join(self.dir, "plain.txt")) as f:
            self.assertEqual(f.read(), b"fine\n")
        with mock.patch.object(filecore.os.path, "isfile", lambda p: True), mock.patch.object(filecore.os.path, "getsize", lambda p: 1):
            self.assertTrue(refused(soon(filecore.raw, self.fifo)), "the check before the open passed: the open's own")
            self.assertTrue(refused(soon(filecore.raw, "/dev/zero")))
            self.assertNotIn("lines", soon(filecore.read, self.fifo))

    @unittest.skipUnless(os.path.exists("/proc/self/environ"), "Linux")
    def test_a_folder_swapped_for_a_link_after_the_check(self):
        """The open file's own path is checked again (/proc/self/fd): blocked() said yes to a path whose folder is a
        link to /proc by the time it is opened."""
        real = os.path.join(self.dir, "d")
        os.makedirs(real)
        with open(os.path.join(real, "environ"), "w") as f:
            f.write("x")
        swapped = os.path.join(self.dir, "swapped")
        os.symlink("/proc/self", swapped)
        with mock.patch.object(filecore, "full", lambda p, cwd=None: os.path.join(swapped, "environ")), \
                mock.patch.object(filecore, "blocked", side_effect=[False, True]):
            self.assertTrue(refused(filecore.raw("whatever")))

    def test_another_machine_goes_the_same_way(self):
        """dcremote files is filecore.op itself (tools/dcremote imports bot.filecore): the same answers."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json
        for path in self.secret_paths():
            for op in ("raw", "read", "list"):
                r = subprocess.run(["python3", os.path.join(root, "tools", "dcremote"), "files"],
                                   input=json.dumps({"op": op, "path": path}), stdout=subprocess.PIPE, universal_newlines=True, timeout=20)
                self.assertTrue(refused(json.loads(r.stdout)), "%s %s" % (op, path))


class Parts(unittest.TestCase):
    """C-90: raw in parts, for a plugin reading a large file of another machine. Every part is checked as a whole read
    is; a file replaced or changed between parts is refused, never read half old, half new; every result says the
    interface's version ("v")."""

    LINUX = SystemTrees.LINUX  # (SystemTrees' setting up and paths; not a subclass, which would run its tests again)
    setUp, tearDown, secret_paths = SystemTrees.setUp, SystemTrees.tearDown, SystemTrees.secret_paths

    def big(self, name="big.bin", n=3 * filecore.PART_MAX + 123):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(bytes(i % 251 for i in range(n)))
        return path

    def part(self, path, offset, length, same=None):
        return filecore.op(dict({"op": "raw", "path": path, "offset": offset, "length": length}, **({"same": same} if same else {})))

    def test_a_large_file_read_whole_in_parts(self):
        path = self.big()
        got, offset, same = b"", 0, None
        while True:
            r = self.part(path, offset, filecore.PART_MAX * 4, same)  # (asks for more: gets a part's worth)
            self.assertNotIn("error", r)
            self.assertLessEqual(r["length"], filecore.PART_MAX)
            self.assertEqual((r["v"], r["offset"], r["size"]), (filecore.V, offset, os.path.getsize(path)))
            got += base64.b64decode(r["b64"])
            offset, same = offset + r["length"], {k: r[k] for k in ("id", "mtime", "size")}
            if r["end"]:
                break
        with open(path, "rb") as f:
            self.assertEqual(got, f.read())

    def test_a_file_replaced_or_changed_between_parts(self):
        path = self.big()
        first = self.part(path, 0, 10)
        same = {k: first[k] for k in ("id", "mtime", "size")}
        with open(path, "ab") as f:  # changed: grown
            f.write(b"more")
        r = self.part(path, 10, 10, same)
        self.assertTrue(r["changed"])
        self.assertIn("error", r)
        self.assertNotIn("b64", r, "nothing of the new one is read")
        other = self.big("other.bin", 100)
        first = self.part(other, 0, 10)
        same = {k: first[k] for k in ("id", "mtime", "size")}
        os.replace(self.big("new.bin", 100), other)  # replaced: same size, another file
        self.assertTrue(self.part(other, 10, 10, same)["changed"])

    def test_every_part_refuses_what_a_whole_read_refuses(self):
        for path in self.secret_paths():
            r = filecore.op({"op": "raw", "path": path, "offset": 0, "length": 10})
            self.assertTrue(refused(r), path)
            self.assertEqual(r["v"], filecore.V)
        token = os.path.join(self.dir, "api_token.json")
        with open(token, "w") as f:
            f.write("x")
        self.assertTrue(refused(self.part(token, 0, 1)))

    def test_a_link_swapped_to_a_secret_between_parts(self):
        """Each part is checked again: a link to an ordinary file swapped for one to /proc after the first part."""
        if not self.LINUX:
            self.skipTest("Linux")
        path = self.big("plain.bin", 100)
        link = os.path.join(self.dir, "link.bin")
        os.symlink(path, link)
        first = self.part(link, 0, 10)
        self.assertNotIn("error", first)
        os.remove(link)
        os.symlink("/proc/self/environ", link)
        self.assertTrue(refused(self.part(link, 10, 10, {k: first[k] for k in ("id", "mtime", "size")})))

    def test_what_it_is_given(self):
        path = self.big("small.bin", 10)
        for offset, length in (("x", 1), (0, "y"), (-1, 1), (0, -1), (None, 1)):
            r = filecore.op({"op": "raw", "path": path, "offset": offset, "length": length})
            if offset is None:
                self.assertNotIn("error", r, "no offset: from the start")
                continue
            self.assertIn("error", r, (offset, length))
        r = self.part(path, 50, 10)
        self.assertEqual((r["length"], r["end"], r["b64"]), (0, True, ""), "past the end: nothing, and the end")
        whole = filecore.op({"op": "raw", "path": path})
        self.assertEqual((whole["size"], whole["v"]), (10, filecore.V), "without offset and length: the whole file, as before")

    def test_over_dcremote(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json
        path = self.big("r.bin", 1000)
        r = subprocess.run(["python3", os.path.join(root, "tools", "dcremote"), "files"],
                           input=json.dumps({"op": "raw", "path": path, "offset": 100, "length": 50}), stdout=subprocess.PIPE,
                           universal_newlines=True, timeout=20)
        got = json.loads(r.stdout)
        with open(path, "rb") as f:
            f.seek(100)
            self.assertEqual(base64.b64decode(got["b64"]), f.read(50))
        self.assertEqual(got["v"], filecore.V)


class Remote(unittest.TestCase):
    def test_dcremote_files(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(["python3", os.path.join(root, "tools", "dcremote"), "files"], input='{"op": "list", "path": "%s"}' % root,
                           stdout=subprocess.PIPE, universal_newlines=True)
        self.assertIn('"bot"', r.stdout)


if __name__ == "__main__":
    unittest.main()
