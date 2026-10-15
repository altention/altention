"""The one place where tests put a fake pane in front of the code that reads the screen and presses keys.

Until 2026-10-11 each test file replaced tmuxio's functions itself (mock.patch.object(tmuxio, …) in 39 places, plain
assignment in three more). Stage 3b moves those functions into the connector: a replacement left pointing at the old
name would then either fail (loud) or, where the old name stays as something that passes the call on, succeed and
do nothing, and the test would go on to the real tmux server of the bot that is running (silent). So:

- WHERE says, once, in which module each replaceable name lives. A function that moves changes one line here.
- replace() refuses a name it does not know, a name the module does not have, and a function the module only
  hands on from somewhere else (imported, not defined there): each of them an AssertionError that names it.
- use() (and patchers()) also stands in front of the real thing: a tmux or ssh process started while it is in force raises Reached
  (not an Exception: the bridge's `except Exception` cannot swallow it), and is said again when use() ends, for
  the ones raised inside a task nobody awaited.

    with fakepane.use(fake):                      # a Screens (or any object) whose methods bear tmuxio's names
    with fakepane.use(raw_screen=f, keys=g):      # single functions
    with fakepane.use(fake, alive=h):             # both
    fakepane.replace("ESC_GAP", 0.1)              # one patcher (mock's), for a constant or a list of patchers
"""
import asyncio
import contextlib
import os
import time
from unittest import mock

from bot import tmuxio
from altention.connectors.claude_code import typist as cc_typing  # noqa: E402
from tests import tmuxguard  # noqa: F401  (in force wherever a fake pane is: no real tmux server is reached)

# The installation's settings (what a conversation is opened with is looked up there since step 2.4b): a test with a
# fake pane reads none, unless it says where its own are. "-" is the engine's word for "this machine has no instance".
os.environ.setdefault("ALT_INSTANCE", "-")

if not tmuxio._set.get("done"):  # a test file run alone, without the bot's bridge (which tells tmuxio from the bot's settings)
    tmuxio.configure(socket="dcbot-test-none")  # a name no server has: whatever gets past the guard reaches nothing

FUNCTIONS = ("tmux", "alive", "start", "kill", "open_window", "kill_pane", "pane_pid", "exists",
             "raw_screen", "color_screen", "screen", "cursor", "in_mode", "vim_mode",
             "keys", "type_text", "escape", "clipboard", "buffers", "take_buffer",
             "claude_panes", "local_claude_panes", "user_sessions", "own_sessions", "started_for", "children")
CONSTANTS = ("ESC_GAP", "TYPE_PAUSE", "TYPE_CHUNK")
WHERE = dict([(name, tmuxio) for name in FUNCTIONS] + [(name, cc_typing) for name in CONSTANTS])  # the module each name
# lives in today (the pace and the Escapes' gap are the connector's since 3b 2.7a-1)

RULE = "─" * 60
REAL = ("tmux", "ssh")  # the programs no test may start while a fake pane is in use


class Reached(BaseException):
    """A test with a fake pane in use started a real tmux or ssh: a replacement did not take."""


def box(row):
    """A screen with the ordinary input box holding `row` (as "❯ text")."""
    return "\n".join(["", RULE, row, RULE, "  ? for shortcuts", ""])


def replace(name, *new, **kw):
    """mock's patcher for one of WHERE's names (as mock.patch.object(tmuxio, name, …) was), after checking that
    the replacement can take."""
    if name not in WHERE:
        raise AssertionError("fakepane: %r is not a name it knows to replace (tests/fakepane.py WHERE)" % name)
    module = WHERE[name]
    if not hasattr(module, name):
        raise AssertionError("fakepane: %s has no %r any more: where did it move? (tests/fakepane.py WHERE)"
                             % (module.__name__, name))
    old = getattr(module, name)
    if any(old is one for one in _ours):
        pass  # a fake of ours is there already (a use() inside a use()): the new one goes over it
    elif name in FUNCTIONS and getattr(old, "__module__", module.__name__) != module.__name__:
        raise AssertionError("fakepane: %s.%s is only handed on from %s: replacing it here would not reach the callers "
                             "there (tests/fakepane.py WHERE)" % (module.__name__, name, old.__module__))
    if new and name in SENT:
        new = (sent(name, new[0]),) + tuple(new[1:])  # (each key and each typing is noted for tests/keylog.py)
    if new:
        _ours.append(new[0])
    return mock.patch.object(module, name, *new, **kw)


SENT = ("keys", "type_text", "escape")  # what reaches the pane: noted when tests/keylog.py records


def sent(name, fake):
    """`fake`, and what it is handed noted (tests/keylog.py, while it records): the name, which kind of target, the
    rest. The same function otherwise."""
    async def noted(t, *args, **kw):
        from tests import keylog
        keylog.note(name, t, args)
        return await fake(t, *args, **kw)
    noted.__wrapped__ = fake
    return noted


_ours = []  # the replacements handed out so far (kept: a fake put over a fake is told by being one of these)


def patchers(fake=None, **named):
    """The patchers use() starts: every method of `fake` bearing one of WHERE's names, each of `named`, and the
    guard. For the tests that keep a list of patchers of their own; use() is the ordinary way."""
    out = []
    for name in FUNCTIONS:
        if fake is not None and hasattr(fake, name) and name not in named:
            out.append(replace(name, getattr(fake, name)))
    for name, new in named.items():
        out.append(replace(name, new))
    out.append(guard())  # also when tmux itself is stood in for: an ssh started directly is still a real one
    return out


reached = []  # what the guard caught, until use() or a test looks


def guard():
    """In front of asyncio's way of starting a process (how tmuxio and remote start tmux and ssh)."""
    real = asyncio.create_subprocess_exec

    async def create_subprocess_exec(program, *args, **kw):
        if os.path.basename(str(program)) in REAL:
            reached.append(" ".join([str(program)] + [str(a) for a in args])[:200])
            raise Reached("a test with a fake pane started a real %s: %s" % (os.path.basename(str(program)), reached[-1]))
        return await real(program, *args, **kw)
    return mock.patch.object(asyncio, "create_subprocess_exec", create_subprocess_exec)


@contextlib.contextmanager
def use(fake=None, **named):
    """The fake pane in force for the block. Raises Reached at the end too when a real tmux or ssh was started in
    between (inside a task whose failure nobody looked at)."""
    del reached[:]
    started = []
    try:
        for p in patchers(fake, **named):
            p.start()
            started.append(p)
        yield fake
    finally:
        for p in reversed(started):
            p.stop()
    if reached:
        caught = list(reached)
        del reached[:]
        raise Reached("a test with a fake pane started a real tmux or ssh: %s" % "; ".join(caught))


class Pane(object):
    """What tmuxio.Pane is, over a fake: the same methods, each handed to the fake's method of that name with the
    target in front (as tmuxio's functions take it). For code that is given a pane and not a target."""

    def __init__(self, fake, t="s"):
        self.fake, self.t = fake, t

    remote = property(lambda self: bool(isinstance(self.t, dict) and self.t.get("host")))
    label = property(lambda self: tmuxio.label(self.t))

    async def alive(self):
        return await self.fake.alive(self.t)

    async def standing(self):
        """As tmuxio.standing(): the fake's own word when it has one, else what its alive() says (never "unknown")."""
        if hasattr(self.fake, "standing"):
            return await self.fake.standing(self.t)
        return "alive" if await self.fake.alive(self.t) else "gone"

    async def raw_screen(self):
        return await self.fake.raw_screen(self.t)

    async def screen(self, lines=0):
        return await self.fake.screen(self.t, lines)

    async def keys(self, *names):
        return await self.fake.keys(self.t, *names)

    async def type_text(self, text):
        return await self.fake.type_text(self.t, text)

    async def cursor(self):
        return await self.fake.cursor(self.t)

    async def color_screen(self):
        return await self.fake.color_screen(self.t)

    async def in_mode(self):
        return await self.fake.in_mode(self.t)

    async def vim_mode(self):
        return await self.fake.vim_mode(self.t)

    async def escape(self, needed):
        return await self.fake.escape(self.t, needed)

    async def close(self):
        return await (self.fake.kill_pane(self.t) if isinstance(self.t, dict) else self.fake.kill(self.t))


class Screens(object):
    """A fake pane: each read shows the next screen (the last one stays); keys and typing are recorded."""

    def __init__(self, screens, color=None, echo=False):
        self.shown, self.color, self.log = list(screens), color, []
        self.echo, self.typed_now = echo, None  # echo: what is typed shows in the box until Enter takes it

    async def raw_screen(self, name):
        if self.echo and self.typed_now is not None:
            self.last = box("❯ " + self.typed_now)
            return self.last
        self.last = self.shown.pop(0) if len(self.shown) > 1 else self.shown[0]
        return self.last

    async def color_screen(self, name):  # the screen last read, with its colours
        return (self.color if self.color is not None else self.last), 160

    async def keys(self, name, *names):
        self.log.extend(("key", n) for n in names)
        if any(tmuxio.key_name(n) == "Escape" for n in names):
            tmuxio._esc_at[tmuxio._pane_key(name)] = time.monotonic()
        if self.echo and "Enter" in names and self.typed_now is not None:
            self.typed_now, self.shown = None, [box("❯ ")]

    async def type_text(self, name, text):
        self.log.append(("typed", text))
        self.typed_now = text

    async def cursor(self, name):
        return None

    async def in_mode(self, name):
        return getattr(self, "scrolling", False)

    async def vim_mode(self, name):
        return getattr(self, "vim", False)

    def patches(self):
        return patchers(self)

    def escapes(self):
        return self.log.count(("key", "Escape"))
