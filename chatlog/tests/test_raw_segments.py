"""A raw file past SEG_MAX is kept in segments (manager 2026-10-07): full ones gzipped and never changed again, an
open tail, meta.json listing them with lines and sha256 and being the one thing that says which files are the record.
Readers join them back exactly and say when one is missing or wrong; a source shorter than what is kept leaves every
segment alone; a run stopped anywhere leaves the record whole; only chatlog's own file names are ever removed."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "..", "chatlog.py")
ROOT = tempfile.mkdtemp(prefix="chatlog-seg ")
os.environ.update(CHATLOG_REPO=os.path.join(ROOT, "logs"), CHATLOG_STATE=os.path.join(ROOT, "state"),
                  CHATLOG_HOST="host-a", CLAUDE_CONFIG_DIR=os.path.join(ROOT, "claude"))
spec = importlib.util.spec_from_file_location("chatlog_seg", PATH)
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)
SID = "5e9a0c1d-0000-4000-8000-000000000001"
SEG = 4000  # bytes: SEG_MAX for these tests
WHOLE = 10 ** 9  # a SEG_MAX nothing here reaches: a whole raw.jsonl is written, as for a small conversation


def rec(i, extra=""):
    if i % 2 == 0:
        return {"type": "user", "timestamp": "2026-10-01T00:%02d:%02dZ" % (i // 60 % 60, i % 60), "sessionId": SID,
                "cwd": "/w/proj", "uuid": "u%d" % i, "message": {"role": "user", "content": "問題 %d %s %s" % (i, "x" * 150, extra)}}
    return {"type": "assistant", "timestamp": "2026-10-01T00:%02d:%02dZ" % (i // 60 % 60, i % 60), "sessionId": SID,
            "cwd": "/w/proj", "uuid": "a%d" % i, "message": {"role": "assistant", "model": "claude-x",
                                                            "content": [{"type": "text", "text": "答 %d %s" % (i, "y" * 150)}]}}


class Rig(unittest.TestCase):
    def setUp(self):
        self.fresh()
        self.patches = [mock.patch.object(cl, "SEG_MAX", SEG)]
        for p in self.patches:
            p.start()

    def fresh(self):
        """An empty logs repo, source folder and state."""
        shutil.rmtree(ROOT, ignore_errors=True)
        os.makedirs(cl.REPO)
        subprocess.run(["git", "init", "-q", cl.REPO], check=True)
        self.src = os.path.join(cl.CLAUDE_DIR, "projects", "-w-proj", SID + ".jsonl")
        os.makedirs(os.path.dirname(self.src))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def source(self, n, extra=""):
        with open(self.src, "w", encoding="utf-8") as f:
            for i in range(n):
                f.write(json.dumps(rec(i, extra), ensure_ascii=False) + "\n")

    def export(self):
        return cl.export_session(self.src)

    def whole(self, n, extra=""):
        """What the whole raw.jsonl would be (write_raw, as before segments)."""
        d = tempfile.mkdtemp(dir=ROOT)
        cl.write_raw(os.path.join(d, "raw.jsonl"), [rec(i, extra) for i in range(n)])
        with open(os.path.join(d, "raw.jsonl"), "rb") as f:
            return f.read()

    def meta(self, d):
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            return json.load(f)

    def files(self, d):
        return sorted(n for n in os.listdir(d) if n.startswith("raw"))

    def snapshot(self, d):
        out = {}
        for top, _, names in os.walk(d):
            for n in names:
                with open(os.path.join(top, n), "rb") as f:
                    out[os.path.relpath(os.path.join(top, n), d)] = f.read()
        return out


class Segments(Rig):
    def test_small_is_as_before(self):
        self.source(6)
        d = self.export()
        self.assertEqual(self.files(d), ["raw.jsonl"])
        self.assertNotIn("raw", self.meta(d))
        self.assertEqual(cl.read_raw(d), (self.whole(6), []))

    def test_a_reader_that_goes_away_is_nothing_to_say(self):
        """`chatlog.py raw … | head` printed a BrokenPipeError traceback (manager 2026-10-08)."""
        self.source(600)  # more than a pipe holds
        d = self.export()
        p = subprocess.Popen([sys.executable, PATH, "raw", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.stdout.read(10)
        p.stdout.close()
        err = p.stderr.read().decode()
        p.stderr.close()
        self.assertEqual((p.wait(), err), (0, ""))

    def test_big_in_segments_joined_back_byte_for_byte(self):
        self.source(80)
        d = self.export()
        info = self.meta(d)["raw"]
        names = [x["file"] for x in info["segments"]]
        self.assertGreaterEqual(len(names), 3)
        self.assertTrue(all(n.endswith(".jsonl.gz") for n in names[:-1]) and names[-1].endswith(".jsonl"), names)
        self.assertEqual(self.files(d), sorted(names), "the whole raw.jsonl is gone")
        self.assertEqual(info["lines"], 80)
        self.assertTrue(all(x["bytes"] <= SEG for x in info["segments"]))
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))
        self.assertEqual(len(cl.load_raw(d)[0]), 80)
        with open(os.path.join(d, "README.md"), encoding="utf-8") as f:
            self.assertIn("分 %d 段" % len(names), f.read())
        r = subprocess.run([sys.executable, PATH, "raw", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout), (0, self.whole(80)), r.stderr)

    def test_growing_full_segments_are_not_written_again(self):
        self.source(80)
        d = self.export()
        first = self.meta(d)["raw"]["segments"]
        sealed = {x["file"]: os.stat(os.path.join(d, x["file"])).st_ino for x in first[:-1]}
        self.source(200)
        self.export()
        now = self.meta(d)["raw"]["segments"]
        self.assertEqual(now[:len(first) - 1], first[:-1], "the same boundaries, the same content")
        self.assertEqual({f: os.stat(os.path.join(d, f)).st_ino for f in sealed}, sealed, "not rewritten")
        self.assertGreater(len(now), len(first))
        self.assertEqual(self.files(d), sorted(x["file"] for x in now), "the old tail went when it was sealed")
        self.assertEqual(cl.read_raw(d), (self.whole(200), []))

    def test_gzip_is_the_same_bytes_every_time(self):
        self.assertEqual(cl._gzip(b"abc\n" * 1000), cl._gzip(b"abc\n" * 1000))

    def test_a_shorter_source_touches_nothing(self):
        self.source(120)
        d = self.export()
        before = self.snapshot(d)
        self.source(90)
        self.assertIsNone(self.export())
        self.assertEqual(self.snapshot(d), before, "no segment rewritten, cut or removed; README as it was")
        with open(os.path.join(cl.STATE_DIR, "chatlog.log"), encoding="utf-8") as f:
            self.assertIn("比已存的 120 行少", f.read())

    def test_a_changed_redaction_rewrites_a_full_segment_the_source_covers(self):
        self.source(80)
        d = self.export()
        first = self.meta(d)["raw"]["segments"][0]
        with mock.patch.object(cl, "redact", lambda t: t.replace("問題 0 ", "問題 * ")):
            self.export()
        self.assertNotEqual(self.meta(d)["raw"]["segments"][0]["sha256"], first["sha256"])
        self.assertEqual(cl.read_raw(d)[1], [])

    def test_only_its_own_names_are_removed(self):
        self.source(80)
        d = self.export()
        for n in ("notes.txt", "raw-backup.jsonl", "raw-12.jsonl", "raw.jsonl.gz", "raw-0099.txt"):
            with open(os.path.join(d, n), "w") as f:
                f.write("mine\n")
        with open(os.path.join(d, "raw-0099.jsonl.gz"), "wb") as f:
            f.write(b"stale")
        self.source(82)
        self.export()
        left = set(os.listdir(d))
        self.assertTrue({"notes.txt", "raw-backup.jsonl", "raw-12.jsonl", "raw.jsonl.gz", "raw-0099.txt"} <= left)
        self.assertNotIn("raw-0099.jsonl.gz", left, "a segment name not listed: removed")

    def test_subagents_too(self):
        self.source(4)
        sub = os.path.join(os.path.dirname(self.src), SID, "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-1.jsonl"), "w", encoding="utf-8") as f:
            for i in range(80):
                f.write(json.dumps(rec(i), ensure_ascii=False) + "\n")
        d = self.export()
        info = self.meta(d)["subagents_raw"]["agent-1"]
        self.assertTrue(info["segments"][0]["file"].startswith("subagents/agent-1-0001"))
        self.assertNotIn("agent-1.jsonl", os.listdir(os.path.join(d, "subagents")))
        self.assertEqual(cl.read_raw(d, "subagents/agent-1")[1], [])
        self.assertEqual(len(cl.load_raw(d, "subagents/agent-1")[0]), 80)

    def test_a_subagent_whose_source_is_gone_stays_listed(self):
        """#code-review 2026-10-08: the main conversation goes on, one subagent's file is gone from the source."""
        self.source(60)
        sub = os.path.join(os.path.dirname(self.src), SID, "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-abc123.jsonl"), "w", encoding="utf-8") as f:
            for i in range(80):
                f.write(json.dumps(rec(i), ensure_ascii=False) + "\n")
        d = self.export()
        before = self.meta(d)["subagents_raw"]["agent-abc123"]
        files = sorted(os.listdir(os.path.join(d, "subagents")))
        os.remove(os.path.join(sub, "agent-abc123.jsonl"))
        self.source(70)
        self.export()
        self.assertEqual(self.meta(d)["subagents_raw"]["agent-abc123"], before)
        self.assertEqual(sorted(os.listdir(os.path.join(d, "subagents"))), files, "nothing removed")
        data, problems = cl.read_raw(d, "subagents/agent-abc123")
        self.assertEqual((len(data.splitlines()), problems), (80, []))

    def test_a_temporary_file_left_by_a_killed_write_is_not_kept(self):
        self.source(6)
        d = self.export()
        with open(os.path.join(d, "meta.json" + cl.TMP), "w") as f:
            f.write("half")
        self.export()  # meta.json unchanged: not written again, the leftover still goes
        self.assertNotIn("meta.json" + cl.TMP, os.listdir(d))


class Broken(Rig):
    def test_a_missing_or_wrong_segment_is_said(self):
        self.source(80)
        d = self.export()
        segs = self.meta(d)["raw"]["segments"]
        os.remove(os.path.join(d, segs[1]["file"]))
        data, problems = cl.read_raw(d)
        self.assertIn("缺了 %s" % segs[1]["file"], problems)
        r = subprocess.run([sys.executable, PATH, "raw", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("缺了", r.stderr)
        with open(os.path.join(d, segs[0]["file"]), "wb") as f:
            f.write(cl._gzip(b'{"x": 1}\n'))
        self.assertTrue(any("跟 meta.json 記的不符" in p for p in cl.read_raw(d)[1]))
        with open(os.path.join(d, segs[2]["file"]), "wb") as f:
            f.write(b"not gzip")
        self.assertTrue(any("讀不出來" in p for p in cl.read_raw(d)[1]))

    def test_no_readable_meta_is_never_an_empty_success(self):
        """Manager 2026-10-08 (#code-review-2): meta.json cut short, gone, or the wrong folder: exit 1 and why."""
        self.source(80)
        d = self.export()
        with open(os.path.join(d, "meta.json"), "rb") as f:
            meta = f.read()

        def raw(folder):
            r = subprocess.run([sys.executable, PATH, "raw", folder], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            return r.returncode, r.stdout, r.stderr
        with open(os.path.join(d, "meta.json"), "wb") as f:
            f.write(meta[:300])
        code, out, err = raw(d)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("meta.json 讀不出來", err)
        self.assertIn("有段檔", err, "its own segments there and not listed: said")
        os.remove(os.path.join(d, "meta.json"))
        self.assertIn("沒有 meta.json", raw(d)[2])
        self.assertEqual(raw(d + "-typo")[0], 1)
        recs, problems = cl.load_raw(d)
        self.assertEqual(recs, [])
        self.assertTrue(problems)
        with open(os.path.join(d, "meta.json"), "wb") as f:
            f.write(meta)
        self.assertEqual(raw(d)[0], 0)

    def test_a_meta_json_of_the_wrong_shape_is_said_not_a_crash(self):
        """#code-review 2026-10-08 low 1: [] or null, or segments that are not objects; progress must not fall over."""
        self.source(80)
        d = self.export()
        meta = self.meta(d)
        for bad in ([], None, dict(meta, raw=dict(meta["raw"], segments=[1, "x"]))):
            with self.subTest(bad=str(bad)[:40]):
                with open(os.path.join(d, "meta.json"), "w") as f:
                    json.dump(bad, f)
                recs, problems = cl.load_raw(d)
                self.assertEqual(recs, [])
                self.assertTrue(any("有段檔" in p for p in problems), problems)
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(dict(meta, subagents_raw=[1]), f)
        self.assertEqual(cl.read_raw(d, "subagents/x"), (b"", []))
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))

    def test_a_folder_with_no_raw_at_all_says_so(self):
        self.source(6)
        d = self.export()
        os.remove(os.path.join(d, "raw.jsonl"))  # a rebuilt session: meta.json, no raw
        self.assertEqual(cl.read_raw(d), (b"", []))
        r = subprocess.run([sys.executable, PATH, "raw", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("沒有原始紀錄", r.stderr)

    def test_files_meta_does_not_list_are_said_not_held_against_it(self):
        """Manager 2026-10-08: meta.json is the commit point; what it lists is whole and right: printed, exit 0."""
        self.source(80)
        d = self.export()
        with open(os.path.join(d, "raw-0099.jsonl.gz"), "wb") as f:
            f.write(b"left by a stopped run")
        r = subprocess.run([sys.executable, PATH, "raw", d], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout), (0, self.whole(80)))
        self.assertIn("raw-0099.jsonl.gz", r.stderr.decode())
        self.assertIn("raw-0099.jsonl.gz", " ".join(cl.load_raw(d)[1]))

    def test_same_lines_wrong_content_says_the_hash(self):
        self.source(80)
        d = self.export()
        x = self.meta(d)["raw"]["segments"][0]
        with open(os.path.join(d, x["file"]), "wb") as f:
            f.write(cl._gzip(b'{"a": 1}\n' * x["lines"]))
        self.assertTrue(any("雜湊不符（行數相同）" in p for p in cl.read_raw(d)[1]), cl.read_raw(d)[1])

    def test_damage_of_the_same_size_is_written_again_by_the_next_export(self):
        """Manager 2026-10-08: export compares by hash, not by size (sealed and tail)."""
        self.source(80)
        d = self.export()
        segs = self.meta(d)["raw"]["segments"]
        for x in (segs[2], segs[-1]):
            path = os.path.join(d, x["file"])
            with open(path, "rb") as f:
                data = bytearray(f.read())
            data[len(data) // 2] ^= 0xFF
            with open(path, "wb") as f:
                f.write(bytes(data))
        self.assertEqual(len(cl.read_raw(d)[1]), 2)
        self.export()  # the conversation did not even grow
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))
        with open(os.path.join(cl.STATE_DIR, "chatlog.log"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("written again"), 2)

    def test_a_subagent_joined_back_on_the_command_line(self):
        self.source(4)
        sub = os.path.join(os.path.dirname(self.src), SID, "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-1.jsonl"), "w", encoding="utf-8") as f:
            for i in range(80):
                f.write(json.dumps(rec(i), ensure_ascii=False) + "\n")
        d = self.export()
        r = subprocess.run([sys.executable, PATH, "raw", d, "--sub", "agent-1"], stdout=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout), (0, self.whole(80)))

    def test_pm_says_it(self):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "progress"))
        import progress
        self.source(80)
        d = self.export()
        os.remove(os.path.join(d, self.meta(d)["raw"]["segments"][0]["file"]))
        said = []
        with mock.patch.object(progress, "log", said.append), mock.patch.object(progress, "LOGS", cl.REPO):
            progress.load_raw(d)
        self.assertTrue(said and "缺了" in said[0], said)


class Stopped(Rig):
    """A run killed anywhere: what meta.json names is whole, and the next run finishes the job."""

    def test_killed_before_meta_json_the_old_record_stands(self):
        self.source(30)
        with mock.patch.object(cl, "SEG_MAX", WHOLE):
            d = self.export()  # a whole raw.jsonl first
        self.source(80)
        real = cl.write

        def dies(path, text):
            if path.endswith("meta.json"):
                raise KeyboardInterrupt
            return real(path, text)
        with mock.patch.object(cl, "write", dies), self.assertRaises(KeyboardInterrupt):
            self.export()
        self.assertIn("raw.jsonl", os.listdir(d), "still there")
        self.assertEqual(cl.read_raw(d), (self.whole(30), []), "the record is the old whole file")
        self.export()
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))
        self.assertNotIn("raw.jsonl", os.listdir(d))

    def test_killed_after_meta_json_before_the_old_file_went(self):
        self.source(80)
        with mock.patch.object(cl, "cleanup_segments", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            with mock.patch.object(cl, "SEG_MAX", WHOLE):
                d = self.export()
            self.export()
        d = os.path.dirname(cl.find_existing_dir(SID) + "/")
        notes = []
        data, problems = cl.read_raw(d, notes=notes)
        self.assertEqual((data, problems), (self.whole(80), []), "the segments are the record")
        self.assertEqual(len(notes), 1)
        self.assertIn("raw.jsonl", notes[0], "the leftover said, not held against the record")
        self.export()
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))

    def test_killed_after_the_tail_grew_before_meta_json(self):
        self.source(72)  # a short tail
        d = self.export()
        self.source(74)  # not enough for a new full segment: the tail grows in place
        real = cl.write

        def dies(path, text):
            if path.endswith("meta.json"):
                raise KeyboardInterrupt
            return real(path, text)
        with mock.patch.object(cl, "write", dies), self.assertRaises(KeyboardInterrupt):
            self.export()
        tail = self.meta(d)["raw"]["segments"][-1]
        self.assertGreater(os.path.getsize(os.path.join(d, tail["file"])), tail["bytes"], "the tail did grow")
        self.assertEqual(cl.read_raw(d), (self.whole(72), []), "what meta.json names, from the front of the tail")

    def test_killed_while_writing_a_segment(self):
        self.source(80)
        with mock.patch.object(cl.os, "fsync", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.export()
        self.assertIsNone(cl.find_existing_dir(SID), "no meta.json yet: nothing that counts was written")
        self.export()
        d = cl.find_existing_dir(SID)
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))
        self.assertFalse([n for n in os.listdir(d) if n.endswith(cl.TMP)], "temporary files cleared")


class Unsure(Rig):
    """Manager 2026-10-08: segments of its own names there and meta.json without a complete list of them → the
    conversation is not exported, nothing rewritten or removed (switch on or off), one line in chatlog.log."""

    def commit(self):
        subprocess.run(["git", "-C", cl.REPO, "add", "-A"], check=True)
        subprocess.run(["git", "-C", cl.REPO, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)

    def lines_said(self):
        try:
            with open(os.path.join(cl.STATE_DIR, "chatlog.log"), encoding="utf-8") as f:
                return f.read().count("沒有完整的分段清單")
        except OSError:
            return 0

    def test_every_broken_meta_with_every_source_and_switch(self):
        def missing(d, m):
            os.remove(os.path.join(d, "meta.json"))

        def cut(d, m):
            with open(os.path.join(d, "meta.json"), "wb") as f:
                f.write(m[:300])

        def drop(field):
            def change(d, m):
                meta = json.loads(m)
                del meta["raw"]["segments"][1][field]
                with open(os.path.join(d, "meta.json"), "w") as f:
                    json.dump(meta, f)
            return change
        def seg_lines(value):
            def change(d, m):
                meta = json.loads(m)
                meta["raw"]["segments"][0]["lines"] = value
                with open(os.path.join(d, "meta.json"), "w") as f:
                    json.dump(meta, f)
            return change

        def top(field, value=None):
            def change(d, m):
                meta = json.loads(m)
                if value is None:
                    del meta["raw"][field]
                else:
                    meta["raw"][field] = value
                with open(os.path.join(d, "meta.json"), "w") as f:
                    json.dump(meta, f)
            return change
        for broken in (missing, cut, drop("lines"), drop("sha256"), drop("file"), top("lines"), top("lines", "80"),
                       top("lines", True), top("sha256"), top("lines", 0), top("lines", 79), seg_lines(-1)):
            for n in (60, 80, 100):
                for whole in (False, True):  # also when a whole raw.jsonl would be written (a source under SEG_MAX)
                    with self.subTest(broken=broken.__name__ if broken in (missing, cut) else "drop", source=n, whole=whole):
                        self.fresh()
                        self.source(80)
                        d = self.export()
                        self.commit()
                        with open(os.path.join(d, "meta.json"), "rb") as f:
                            broken(d, f.read())
                        before = self.snapshot(d)
                        self.source(n)
                        with mock.patch.object(cl, "SEG_MAX", WHOLE if whole else SEG):
                            self.assertIsNone(self.export())
                            self.assertIsNone(self.export())
                        self.assertEqual(self.snapshot(d), before, "every file byte for byte as it was")
                        self.assertEqual(self.lines_said(), 1, "one line, not one per round")

    def test_split_leaves_an_unsure_folder_alone(self):
        """#code-review 2026-10-08: a broken list and half a raw.jsonl beside it: split must not rebuild from it."""
        self.source(80)
        d = self.export()
        self.commit()
        meta = self.meta(d)
        del meta["raw"]["segments"][0]["lines"]
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(meta, f)
        with open(os.path.join(d, "raw.jsonl"), "wb") as f:
            f.write(self.whole(80)[:len(self.whole(80)) // 2].rsplit(b"\n", 1)[0] + b"\n")
        before = self.snapshot(d)
        self.assertIn("不動", cl.split_existing(d))
        self.assertEqual(self.snapshot(d), before)

    def test_subagents_raw_not_an_object_does_not_stop_export(self):
        self.source(6)
        d = self.export()
        meta = self.meta(d)
        meta["subagents_raw"] = [1]
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(meta, f)
        self.source(8)
        self.assertIsNotNone(self.export())

    def test_a_subagent_big_from_the_start_stopped_before_meta_json_goes_on(self):
        """Manager 2026-10-08: a whole meta.json that lists none of it: its files never were the record."""
        self.source(6)
        d = self.export()
        self.commit()
        sub = os.path.join(os.path.dirname(self.src), SID, "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-9.jsonl"), "w", encoding="utf-8") as f:
            for i in range(80):
                f.write(json.dumps(rec(i), ensure_ascii=False) + "\n")
        calls = []
        real = cl.os.fsync

        def dies(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(fd)
        with mock.patch.object(cl.os, "fsync", dies), self.assertRaises(KeyboardInterrupt):
            self.export()
        self.commit()  # a sync in between commits what was left
        self.assertEqual(cl.unsure(d), [])
        self.source(8)
        self.assertIsNotNone(self.export())
        self.assertEqual(cl.read_raw(d, "subagents/agent-9"), (self.whole(80), []))
        self.assertFalse([n for n in os.listdir(os.path.join(d, "subagents")) if n.endswith(cl.TMP)])

    def test_said_again_an_hour_later(self):
        self.source(80)
        d = self.export()
        self.commit()
        os.remove(os.path.join(d, "meta.json"))
        self.export()
        with mock.patch.object(cl.time, "time", return_value=cl.time.time() + 3700):
            self.export()
        self.assertEqual(self.lines_said(), 2)

    def test_a_whole_file_and_a_meta_without_a_list_is_not_unsure(self):
        """The first split stopped before meta.json: the whole raw.jsonl is the record; export goes on."""
        self.source(80)
        with mock.patch.object(cl, "SEG_MAX", WHOLE):
            d = self.export()
        self.commit()
        with open(os.path.join(d, "raw-0001.jsonl.gz"), "wb") as f:
            f.write(b"left")
        self.assertEqual(cl.unsure(d), [])
        self.assertIsNotNone(self.export())
        self.assertEqual(cl.read_raw(d), (self.whole(80), []))

    def test_a_subagent_with_a_broken_entry_stops_the_conversation(self):
        self.source(4)
        sub = os.path.join(os.path.dirname(self.src), SID, "subagents")
        os.makedirs(sub)
        with open(os.path.join(sub, "agent-1.jsonl"), "w", encoding="utf-8") as f:
            for i in range(80):
                f.write(json.dumps(rec(i), ensure_ascii=False) + "\n")
        d = self.export()
        self.commit()
        meta = self.meta(d)
        del meta["subagents_raw"]["agent-1"]["segments"][0]["sha256"]
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(meta, f)
        self.assertEqual(cl.unsure(d), ["subagents/agent-1"])
        before = self.snapshot(d)
        self.source(6)
        self.assertIsNone(self.export())
        self.assertEqual(self.snapshot(d), before)


class SyncGoesOn(Rig):
    """Manager 2026-10-08: one folder's meta.json broken in any way never stops this machine's sync: every other
    conversation is committed and pushed."""

    def test_only_the_scheduled_sync_brings_the_schedules_trace_up_to_date(self):
        """Manager 2026-10-09: every session's end synced and wrote last_sync, the schedule's trace: --compare
        took those for runs at other times. The plain sync (what the schedule runs) writes last_cron_sync."""
        origin = os.path.join(ROOT, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
        subprocess.run(["git", "-C", cl.REPO, "remote", "add", "origin", origin], check=True)
        env = mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                                           "GIT_COMMITTER_EMAIL": "t@t"})
        env.start()
        self.addCleanup(env.stop)
        self.source(6)
        self.assertEqual(cl.do_sync(only=self.src), 0)  # a session's end: export --sync <path>
        st = cl.load_state()
        self.assertTrue(st["last_sync"])
        self.assertIsNone(st.get("last_cron_sync"))
        self.assertEqual(cl.do_sync(full=True), 0)  # a rebuild by hand: sync --all
        self.assertIsNone(cl.load_state().get("last_cron_sync"))
        self.assertEqual(cl.do_sync(), 0)  # the schedule's: sync
        st = cl.load_state()
        self.assertEqual(st["last_cron_sync"], st["last_sync"])
        kept = st["last_cron_sync"]
        with mock.patch.object(cl, "datetime") as later:
            later.now.return_value.isoformat.return_value = "2099-01-01T00:00:00+08:00"
            self.assertEqual(cl.do_sync(only=self.src), 0)
        st = cl.load_state()
        self.assertEqual(st["last_sync"], "2099-01-01T00:00:00+08:00")
        self.assertEqual(st["last_cron_sync"], kept, "a session ending later leaves the schedule's trace alone")
        with open(os.path.join(HERE, "..", "plugin.json"), encoding="utf-8") as f:
            trace = json.load(f)["schedules"][0]["do"]
        self.assertEqual((trace["run"].split()[-1], trace["trace"]["key"]), ("sync", "last_cron_sync"))

    def test_any_broken_meta_and_the_others_are_pushed(self):
        other = os.path.join(cl.CLAUDE_DIR, "projects", "-w-proj", "6f000000-0000-4000-8000-000000000002.jsonl")

        def other_source(n):
            with open(other, "w", encoding="utf-8") as f:
                for i in range(n):
                    r = rec(i)
                    r["sessionId"] = "6f000000-0000-4000-8000-000000000002"
                    r["timestamp"] = r["timestamp"].replace("2026-10-01", "2026-10-02")
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for name, text in (("[]", "[]"), ("null", "null"), ("string", '"x"'), ("number", "3"), ("cut", None),
                           ("gone", ""), ("lines", "lines"), ("start_utc", 5), ("title", ["x"]), ("models", 3),
                           ("start_local", 7)):
            with self.subTest(meta=name):
                self.fresh()
                origin = os.path.join(ROOT, "origin.git")
                subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
                subprocess.run(["git", "-C", cl.REPO, "remote", "add", "origin", origin], check=True)
                env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
                with mock.patch.dict(os.environ, env):
                    self.source(80)
                    other_source(4)
                    self.assertEqual(cl.do_sync(), 0)
                    d = cl.find_existing_dir(SID)
                    path = os.path.join(d, "meta.json")
                    with open(path, "rb") as f:
                        whole = f.read()
                    if name == "gone":
                        os.remove(path)
                    elif name == "cut":
                        with open(path, "wb") as f:
                            f.write(whole[:200])
                    elif name == "lines":
                        meta = json.loads(whole)
                        del meta["raw"]["lines"]
                        with open(path, "w") as f:
                            json.dump(meta, f)
                    elif not isinstance(text, str) and text is not None:  # a field of the wrong kind, its source gone
                        meta = json.loads(whole)
                        meta[name] = text
                        with open(path, "w") as f:
                            json.dump(meta, f)
                        os.remove(self.src)
                    else:
                        with open(path, "w") as f:
                            f.write(text)
                    before = self.snapshot(d)
                    if os.path.exists(self.src):
                        self.source(60)
                    other_source(6)
                    self.assertEqual(cl.do_sync(), 0)
                    self.assertEqual(self.snapshot(d), before, "the broken one untouched")
                    rel = os.path.relpath(cl.find_existing_dir("6f000000-0000-4000-8000-000000000002"), cl.REPO)
                    pushed = subprocess.run(["git", "-C", origin, "show", "HEAD:%s/raw.jsonl" % rel], stdout=subprocess.PIPE,
                                            universal_newlines=True).stdout
                    self.assertEqual(len(pushed.splitlines()), 6, "the other conversation went out")
                    self.assertEqual(subprocess.run(["git", "-C", cl.REPO, "status", "--porcelain"], stdout=subprocess.PIPE,
                                                    universal_newlines=True).stdout, "", "everything committed")


class Split(Rig):
    """chatlog.py split: a big raw.jsonl whose conversation is gone, put in segments from itself."""

    def made(self, n=80):
        self.source(n)
        with mock.patch.object(cl, "SEG_MAX", WHOLE):
            d = self.export()
        os.remove(self.src)
        with open(os.path.join(d, "raw.jsonl"), "rb") as f:
            return d, f.read()

    def test_split_joins_back_and_runs_again(self):
        d, old = self.made()
        self.assertIn("分好了", cl.split_existing(d))
        self.assertEqual(cl.read_raw(d), (old, []))
        self.assertNotIn("raw.jsonl", os.listdir(d))
        self.assertIn("已分段", cl.split_existing(d))

    def test_split_stopped_anywhere_then_again(self):
        for where in ("write_segments_done", "meta_written"):
            with self.subTest(where=where):
                shutil.rmtree(cl.REPO)
                os.makedirs(cl.REPO)
                subprocess.run(["git", "init", "-q", cl.REPO], check=True)
                d, old = self.made()
                target = ("write", lambda p, t: (_ for _ in ()).throw(KeyboardInterrupt)) if where == "write_segments_done" \
                    else ("cleanup_segments", mock.Mock(side_effect=KeyboardInterrupt))
                with mock.patch.object(cl, target[0], target[1]), self.assertRaises(KeyboardInterrupt):
                    cl.split_existing(d)
                self.assertIn("raw.jsonl", os.listdir(d), "the old file is still there")
                got, _ = cl.read_raw(d)
                self.assertEqual(got, old, "whichever meta.json names, it is whole")
                self.assertIn("分好了" if where == "write_segments_done" else "收尾", cl.split_existing(d))
                self.assertEqual(cl.read_raw(d), (old, []))
                self.assertNotIn("raw.jsonl", os.listdir(d))

    def test_small_and_unfinished_are_left(self):
        d, old = self.made(6)
        self.assertIn("不用分", cl.split_existing(d))
        d, old = self.made(80)
        with open(os.path.join(d, "raw.jsonl"), "ab") as f:
            f.write(b'{"half":')
        self.assertIn("最後一行沒有換行", cl.split_existing(d))

    def test_the_command_only_takes_this_machines_big_ones(self):
        d, old = self.made()
        other = os.path.join(cl.REPO, "host-b", "2026-10", "x")
        os.makedirs(other)
        shutil.copy(os.path.join(d, "raw.jsonl"), other)
        shutil.copy(os.path.join(d, "meta.json"), other)
        env = dict(os.environ)
        r = subprocess.run([sys.executable, PATH, "split", "--over", str(SEG / 1048576 / 2)], env=env,
                           stdout=subprocess.PIPE, universal_newlines=True)
        # the child has the real SEG_MAX (8 MB): too small to split, but it looked only at host-a
        self.assertIn("不用分：%s" % d, r.stdout)
        self.assertNotIn("host-b", r.stdout)


def tearDownModule():
    shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
