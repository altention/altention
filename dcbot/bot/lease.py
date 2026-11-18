"""Who is the bot (C-163 step 1, design: instance docs/refactor/c163-design.md sections 1.1-1.3).

Only for an installation with a standby: a machine of the list with the role ROLE. Without one, nothing here runs
at all (enabled() is False; main.Bot makes no Lease, leader_ok() is always True): no #bot-lease read or written,
nothing queued, the bot as it always was.

#bot-lease holds one message per machine, each edited by its own machine only: one line of JSON
{"machine", "epoch", "leader", "lt"}. Times are Discord's (the message's edited_timestamp), never a machine's clock:
two machines whose clocks differ still agree. "lt" is the Discord time of that machine's last edit with
leader: true (an edit with leader: false only says the machine is alive and copies the old lt; it never stands in
the way of a takeover). The bot is the machine whose lt is fresh (within FRESH_SECS of the reader's own newest
edit) and whose leader is true, with the biggest epoch; on a tie, the machine list's order.

leader_ok() is a function of two times, looked at whenever it is used: the last read of the lease that found this
machine the bot (from that read's start) is under READ_OK_SECS old, and the last edit of its own message is under
WRITE_OK_SECS old. Clocks: CLOCK_BOOTTIME on Linux (counts sleep and a stopped process), CLOCK_MONOTONIC on macOS
(counts sleep there), the wall clock elsewhere (going back means not ok). False for YIELD_SECS in a row: the bot
gives the place up (the caller ends it), so a half-broken machine does not hold it forever (section 1.3).

A bot started while it is crashing (the supervisor says so, DCBOT_CRASHING: its last run ended before it was up the
supervisor's STABLE_SECS) and while its machine holds the place already makes no edit until it has been up
STABLE_SECS, and only then writes leader: true. Meanwhile it is the bot by the lt written before (a good read is
enough, no edit asked: an edit would move lt, and a leader: false one holds nothing), but only while that lt is under
FRESH_SECS - SETTLE_MARGIN old, so a standby never finds it stale while this bot counts itself the bot (#code-review
and #code-review-2 on 67ba1d8: its own reads alone always find an lt never moved since fresh). Past that it is not
the bot and still makes no edit until up STABLE_SECS (an edit then would move lt at a crash loop's every start and
the standby would never take over). How old, the older of two: the lt's distance from the newest edit of any machine
at the first read, plus the time since; and state/lease-true-at, this machine's clock() at its last leader: true edit
that Discord took (the same boot only, the wall clock agreeing). The first alone can be short: a standby edits once a
minute, or not at all while down. A bot that crashes every half minute so no longer
moves lt at each start: its lt goes stale FRESH_SECS after its last stable run and the standby takes over then
(drill item 17, manager 11-09; the primary's gate leaves the lapsed place to the standby meanwhile:
leave_to_standby()). Every other start (a deploy, !restart, a start after a clean end, a takeover, a failback)
writes leader: true at once, as before (manager 11-10).
"""
import datetime
import json
import os
import sys
import time

ROLE = "dcbot-standby"
FRESH_SECS = 300
READ_OK_SECS = 90
WRITE_OK_SECS = 120
YIELD_SECS = 180
ROUND_SECS = 60
STABLE_SECS = 120  # a bot restarted while crashing up this long before its first leader: true (the module's note)
SETTLE_MARGIN = 2 * ROUND_SECS  # meanwhile it is the bot only while its old lt is this much short of FRESH_SECS
QUEUE_MAX = 500
QUEUE_SECS = 600


def enabled(machines):
    """Does this installation have a standby (a machine of `machines`, the list's {name: entry}, with ROLE)?"""
    return any(isinstance(m, dict) and ROLE in (m.get("roles") or []) for m in (machines or {}).values())


def clock():
    """Seconds that keep counting while the machine sleeps or the process is stopped (see the module's note)."""
    if sys.platform.startswith("linux") and hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    if sys.platform == "darwin":
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    return time.time()


def parse(content, stamp=None):
    """One machine's message: {"machine", "epoch", "leader", "lt"} (lt a datetime or None); None for anything else.
    `stamp`: the message's Discord time (edited, else created). A leader: true message's lt is that time itself (the
    edit is the refresh: no second edit to write it in); a leader: false one carries the old lt in its JSON."""
    try:
        got = json.loads(content or "")
    except ValueError:
        return None
    if not isinstance(got, dict) or not isinstance(got.get("machine"), str) or not isinstance(got.get("epoch"), int) \
            or isinstance(got.get("epoch"), bool):
        return None
    leader = got.get("leader") is True
    down = got.get("down") if isinstance(got.get("down"), str) and got.get("down") else None
    return {"machine": got["machine"], "epoch": got["epoch"], "leader": leader,
            "lt": stamp if leader and stamp is not None else when(got.get("lt")), "at": stamp, "down": down}


def when(text):
    try:
        got = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return got if got.tzinfo else got.replace(tzinfo=datetime.timezone.utc)


def worded(machine, epoch, leader, lt, down=None):
    """A machine's message. `down` (the primary's gate only, its bot crashing: "crashing"): the machine is up but its
    bot cannot run, so a standby holding the place does not fail back to it (drill item 20, manager 11-10). A
    message without it (every older engine's, the bot's own) says nothing of the kind."""
    body = {"machine": machine, "epoch": epoch, "leader": bool(leader),
            "lt": lt.isoformat() if isinstance(lt, datetime.datetime) else None}
    if down:
        body["down"] = down
    return json.dumps(body, ensure_ascii=False)


def fresh(entry, now):
    """`entry` (parse()) holds the place at Discord time `now` (the reader's own newest edit)."""
    return entry["leader"] and entry["lt"] is not None and (now - entry["lt"]).total_seconds() <= FRESH_SECS


def leader_of(entries, now, order=()):
    """The machine that is the bot among `entries`, at Discord time `now`; None when none holds it."""
    holding = [e for e in entries if fresh(e, now)]
    if not holding:
        return None
    rank = {name: i for i, name in enumerate(order)}
    return max(holding, key=lambda e: (e["epoch"], -rank.get(e["machine"], len(rank))))["machine"]


def view(entries, now, me, holder, wall):
    """state/lease-view.json (C-181): what one good read saw, for the status boards on this machine (they post only
    where the bot is right now and never ask Discord themselves). `wall`: this machine's time.time() at the read;
    each machine's "age" is how long before the read its message was last edited, by Discord's times alone (the
    reader adds it to its own clock, never comparing two machines' clocks)."""
    machines = {}
    for e in entries:
        age = (now - e["at"]).total_seconds() if e.get("at") is not None and now is not None else None
        machines[e["machine"]] = dict({"leader": e["leader"], "epoch": e["epoch"], "age": age},
                                      **({"down": e["down"]} if e.get("down") else {}))
    return {"at": wall, "machine": me, "holder": holder,
            "leaders": sorted(e["machine"] for e in entries if now is not None and fresh(e, now)), "machines": machines}


ALIVE_SECS = 150  # a machine's message edited this recently (its bot's rounds, or its supervisor's leader: false
# edit before each start, every minute while it waits): that machine is up (section 5)
FAILBACK_SECS = 30 * 60  # the primary up this long without a break (and reached by ssh): the bot goes back to it


DOWN_CHECK_SECS = 5 * 60  # the primary marked down tries a load check this often while it waits (manager 11-10)


def alive(entry, now):
    """`entry` (parse()) was edited within ALIVE_SECS of Discord time `now`."""
    return entry is not None and entry.get("at") is not None and now is not None and \
        abs((now - entry["at"]).total_seconds()) <= ALIVE_SECS


class Lease(object):
    """This machine's part of the lease, on the bot's Discord connection (the supervisor's own part, before the
    bot connects, is the next step). `channel()` gives #bot-lease (None when there is none: then nothing is held
    back, and the caller says so once); `me` this machine's name; `order` the machine list's names."""

    def __init__(self, me, order, channel, bot_id, clock_=clock, crashing=False, mark=None):
        self.me, self.order, self.channel, self.bot_id, self.clock = me, list(order), channel, bot_id, clock_
        self.epoch, self.lt, self.mine = 0, None, None
        self.read_ok = self.write_ok = None   # clock() of the last good read (its start, finding this machine
        self.false_since = None               # the bot) and of the last good edit of its own message
        self.last_seen = None                 # the clock when last looked at (a clock going back: not ok)
        self.other = None                     # a machine seen holding the place with a bigger epoch
        self.entries, self.now = [], None     # what the last read saw (every machine's message) and its Discord time
        self.reads = 0                        # good reads so far (the bot writes state/lease-view.json after each)
        self.started = clock_()               # (STABLE_SECS counts from here)
        self.crashing = crashing              # started while crashing (DCBOT_CRASHING, the module's note)
        self.restart = None if crashing else False  # crashing and found holding the place by its first read
        self.mark = mark                      # state/lease-true-at (the module's note): written at each leader: true
        self.lt_at = None                     # (started while crashing: clock() at its last leader: true, if known)

    def ok(self):
        now = self.clock()
        if self.last_seen is not None and now < self.last_seen:
            return False  # (the wall clock went back: nothing it says can be trusted)
        self.last_seen = now
        wrote = self.write_ok is not None and now - self.write_ok < WRITE_OK_SECS
        settling = self.restart is True and self.write_ok is None and now - self.started < STABLE_SECS and \
            self.lt_at is not None and now - self.lt_at < FRESH_SECS - SETTLE_MARGIN
        good = self.read_ok is not None and now - self.read_ok < READ_OK_SECS and (wrote or settling)
        if good:
            self.false_since = None
        elif self.false_since is None:
            self.false_since = now
        return good

    def giving_up(self):
        """leader_ok() has been false for YIELD_SECS in a row (section 1.3: give the place up)."""
        self.ok()
        return self.false_since is not None and self.clock() - self.false_since >= YIELD_SECS

    async def messages(self):
        if drill_read_fails():  # (the drill's item 15: a GET of #bot-lease failing, as a proxy blocking it would)
            raise RuntimeError("drill: reading #bot-lease made to fail (DCBOT_DRILL_LEASE_READ_FAIL)")
        ch = self.channel()
        if ch is None:
            return None, []
        got = [m async for m in ch.history(limit=20)]
        return ch, [m for m in got if getattr(m.author, "id", None) == self.bot_id]

    async def write(self, leader):
        """Edit this machine's own message (made when there is none); True when Discord took it. A leader: true
        edit moves lt to that edit's Discord time; a leader: false one keeps the old lt."""
        ch, mine = await self.messages()
        if ch is None:
            return False
        own = next((m for m in mine if (parse(m.content) or {}).get("machine") == self.me), None)
        if own is not None:  # (its epoch as written before: the supervisor's, or this bot's last run)
            was = parse(own.content, getattr(own, "edited_at", None) or getattr(own, "created_at", None))
            self.epoch = max(self.epoch, was["epoch"])
            if self.lt is None:  # (a leader: false edit before this bot's first leader: true one keeps the lt
                self.lt = was["lt"]  # written before, never null: a restart's first two minutes, give_up())
        text = worded(self.me, self.epoch, leader, self.lt)
        before = self.clock()  # (the edit's Discord time is no earlier: lease-true-at errs old, the safe way)
        sent = await own.edit(content=text) if own is not None else await ch.send(text)
        stamp = getattr(sent, "edited_at", None) or getattr(sent, "created_at", None)
        if leader and stamp is not None:
            self.lt = stamp  # (what a later leader: false edit copies)
            wrote_true(self.mark, before)
        self.write_ok = self.clock()
        return True

    async def read(self):
        """Read every machine's message: this machine's place (the bot, or who is). Returns the machine that holds
        it (None: none); `other` is set when another one holds it with a bigger epoch than this machine's."""
        t0 = self.clock()
        ch, mine = await self.messages()
        if ch is None:
            return None
        entries, now = [], None
        for m in mine:
            stamp = getattr(m, "edited_at", None) or getattr(m, "created_at", None)
            e = parse(m.content, stamp)
            if e is None:
                continue
            entries.append(e)
            if e["machine"] == self.me:
                now = stamp if now is None or (stamp is not None and stamp > now) else now  # (its newest: #code-review-2)
                self.epoch = max(self.epoch, e["epoch"])
        if now is None:
            return None
        self.entries, self.now = entries, now
        self.reads += 1
        holder = leader_of(entries, now, self.order)
        self.other = None
        if holder == self.me:
            self.read_ok = t0
        elif holder is not None:
            self.other = holder
        return holder

    def epoch_known(self):
        """Is this machine's epoch known: after its first edit, or (started while crashing, holding the place) its
        first read (its own message read, no edit in its first STABLE_SECS)?"""
        return self.write_ok is not None or self.restart is True

    def stable(self):
        """May this bot's round edit its message, leader: true (the module's note)? Started while crashing: not
        before its first read has said whether it holds the place (a read that failed says nothing: no edit that
        round), and then only once up STABLE_SECS."""
        return self.restart is False or (self.restart is True and self.clock() - self.started >= STABLE_SECS)

    async def round(self):
        """One round of the bot's: its own leader: true edit first (none in a restart's first STABLE_SECS), then
        the read (sections 1.2, 1.3). The very first round reads once before it."""
        if self.restart is None:
            try:
                t0 = self.clock()
                await self.read()
                stamps = [e["at"] for e in self.entries if e.get("at") is not None]
                # holding by the newest edit of any machine, as the gate looks (by its own alone, an lt never moved
                # since is always fresh against itself); no message of its own yet (self.entries left empty): none
                self.restart = bool(stamps) and leader_of(self.entries, max(stamps), self.order) == self.me
                if self.restart:  # (the clock() its lt was written at: the earlier of the two ways, the module's note)
                    own = next(e for e in self.entries if e["machine"] == self.me)
                    seen = t0 - max((max(stamps) - own["lt"]).total_seconds(), 0)
                    kept = true_at(self.mark, self.clock())
                    self.lt_at = seen if kept is None else min(seen, kept)
            except Exception:
                pass
        try:
            if self.stable():
                await self.write(True)
        except Exception:
            pass  # (not ok in two minutes: write_ok stays old)
        try:
            return await self.read()
        except Exception:
            return None


def boot_id():
    """This boot's id (Linux), or None."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip() or None
    except OSError:
        return None


def wrote_true(path, at):
    """state/lease-true-at: clock() `at` of a leader: true edit Discord took, with the boot and the wall clock."""
    if not path:
        return
    try:
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump({"at": at, "wall": time.time(), "boot": boot_id()}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def true_at(path, now):
    """clock() of the last leader: true edit (wrote_true()), when it can be trusted at clock() `now`: the same boot
    (where there is a boot id), not in the future, and the wall clock saying the same time passed within a minute
    (a sleep counted by one and not the other, a clock set: not trusted). None else."""
    try:
        with open(path) as f:
            got = json.load(f)
        at, wall, boot = float(got["at"]), float(got["wall"]), got.get("boot")
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if boot != boot_id() or not 0 <= now - at or abs((time.time() - wall) - (now - at)) > 60:
        return None
    return at


def drill_read_fails():
    """The C-163 drill's item 15 (manager 11-08): DCBOT_DRILL_LEASE_READ_FAIL names a file; while that file is there,
    every read of #bot-lease by this bot fails (the bot's own write reads its message first, so it fails too, as with
    a proxy blocking that GET). Only in the drill's rig (altention.standby.drill_rig, the gate of every drill switch);
    anywhere else ignored, said in #audit by on_ready."""
    path = os.environ.get("DCBOT_DRILL_LEASE_READ_FAIL", "").strip()
    if not path or not os.path.exists(path):
        return False
    try:
        from altention import standby as alt_standby
        return bool(alt_standby.drill_rig())
    except Exception:
        return False


# ------------------------------------------------------------------ before the bot starts (C-163 step 2, section 1.2)
TAKEOVER_WAIT = 15       # after the takeover edit, before the read that decides
CALM_SECS = 30 * 60      # no takeover again this soon after one
DAY_MOST = 3             # takeovers in 24 hours: more stops them (and the owner is told once)


def role_of(me, machines):
    """"primary" (the list's dcbot machine), "standby" (ROLE), or None (no part in the lease)."""
    roles = ((machines or {}).get(me) or {}).get("roles") or []
    return "primary" if "dcbot" in roles else "standby" if ROLE in roles else None


def decide(role, me, entries, now, order):
    """What a supervisor does before starting the bot, from every machine's message (parse()d with its Discord
    time) and `now`, the Discord time of this machine's own leader: false edit just made (it says "alive" only):
    "start", "wait", or (a standby) "takeover". The primary starts unless another machine holds the place (it will
    write leader: true and read again once connected); a standby takes over only when nobody holds it, the
    primary's lt being stale (section 1.1: a leader: false edit never stands in the way)."""
    holder = leader_of(entries, now, order)
    if role == "primary":
        return "start" if holder in (None, me) else "wait"
    if role == "standby":
        return "takeover" if holder is None else "start" if holder == me else "wait"
    return "start"


def members(machines):
    """The machines with a part in the lease: the standbys by name, then the primary last (place N-1): the same order
    on every machine, whatever order each one's list has. The primary last: working one out from the same epochs, it
    gets the biggest, so a primary back while a standby takes over goes on and the standby gives up (no needless
    change of hands, none of the 30-minute and 3-a-day allowance spent: manager 11-08)."""
    standbys = sorted(n for n in (machines or {}) if role_of(n, machines) == "standby")
    return standbys + sorted(n for n in (machines or {}) if role_of(n, machines) == "primary")


def new_epoch(entries, own=0, me=None, machines=None):
    """The epoch of a machine becoming the bot (a takeover, the primary starting while nobody holds the place, a
    failback): bigger than every epoch seen and its own (fencing holds only while epochs only go up: machines that
    saw the last bot's refuse a smaller one for good; #code-review and #code-review-2 on 94f58e5), and the smallest
    such that epoch mod N is this machine's place among members() (N of them): two machines working one out at the
    same moment never get the same one (#code-review on 6f7327b, manager's (a)). While the list changes (a standby
    added or taken away) N changes, and two machines working one out at that very moment could still meet: accepted
    (design section 1.2)."""
    epoch = max([e["epoch"] for e in entries] + [own]) + 1
    names = members(machines)
    if me in names:
        n, place = len(names), names.index(me)
        epoch += (place - epoch) % n
    return epoch


def still_mine(entries, now, me, epoch, primary, order):
    """The read TAKEOVER_WAIT after the takeover edit (section 1.2, fifth version): this machine's message really
    is the new epoch with leader: true (an edit that failed, or a 200 with the old content, is no takeover), the
    primary's lt is still stale (fresh again: given up, whatever the epochs), and this machine holds the place."""
    own = next((e for e in entries if e["machine"] == me), None)
    if own is None or own["epoch"] != epoch or not own["leader"]:
        return False
    if any(e["epoch"] >= epoch for e in entries if e["machine"] != me):
        return False  # another machine wrote a bigger (or the same) epoch meanwhile, leader or not: the primary
        # starting while nobody held the place (#code-review-2 on 6f7327b)
    prim = next((e for e in entries if e["machine"] == primary), None)
    if prim is not None and fresh(prim, now):
        return False
    return leader_of(entries, now, order) == me


def may_take_over(history, now):
    """`history`: the times (wall clock, this machine) of earlier takeovers. None when one may happen now; else why
    not: "calm" (one less than CALM_SECS ago) or "day" (DAY_MOST in 24 hours: no more automatic ones)."""
    recent = [t for t in history if now - t < 86400]
    if len(recent) >= DAY_MOST:
        return "day"
    if recent and now - max(recent) < CALM_SECS:
        return "calm"
    return None


LEAVE_SECS = 2 * FRESH_SECS  # a crashing primary leaves a lapsed place to the standby this long after its lt


def leave_to_standby(entries, now, me, machines):
    """The primary's gate, its bot crashing (down before it was ever up the supervisor's STABLE_SECS) and nobody
    holding the place: leave it to a standby that is up (alive()) instead of taking it with a new epoch, so the
    standby takes over FRESH_SECS after the primary's last stable run (drill item 17, manager 11-09). Only up to
    LEAVE_SECS after its own lt: a standby that does not take it by then (its 30 minutes or 3 a day spent, ssh
    saying the primary is alive) leaves the primary to start as before, never nobody being the bot."""
    own = next((e for e in entries if e["machine"] == me), None)
    if own is None or own.get("lt") is None or now is None or (now - own["lt"]).total_seconds() > LEAVE_SECS:
        return False
    return any(alive(e, now) for e in entries if e["machine"] != me and role_of(e["machine"], machines) == "standby")


def mine_alone(entries, now, me, epoch, order):
    """The read after the primary wrote its new epoch (starting while nobody held the place): no other machine has
    an epoch as big or bigger, and nobody else holds the place; else it waits (#code-review-2 on 6f7327b: the same
    check as a takeover's)."""
    if any(e["epoch"] >= epoch for e in entries if e["machine"] != me):
        return False
    return leader_of(entries, now, order) in (None, me)


def fenced_epoch(path):
    """The epoch in state/fenced (written on this machine by the bot that took over: section 1.4), or None."""
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None
