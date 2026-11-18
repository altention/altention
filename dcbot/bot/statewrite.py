"""The channels' state file written off the event loop (C-175).

state/channels.json is a few MB on NFS, written on every change. Written in the event loop, one slow NFS write
stopped everything (11-02: the pump idle 103 s). Bot.save() makes the text there (json.dumps: what the state is at
that moment, ~30 ms) and hands it to one writer thread, which writes it whole (aside, then renamed). Saves that come
while one is being written are folded into one: only the newest text of a file is written, never an older one after
it. The thread is not a daemon, so an ordinary exit waits for the last write; flush() waits for it on purpose.
"""
import os
import threading


class Writer(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}         # path -> the newest text not yet written
        self.thread = None
        self.idle = threading.Event()
        self.idle.set()
        self.failing = set()      # paths whose last write failed (said once, until one works again)

    def put(self, path, text):
        with self.lock:
            self.pending[path] = text
            self.idle.clear()
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, name="state-writer", daemon=False)
                self.thread.start()

    def run(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.thread = None
                    self.idle.set()
                    return
                path = next(iter(self.pending))
                text = self.pending.pop(path)
            self.write_one(path, text)

    def write_one(self, path, text):
        try:
            write(path, text)
            if path in self.failing:
                self.failing.discard(path)
                print("state: %s is written again" % os.path.basename(path), flush=True)
        except Exception as e:  # (the next save tries again: it carries the whole state)
            if path not in self.failing:
                self.failing.add(path)
                print("state: could not write %s (%s: %s); tried again at the next change" % (
                    os.path.basename(path), type(e).__name__, e), flush=True)

    def flush(self, timeout=None):
        """Wait until everything handed over is written (or `timeout` seconds): True when it is."""
        return self.idle.wait(timeout)


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


WRITER = Writer()
