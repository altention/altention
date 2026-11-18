"""C-175: the channels' state is written off the event loop. Bot.save() makes the text and hands it to one writer
thread; a slow write (NFS) no longer holds the loop, saves meanwhile fold into the newest, and nothing older is ever
written after something newer."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()

import asyncio
import base64
import contextlib
import io
import json
import signal
import tarfile
import types
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401
from bot import main, standby, statewrite


class Writer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "channels.json")
        self.w = statewrite.Writer()

    def tearDown(self):
        self.assertTrue(self.w.flush(10))
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_slow_write_holds_nobody_and_the_newest_wins(self):
        gate, written = threading.Event(), []
        real = statewrite.write

        def slow(path, text):
            gate.wait(5)  # the disk hangs until let go
            written.append(json.loads(text)["n"])
            real(path, text)
        with mock.patch.object(statewrite, "write", slow):
            t0 = time.time()
            for n in range(5):
                self.w.put(self.path, json.dumps({"n": n}))
            self.assertLess(time.time() - t0, 0.5, "put() never waits on the disk")
            gate.set()
            self.assertTrue(self.w.flush(10))
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"n": 4})
        self.assertEqual(written[-1], 4)
        self.assertEqual(written, sorted(written), "never an older one after a newer")
        self.assertLessEqual(len(written), 2, "the ones that came while one was written were folded into one")
        self.assertEqual(os.listdir(self.dir), ["channels.json"], "no .tmp left")

    def test_a_failed_write_said_once_and_the_next_one_works(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with mock.patch.object(statewrite, "write", mock.Mock(side_effect=OSError("stale file handle"))):
                for n in range(3):
                    self.w.put(self.path, json.dumps({"n": n}))
                    self.assertTrue(self.w.flush(5))
            self.w.put(self.path, json.dumps({"n": 9}))
            self.assertTrue(self.w.flush(5))
        said = out.getvalue()
        self.assertEqual(said.count("could not write channels.json"), 1, said)
        self.assertIn("channels.json is written again", said)
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"n": 9})


class Save(unittest.TestCase):
    def test_bot_save_in_the_loop_is_not_held_by_the_disk(self):
        """Bot.save() inside the running loop, the disk taking 2 s: the loop goes on at once; the file then says
        what the state was at the save, not what it became later."""
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "state", "channels.json")
        gate = threading.Event()
        real = statewrite.write

        def slow(p, text):
            gate.wait(2)
            real(p, text)
        bot = mock.Mock(channels_state={"1": {"cwd": "/w"}})

        async def go():
            t0 = time.time()
            main.Bot.save(bot)
            took = time.time() - t0
            bot.channels_state["1"]["cwd"] = "/changed-after"  # not saved: must not reach the file
            return took
        try:
            with mock.patch.object(main, "CHANNELS", path), mock.patch.object(statewrite, "write", slow):
                took = asyncio.run(go())
                self.assertLess(took, 0.5)
                gate.set()
                self.assertTrue(statewrite.WRITER.flush(10))
            with open(path) as f:
                self.assertEqual(json.load(f)["1"]["cwd"], "/w")
        finally:
            gate.set()
            statewrite.WRITER.flush(10)
            shutil.rmtree(tmp, ignore_errors=True)


class Push(unittest.TestCase):
    def test_the_standby_gets_what_is_in_memory_while_the_file_is_still_being_written(self):
        """#code-review on 0b43435: push_rounds records what it pushed from memory; push_state read channels.json
        from the disk, which the writer may not have written yet (NFS slow): the standby got the old pane, and the
        change was not pushed again for hours. The bundle now carries the channels as they are in memory."""
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, "state")
        os.makedirs(state)
        path = os.path.join(state, "channels.json")
        with open(path, "w") as f:
            json.dump({"1": {"pane": {"id": "%old"}}}, f)
        gate, sent = threading.Event(), []
        real = statewrite.write

        def stuck(p, text):
            gate.wait(10)
            real(p, text)

        async def dcremote(host, cmd, input=None, timeout=None, err=None):
            sent.append(input)
            return 0, ""
        b = types.SimpleNamespace(channels_state={"1": {"pane": {"id": "%new"}}}, lease=types.SimpleNamespace(epoch=3))
        try:
            with mock.patch.object(main, "CHANNELS", path), mock.patch.object(main, "STATE", state), \
                    mock.patch.object(statewrite, "write", stuck), \
                    mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-a": {"roles": ["dcbot"]},
                                                                                       "host-b": {"roles": ["dcbot-standby"]}}}), \
                    mock.patch.object(main.remote, "this_machine", lambda: "host-a"), \
                    mock.patch.object(main.remote, "dcremote", dcremote), \
                    mock.patch.object(standby, "meta", lambda root, me: {"machine": me}), \
                    mock.patch.object(standby, "bot_panes_socket", lambda s: None), \
                    mock.patch.object(main.ask_mod, "bundled", lambda: []):
                main.Bot.save(b)  # handed to the writer, which hangs
                said = asyncio.run(main.Bot.push_state(b))
            self.assertTrue(said["host-b"]["ok"])
            with open(path) as f:
                self.assertEqual(json.load(f)["1"]["pane"]["id"], "%old", "(the file was indeed still the old one)")
            with tarfile.open(fileobj=io.BytesIO(base64.b64decode(sent[0])), mode="r:gz") as tar:
                pushed = json.load(tar.extractfile("channels.json"))
            self.assertEqual(pushed["1"]["pane"]["id"], "%new")
        finally:
            gate.set()
            statewrite.WRITER.flush(10)
            shutil.rmtree(tmp, ignore_errors=True)


class Ends(unittest.TestCase):
    def test_the_backstop_writes_before_it_ends_the_process(self):
        """#code-review-2 on 0b43435: arm_end()'s hard() is the one way out that does not wait for the writer
        (os._exit). It waits for it first: the file is written before os._exit is called."""
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "channels.json")
        real, order = statewrite.write, []

        def slow(p, text):
            time.sleep(0.5)
            real(p, text)
            order.append("written")
        timers = []

        class Timer(object):
            def __init__(self, secs, fn):
                timers.append(fn)

            def start(self):
                pass
        b = types.SimpleNamespace(exit_code=4)

        def exit_(code):
            with open(path) as f:
                order.append(("os._exit", code, json.load(f)))
        try:
            with mock.patch.object(statewrite, "write", slow), mock.patch.object(main.threading, "Timer", Timer), \
                    mock.patch.object(main.os, "_exit", exit_), contextlib.redirect_stdout(io.StringIO()):
                main.Bot.arm_end(b)
                statewrite.WRITER.put(path, json.dumps({"n": 1}))
                timers[0]()  # END_SECS later
            self.assertEqual(order, ["written", ("os._exit", 4, {"n": 1})])
        finally:
            statewrite.WRITER.flush(10)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sigterm_ends_as_sys_exit_does(self):
        """uninstall.sh and SUPERVISOR-RESCUE.md kill bot.pid: SystemExit, so main()'s finally flushes the writer."""
        with self.assertRaises(SystemExit) as got:
            main.ended(signal.SIGTERM, None)
        self.assertEqual(got.exception.code, 128 + signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
