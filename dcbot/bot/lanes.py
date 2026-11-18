"""Each channel's lane: what a turn shows along the way (Claude's words as quotes, the "working…" message, 📤 to
another conversation) is posted from here, in the order it came, and never from the round of hook events.

The round (tmux_bridge.pump_round()) handles every channel's events one after another; a Discord request that
hung there held them all (11-10: a progress event of one channel waited 135 s on Discord, a SessionStart of
another channel waited behind it, and that channel's good window was closed as not come up). The round now only
puts the work in the channel's lane and goes on; the lane waits on Discord, at most LANE_SECS for each request.

Rules (manager on the plan, 11-10):
- What ends a turn (its reply, a question, a failure) comes after everything the lane holds: such an event of the
  channel stays in the spool while its lane is busy (tmux_bridge.lane_waits()), and the "working…" updates not yet
  sent are dropped then. The other channels' events go on meanwhile. On another machine's stream the event and the
  channel's later ones are kept aside the same way (tmux_bridge.from_stream(), C-174): that machine's other channels
  go on.
- A request that does not answer in LANE_SECS is not known not to have reached Discord: a new message is never
  sent again (a quote would show twice); it is left to finish on its own, and a "working…" message it makes after
  its turn has moved on is deleted when it comes (Turn.gen). An edit or a delete may be tried once more.
- A restart drops what the lanes still hold (only progress), with one line in the log.
"""
import asyncio
import collections
import time
import traceback

LANE_SECS = 30   # the longest one Discord request of a lane is waited for
LANE_HOLD = 60   # a timed-out new message still under way holds its channel's turn end this long from its start


class TimedOut(Exception):
    """A request of the lane got no answer in LANE_SECS (said in the log already)."""


class Lane(object):
    def __init__(self, cid):
        self.cid = str(cid)
        self.items = collections.deque()  # (what, job, progress): job() is awaited; progress: a "working…" update
        self.task = None
        self.running = None  # (what, since) of the job being done
        self.late = []       # (task, since): new messages that timed out and are still under way

    def put(self, what, job, progress=False):
        """Add `job` (a function that gives an awaitable) to the end. A "working…" update is added only when none
        is waiting: the one waiting reads the latest lines when its turn comes."""
        if progress and any(p for _, _, p in self.items):
            return
        self.items.append((what, job, progress))
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self.run())

    async def run(self):
        while self.items:
            what, job, _ = self.items.popleft()
            self.running = (what, time.monotonic())
            try:
                await job()
            except TimedOut:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                traceback.print_exc()
            finally:
                self.running = None

    def busy(self):
        """Does something of the lane still have to reach Discord before what ends the turn is posted?"""
        now = time.monotonic()
        self.late = [(t, s) for t, s in self.late if not t.done() and now - s < LANE_HOLD]
        return bool(self.items or self.running or self.late)

    def drop_progress(self):
        """The turn ends: the "working…" updates not sent yet are not wanted any more (the quotes stay)."""
        if any(p for _, _, p in self.items):
            self.items = collections.deque(i for i in self.items if not i[2])

    def stop(self):
        """The bot restarts: what is not sent is dropped. Gives how many were dropped."""
        n = len(self.items)
        self.items.clear()
        if self.task is not None and not self.task.done():
            self.task.cancel()
        return n

    async def call(self, what, make, new=False, on_late=None):
        """One Discord request: `make()` gives its awaitable. Waited for LANE_SECS at most; a timeout is said in the
        log and raises TimedOut. `new`: it makes a new message, so it is never sent again: left running, and
        `on_late(message)` is called if it comes after all. Anything else (an edit, a delete) is tried once more."""
        for attempt in (1, 2):
            task = asyncio.ensure_future(make())
            try:
                return await asyncio.wait_for(asyncio.shield(task) if new else task, LANE_SECS)
            except asyncio.TimeoutError:
                pass
            if new:
                print("[lane] #%s：%s %d 秒沒有回應，不重送（之後送到也照常留著）" % (self.cid, what, LANE_SECS), flush=True)
                self.late.append((task, time.monotonic()))
                task.add_done_callback(lambda t: late_done(t, on_late))
                raise TimedOut()
            print("[lane] #%s：%s %d 秒沒有回應（第 %d 次）" % (self.cid, what, LANE_SECS, attempt), flush=True)
        raise TimedOut()


def late_done(task, on_late):
    """A timed-out new message finished: `on_late(message)`, or on_late(None) when it failed (its error read, so no
    "exception never retrieved" in the log)."""
    msg = None if task.cancelled() or task.exception() is not None else task.result()
    if on_late is not None:
        try:
            on_late(msg)
        except Exception:
            traceback.print_exc()
