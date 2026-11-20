"""C-201 2c1: /move session and /move tmux (bot/moving.py), over a fake tmux of two servers (the bot's and the owner's)
that counts the Claudes holding each conversation: at no point two (the plan's required test: the new side failing,
the original taken up again)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os  # noqa: E402
import re  # noqa: E402
import tempfile
import types  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from tests import fakepane  # noqa: E402
from tests.test_setup_channel import Ch, bot as make_bot, run  # noqa: E402

from bot import moving, tmux_bridge  # noqa: E402

BOT = "/tmp/tmux-test/dcbot-test"
USER = "/tmp/tmux-test/default"
BOX = "\n".join(["some reply", fakepane.RULE, "❯ ", fakepane.RULE, "  ? for shortcuts"])
BUSY = "\n".join(["some reply", fakepane.RULE, "❯ ", fakepane.RULE, "  ✻ Working… (esc to interrupt)"])


class World(object):
    """Two tmux servers: panes {id: {socket, session, pid, claude: conversation or None, shell, typed}}."""
    def __init__(self):
        self.panes, self.n, self.pid = {}, 0, 4900000
        self.max_same = 0
        self.outcome = {}  # socket -> "ok" | "down" | "other": how the next Claude started there comes up
        self.exit_hangs = self.exit_background = self.busy = False
        self.dropped = False  # another machine's line down: its tmux commands get ssh's 255 (C-201 2c2)
        self.unanswered = False  # alive() of another machine's pane gets no answer
        self.typed_hangs = False  # a Claude typed into an owner's shell does not end on /exit
        self.resumed = []  # what was typed into an owner's shell to take a conversation up again
        self.opened = []  # (socket, session) of every Claude started
        self.windows = []  # the command of every window opened
        self.log = []

    def add(self, socket, session, claude=None, shell=False, window=False):
        self.n += 1
        self.pid += 1
        pid = "@%d" % self.n if window else "%%%d" % self.n
        self.panes[pid] = {"socket": socket, "session": session, "pid": str(self.pid), "claude": claude, "shell": shell,
                           "typed": "", "text": ""}
        self.count()
        return pid

    def count(self):
        held = {}
        for p in self.panes.values():
            if p["claude"]:
                held[p["claude"]] = held.get(p["claude"], 0) + 1
        self.max_same = max([self.max_same] + list(held.values()))

    def find(self, t):
        if isinstance(t, dict):
            p = self.panes.get(t["id"])
            return p if p and (not t.get("pid") or str(t["pid"]) == p["pid"]) else None
        return next((p for p in self.panes.values() if p["socket"] == BOT and p["session"] == t), None)

    def start_claude(self, socket, sid):
        how = self.outcome.get(socket, "ok")
        self.opened.append(socket)
        return None if how == "down" else ("other-conversation" if how == "other" else sid)

    def target(self, socket, t):
        if t.startswith("="):
            name = t[1:].rstrip(":")
            return next((k for k, p in self.panes.items() if p["socket"] == socket and p["session"] == name), None)
        return t if t in self.panes and self.panes[t]["socket"] == socket else None

    async def tmux(self, *args, socket=None, host=None, err=None):
        socket = socket or BOT
        self.log.append(args)
        if host and self.dropped:
            return 255, ""
        a = list(args)
        if a[0] == "display-message":
            k = self.target(socket, a[a.index("-t") + 1])
            if k is None:
                return 1, ""
            p = self.panes[k]
            return 0, a[-1].replace("#{pane_id}", k).replace("#{pane_pid}", p["pid"]).replace("#{session_name}", p["session"]) + "\n"
        if a[0] == "capture-pane":
            k = self.target(socket, a[a.index("-t") + 1])
            if k is None:
                return 1, ""
            p = self.panes[k]
            return 0, ((BUSY if self.busy else BOX) if p["claude"] else "$ ") + p["text"]
        if a[0] == "send-keys":
            k = self.target(socket, a[a.index("-t") + 1])
            p = self.panes.get(k)
            if p is None:
                return 1, ""
            if "-l" in a:
                p["typed"] += a[-1]
                return 0, ""
            typed, p["typed"] = p["typed"], ""
            if self.exit_hangs and self.exit_background and typed == "/exit":  # (a daemon: the process stays on it)
                p["text"] = "\nYour conversation moved to the background"
            elif not typed and a[-1] == "Escape":
                p["text"] = ""
                self.log.append(("esc",))
            elif typed == "/exit" and p["claude"] and not self.exit_hangs and not (self.typed_hangs and p.get("by_line")):
                p["claude"] = None
                if self.exit_background:
                    p["text"] = "\nYour conversation moved to the background"
                if not p["shell"]:
                    del self.panes[k]
            elif "--resume " in typed and p["shell"] and not p["claude"]:
                self.resumed.append(typed)
                p["claude"] = self.start_claude(socket, re.search(r"--resume (\S+)", typed).group(1))
                p["by_line"] = True
            self.count()
            return 0, ""
        if a[0] == "has-session":
            name = a[a.index("-t") + 1][1:]
            return (0 if any(p["socket"] == socket and p["session"] == name for p in self.panes.values()) else 1), ""
        if a[0] == "new-session":
            return 0, self.add(socket, a[a.index("-s") + 1], shell=True, window=True) + "\n"
        if a[0] == "move-window":
            k = self.target(socket, a[a.index("-s") + 1])
            to = a[a.index("-t") + 1][1:].rstrip(":")
            if k is None or not any(p["socket"] == socket and p["session"] == to for p in self.panes.values()):
                if err is not None:
                    err.append("can't find session: %s" % to)
                return 1, ""
            self.panes[k]["session"] = to
            return 0, ""
        if a[0] in ("kill-window", "kill-pane"):
            self.panes.pop(a[a.index("-t") + 1], None)
            self.count()
            return 0, ""
        if a[0] == "kill-session":
            name = a[a.index("-t") + 1][1:]
            for k in [k for k, p in self.panes.items() if p["socket"] == socket and p["session"] == name]:
                del self.panes[k]
            self.count()
            return 0, ""
        return 0, ""

    async def alive(self, t):
        if isinstance(t, dict) and t.get("host"):
            moving.tmuxio.asked(t, answered=not (self.unanswered or self.dropped))
            if self.unanswered or self.dropped:
                return False
        p = self.find(t)
        return bool(p and p["claude"])

    async def start(self, name, cwd, argv, env=None):
        k = self.add(BOT, name)
        self.panes[k]["claude"] = self.start_claude(BOT, argv[-1])
        self.count()
        return True

    async def open_window(self, socket, session, window_name, cwd, argv, env=None, host=None, why=None):
        self.windows.append(list(argv))
        run = argv[2] if len(argv) == 3 and argv[1] == "-c" else None  # (the owner's shell running a line)
        k = self.add(socket, session, shell=bool(run))
        self.panes[k]["cwd"] = cwd
        if run:
            self.resumed.append(run.split(";")[0])
            self.panes[k]["claude"] = self.start_claude(socket, run.split(";")[0].split()[-1])
        else:
            self.panes[k]["claude"] = self.start_claude(socket, argv[-1])
        self.count()
        return dict({"socket": socket, "id": k, "pid": self.panes[k]["pid"]}, **({"host": host} if host else {}))

    async def settled(self, name):
        pass

    async def kill(self, t):
        await self.tmux("kill-session", "-t", "=" + t)

    async def kill_pane(self, pane):
        await self.tmux("kill-pane", "-t", pane["id"], socket=pane["socket"])


class Bridge(object):
    def __init__(self, world):
        self.world, self.rts, self.startup = world, {}, None

    def rt(self, ch):
        return self.rts.setdefault(str(ch.id), tmux_bridge.Runtime())

    async def chosen_for(self, st):
        return "opus", "default"

    def argv(self, ch, st, resume, dirs, how, claude=None):
        self.cwds = getattr(self, "cwds", []) + [st.get("cwd")]
        return [claude or "claude", "--resume", resume]

    def env(self, ch):
        return {}

    async def wait_ready(self, pane, rt, ask=None):
        p = self.world.find(getattr(pane, "t", pane))
        if p and p["claude"]:
            rt.ready_sid = p["claude"]
            rt.ready.set()
            return True
        return False


class Base(unittest.TestCase):
    def setUp(self):
        for name, secs in (("BACK_SECS", 1), ("SID_SECS", 2)):
            q = mock.patch.object(moving, name, secs)
            q.start()
            self.addCleanup(q.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.dict(os.environ, {"HOME": tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.world = World()
        self.routes = []

        async def bind_any(pane, cid, tag=True):
            self.routes.append(("bind", pane["id"], cid))

        async def unbind_any(cid, host=None, pane=None):
            self.routes.append(("unbind", (pane or {}).get("id"), cid))
        for name, f in (("bind_any", bind_any), ("unbind_any", unbind_any)):
            q = mock.patch.object(moving.routes, name, f)
            q.start()
            self.addCleanup(q.stop)
        q = mock.patch.object(moving, "EXIT_SECS", 1.5)
        q.start()
        self.addCleanup(q.stop)
        q = mock.patch.object(moving.cc_health, "binary", lambda data=None: "claude")  # (the Claude the bot runs)
        q.start()
        self.addCleanup(q.stop)

    def bot(self, st):
        _, b = make_bot({"7": st})
        b.tmux = Bridge(self.world)
        b.where_context = lambda: ("lap", BOT)
        b.mode = lambda st: "tmux"
        b.posts = types.SimpleNamespace(pauses=types.SimpleNamespace(of=lambda cid: None))

        async def pane_session_id(pane):
            return None
        b.pane_session_id = pane_session_id
        b.mover = moving.Mover(b)
        b.mover.paths = lambda: (BOT, USER)
        b.mover.daemon_running = lambda: False
        return b, Ch(b.guild, "a", 7)

    def go(self, coro):
        with fakepane.use(self.world):
            return run(coro)


class SessionMove(Base):
    def test_a_bot_session_channel_becomes_a_window_of_the_session_it_moved_to(self):
        k = self.world.add(BOT, "dc-a-7", claude="s1")
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        sent = []

        async def send(text, view=None):
            sent.append(text)
        self.go(b.mover.command(ch, b.channels_state["7"], "session work", send))
        st = b.channels_state["7"]
        self.assertTrue(sent[0].startswith("✅"), sent)
        self.assertNotIn("tmux", st)
        self.assertEqual(st["home"], {"socket": BOT, "session": "work", "window": ""})
        self.assertEqual(st["pane"]["id"], k)
        self.assertEqual(self.world.panes[k]["session"], "work")
        self.assertEqual(self.world.panes[k]["claude"], "s1")  # (never stopped)
        self.assertFalse([p for p in self.world.panes if p.startswith("@")])  # (the session's first window gone)
        self.assertIn("work", st["note_next"])
        self.assertEqual(len(b.audits), 1)

    def test_the_owners_server_needs_the_session_there_first(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        card = self.go(b.mover.move_session(ch, b.channels_state["7"], "nowhere"))
        self.assertTrue(card.startswith("❌"), card)
        self.assertIn("先開好再搬", card)
        self.assertEqual(self.world.panes[k]["session"], "main")
        self.assertEqual(b.channels_state["7"]["pane_session"], "main")

    def test_the_owners_pane_moved_its_session_noted(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        self.world.add(USER, "x", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        card = self.go(b.mover.move_session(ch, b.channels_state["7"], "x"))
        self.assertTrue(card.startswith("✅"), card)
        self.assertEqual(b.channels_state["7"]["pane_session"], "x")
        self.assertEqual(self.world.panes[k]["session"], "x")


class AnotherChannelsSession(Base):
    def test_a_session_found_by_name_for_another_channel_refused_nothing_moved(self):
        a = self.world.add(BOT, "dc-a-7", claude="s1")
        b_pane = self.world.add(BOT, "dc-b-2222", claude="s2")
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        b.channels_state["2222"] = {"tmux": "dc-b-2222", "session_id": "s2"}
        sent = []

        async def send(text, view=None):
            sent.append(text)
        self.go(b.mover.command(ch, b.channels_state["7"], "session dc-b-2222", send))
        self.assertIn("另一個頻道", sent[0])
        self.assertEqual((self.world.panes[a]["session"], self.world.panes[b_pane]["session"]), ("dc-a-7", "dc-b-2222"))
        self.assertFalse([x for x in self.world.log if x[0] == "move-window"])
        self.assertEqual(b.channels_state["7"]["tmux"], "dc-a-7")

    def test_a_name_of_that_form_refused_too(self):
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        self.assertIn("dc-…-四位數", b.mover.refused(ch, b.channels_state["7"], "session", "dc-gone-1234"))
        self.assertIsNone(b.mover.refused(ch, b.channels_state["7"], "session", "dc-work"))


class ResumeLine(unittest.TestCase):
    def test_flags_kept_resume_and_continue_dropped(self):
        self.assertIsNone(moving.resume_line(None, "s1"))
        self.assertEqual(moving.resume_line(["/usr/bin/claude", "-c", "--model", "opus"], "s1"), "/usr/bin/claude --model opus --resume s1")
        self.assertEqual(moving.resume_line(["claude", "--resume=s0", "--session-id", "x", "--fork-session"], "s1"), "claude --resume s1")
        self.assertEqual(moving.resume_line(["claude", "-r", "s0", "--verbose"], "s1"), "claude --verbose --resume s1")

    def test_the_prompt_it_was_started_with_left_out(self):
        """Not sent again as a new turn (#code-review-2 on 4c96c659, manager 11-20)."""
        line = moving.resume_line
        self.assertEqual(line(["claude", "--resume=abc", "-c", "fix the bug"], "S1"), "claude --resume S1")
        self.assertEqual(line(["claude", "做某件事", "--permission-mode", "plan"], "S1"), "claude --permission-mode plan --resume S1")
        self.assertEqual(line(["claude", "--model", "opus", "go on"], "S1"), "claude --model opus --resume S1")
        self.assertEqual(line(["claude", "--add-dir", "/a", "/b", "--verbose", "x"], "S1"), "claude --add-dir /a /b --verbose --resume S1")
        self.assertEqual(line(["claude", "--debug", "hooks", "--", "-x prompt"], "S1"), "claude --debug hooks --resume S1")
        self.assertEqual(line(["node", "/n/claude-code/cli.js", "--effort=high", "hi"], "S1"), "node /n/claude-code/cli.js --effort=high --resume S1")
        self.assertIsNone(line(["claude", "--new-flag", "v"], "S1"), "an option not known: the line not told")
        self.assertEqual(line(["claude", "--permission-mode", "plan", "fix the bug"], "abc"), "claude --permission-mode plan --resume abc")
        self.assertEqual(line(["claude", "-r", "--model", "opus"], "abc"), "claude --model opus --resume abc",
                         "-r with no id: the next option is not eaten (#code-review on 4c96c659)")


class TmuxMove(Base):
    def test_the_owners_move_asks_first(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        sent = []

        async def send(text, view=None):
            sent.append((text, view))
        self.go(b.mover.command(ch, b.channels_state["7"], "tmux user main", send))
        (text, view), = sent
        self.assertTrue(text.startswith("❓"), text)
        self.assertEqual([i.label for i in view.children], ["搬", "取消"])
        self.assertEqual(self.world.opened, [])

    def test_from_the_bots_session_to_the_owners(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main"}))
        st = b.channels_state["7"]
        self.assertNotIn("tmux", st)
        self.assertFalse(st.get("home"), "the owner's own pane now, as /attach makes one (manager 11-20)")
        self.assertEqual(st["pane_session"], "main")
        new = self.world.panes[st["pane"]["id"]]
        self.assertEqual((new["socket"], new["session"], new["claude"], new["shell"]), (USER, "main", "s1", True))
        self.assertEqual(self.world.resumed, ["claude --permission-mode default --resume s1"],
                         "started as the owner starts Claude: no bot settings, never bypassPermissions")
        shell = moving.login_shell()
        self.assertEqual(self.world.windows, [[shell, "-c", "claude --permission-mode default --resume s1; exec %s -l" % shell]],
                         "run by the owner's shell, which stays when Claude ends (not typed: a new shell may ask first)")
        self.assertIn(("bind", st["pane"]["id"], "7"), self.routes)
        self.assertFalse(any(p["socket"] == BOT for p in self.world.panes.values()))
        self.assertEqual(self.world.max_same, 1)
        self.assertEqual(len(b.audits), 1)

    def test_the_owners_pane_to_the_bots_tmux_the_pane_kept(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot"}))
        st = b.channels_state["7"]
        self.assertEqual(st["tmux"], "dc-a-7")
        self.assertNotIn("pane", st)
        self.assertIn(k, self.world.panes)  # (the owner's pane stays, at its shell)
        self.assertIsNone(self.world.panes[k]["claude"])
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertIn(("unbind", k, "7"), self.routes)
        self.assertEqual(self.world.max_same, 1)

    def test_the_new_side_another_conversation_closed_and_the_old_taken_up_again(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        self.world.outcome[USER] = "other"
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        cards = []
        msg = types.SimpleNamespace(edit=None)

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main", "card": msg}))
        st = b.channels_state["7"]
        self.assertEqual(st.get("tmux"), "dc-a-7")
        self.assertNotIn("home", st)
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertFalse(any(p["socket"] == USER and p["claude"] for p in self.world.panes.values()))
        self.assertEqual(self.world.opened, [USER, BOT])
        self.assertEqual(self.world.max_same, 1)
        self.assertTrue(cards[-1].startswith("❌"), cards)
        self.assertIn("另一個對話", cards[-1])
        self.assertIn("原本的位置接回", cards[-1])

    def test_the_owners_pane_taken_up_again_with_its_own_flags(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        self.world.outcome[BOT] = "down"

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        b.mover.claude_line = lambda p: ["claude", "--permission-mode", "plan", "--add-dir", "/d x", "-r", "s0", "do it"]
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot"}))
        self.assertEqual(self.world.resumed, ["claude --permission-mode plan --add-dir '/d x' --resume s1"])
        self.assertEqual(self.world.panes[k]["claude"], "s1")

    def test_its_words_not_known_said_on_the_card(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        self.world.outcome[BOT] = "down"

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        b.mover.claude_line = lambda p: None
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot", "card": msg}))
        self.assertEqual(self.world.resumed, ["claude --permission-mode default --resume s1"])
        self.assertIn("預設的設定", cards[-1])

    def test_the_owners_tmux_never_gets_the_bots_line_nor_a_mode_that_skips_questions(self):
        """Manager 11-20, from the 2c1 drill: a channel moved back to the owner's tmux ran the bot's command line there
        (the bot's --settings, bypassPermissions); so did the line typed back after a failed move."""
        state = os.path.join(moving.config.STATE, "claude-settings.ab.json")
        bots = ["/opt/claude", "--model", "opus", "--settings", state, "--permission-mode", "bypassPermissions",
                "--append-system-prompt", "note", "--resume", "s0"]
        self.assertIsNone(moving.users_options(bots))
        self.assertIsNone(moving.users_options(["claude", "--settings", '{"permissions":{}}']))
        self.assertEqual(moving.user_line(bots, "s1"), ("claude --permission-mode default --resume s1", False))
        self.assertEqual(moving.user_line(bots, "s1", ["--permission-mode", "plan"]),
                         ("claude --permission-mode plan --resume s1", True))
        for own in (["claude", "--dangerously-skip-permissions"], ["claude", "--permission-mode", "bypassPermissions"],
                    ["claude", "--permission-mode=bypassPermissions", "--allow-dangerously-skip-permissions"]):
            line, known = moving.user_line(own, "s1")
            self.assertEqual((line, known), ("claude --permission-mode default --resume s1", True), own)
        self.assertEqual(moving.user_line(["claude", "--settings", "/home/me/my.json", "--model", "x"], "s1")[0],
                         "claude --settings /home/me/my.json --model x --permission-mode default --resume s1",
                         "the owner's own settings file is theirs: kept")

    def test_the_owners_pane_to_the_bot_and_back_its_own_mode_again(self):
        """O user -> bot -> user (manager 11-20): back in the owner's tmux it runs as the owner started it."""
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        b.mover.claude_line = lambda p: ["claude", "--permission-mode", "acceptEdits", "--model", "sonnet", "go on"]
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot"}))
        st = b.channels_state["7"]
        self.assertTrue(st.get("tmux"))
        self.assertEqual(st["user_options"], ["--permission-mode", "acceptEdits", "--model", "sonnet"])
        b.mover.claude_line = lambda p: ["/opt/claude", "--settings", os.path.join(moving.config.STATE, "s.json"),
                                         "--permission-mode", "bypassPermissions", "--resume", "s1"]
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main"}))
        self.assertEqual(self.world.resumed, ["claude --permission-mode acceptEdits --model sonnet --resume s1"])
        self.assertFalse(st.get("home"))
        self.assertEqual(self.world.panes[st["pane"]["id"]]["claude"], "s1")
        self.assertEqual(self.world.max_same, 1)

    def test_the_confirm_card_says_the_bots_mode_and_the_one_it_comes_back_in(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        b.mover.claude_line = lambda p: ["claude", "--permission-mode", "plan"]

        async def chosen_for(st):
            return "opus", "bypassPermissions"
        b.tmux.chosen_for = chosen_for
        sent = []

        async def send(text, view=None):
            sent.append(text)
        self.go(b.mover.command(ch, b.channels_state["7"], "tmux bot", send))
        self.assertIn("搬過去會用機器人的設定和權限模式（不逐一確認）；搬回你的 tmux 時會恢復成 `plan`", sent[0])
        sent.clear()
        b2, ch2 = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        self.go(b2.mover.command(ch2, b2.channels_state["7"], "tmux user main", send))
        self.assertNotIn("搬過去", sent[0], "only the owner's pane going to the bot")

    def test_a_daemon_keeps_the_process_on_that_screen_said_at_once_esc(self):
        k = self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        self.world.exit_hangs = self.world.exit_background = True
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        import time
        t0 = time.time()
        with mock.patch.object(moving, "EXIT_SECS", 6):
            self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main", "card": msg}))
        self.assertLess(time.time() - t0, 3, "not waited out")
        self.assertIn("背景", cards[-1])
        self.assertIn("舊的沒動", cards[-1])
        self.assertEqual(cards[-1].count("舊的沒動"), 1)
        self.assertIn(("esc",), self.world.log)
        self.assertEqual(self.world.opened, [])
        self.assertEqual(self.world.panes[k]["claude"], "s1")

    def test_the_new_side_not_up_and_the_owners_pane_takes_it_up_again(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        self.world.outcome[BOT] = "down"

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot"}))
        st = b.channels_state["7"]
        self.assertEqual(st["pane"]["id"], k)
        self.assertNotIn("tmux", st)
        self.assertEqual(self.world.panes[k]["claude"], "s1")
        self.assertIn(("bind", k, "7"), self.routes)
        self.assertEqual(self.world.max_same, 1)
        self.assertEqual(len(b.audits), 1)
        self.assertNotIn("-#", b.audits[0], "the card's small print is plain words in #audit (2c1 drill 11-20)")
        self.assertIn(moving.say("back_ok")[3:], b.audits[0])

    def test_the_owners_pane_not_taken_up_again_the_card_gives_the_line(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        self.world.outcome[BOT] = self.world.outcome[USER] = "down"

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        b.mover.claude_line = lambda p: ["claude", "--permission-mode", "plan"]
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot", "card": msg}))
        self.assertIn("在你的窗格打 `claude --permission-mode plan --resume s1` 接回", cards[-1])
        self.assertNotIn("下一則訊息會再開", cards[-1], "the bot opens nothing in the owner's pane")

    def test_a_claude_that_does_not_end_nothing_opened(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        self.world.exit_hangs = True
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main", "card": msg}))
        self.assertEqual(self.world.opened, [])
        self.assertEqual(b.channels_state["7"]["tmux"], "dc-a-7")
        self.assertIn("/exit", cards[-1])
        self.assertTrue(cards[-1].startswith("❌"))

    def test_a_claude_moved_to_the_background_nothing_opened(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
        self.world.exit_background = True

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot", "card": msg}))
        self.assertEqual(self.world.opened, [])
        self.assertIn("claude stop s1", cards[-1])
        self.assertNotIn("舊的沒動", cards[-1])
        self.assertIn(("bind", k, "7"), self.routes)  # (its route back: the pane is still the channel's)
        self.assertEqual(b.channels_state["7"]["move_background"]["session"], "s1")

    def test_in_the_background_the_next_message_opens_nothing_until_cleared(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        self.world.exit_background = True  # (the bot's window: closed at /exit, the line seen just before)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        orig = self.world.tmux

        async def tmux(*args, **kw):  # (the window gone with the line on its last screen)
            got = await orig(*args, **kw)
            if args[0] == "capture-pane" and got[0] == 1:
                return 0, "Your conversation moved to the background"
            return got
        self.world.tmux = tmux
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main"}))
        st = b.channels_state["7"]
        self.assertIn("move_background", st)
        self.assertEqual(self.world.opened, [])
        self.assertIn("背景", b.mover.refused(ch, st, "tmux", "user", "main"))
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        with self.assertRaises(tmux_bridge.NotOpened) as e:
            self.go(bridge.ensure(ch, st, None))
        self.assertEqual(e.exception.why, tmux_bridge.NotOpened.BACKGROUND)
        self.assertEqual(self.world.opened, [], "no second Claude on s1")
        sent = []

        async def send(text, view=None):
            sent.append(text)
        self.go(b.mover.command(ch, st, "clear", send))
        self.assertNotIn("move_background", st)
        self.assertTrue(sent[0].startswith("✅"))

    def test_a_daemon_running_nothing_sent(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        b.mover.daemon_running = lambda: True
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main", "card": msg}))
        self.assertIn("daemon", cards[-1])
        self.assertIn("舊的沒動", cards[-1])
        self.assertFalse([a for a in self.world.log if a[0] == "send-keys"])
        self.assertEqual(self.world.opened, [])

    def test_not_at_an_empty_box_nothing_typed(self):
        k = self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        self.world.busy = True
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main"}))
        self.assertEqual(self.world.panes[k]["claude"], "s1")
        self.assertFalse([a for a in self.world.log if a[0] == "send-keys"])
        self.assertEqual(self.world.opened, [])

    def test_the_owners_pane_with_no_shell_under_claude_refused(self):
        k = self.world.add(USER, "main", claude="s1")
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})

        async def shell_under(p):
            return False
        b.mover.shell_under = shell_under
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        self.go(b.mover.go(ch, {"cid": "7", "target": "bot", "card": msg}))
        self.assertIn("shell", cards[-1])
        self.assertEqual(self.world.panes[k]["claude"], "s1")
        self.assertFalse([a for a in self.world.log if a[0] == "send-keys"])


class Refused(Base):
    def test_what_is_refused(self):
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        m, st = b.mover, b.channels_state["7"]
        self.assertIsNone(m.refused(ch, st, "tmux", "user", "main"))
        self.assertIn("已經在", m.refused(ch, st, "tmux", "bot"))
        self.assertIn("session 名字", m.refused(ch, st, "tmux", "user"))
        self.assertIn("用法", m.refused(ch, st, "jump", "x"))
        self.assertIn("用法", m.refused(ch, st, "session", "a:b"))
        for extra, words in (({"bg": 2}, "背景"), ({"subs": {"x": 1}}, "子代理"), ({"move_after": {}}, "已經有一個搬家"),
                             ({"session_id": None}, "還沒有對話")):
            self.assertIn(words, m.refused(ch, dict(st, **extra), "tmux", "user", "main"))
        far = {"pane": {"socket": USER, "id": "%1", "host": "nv"}, "home": {"socket": USER, "session": "m", "host": "nv"},
               "session_id": "s1", "cwd": "/w"}
        self.assertIn("別台", m.refused(ch, far, "tmux", "bot"))
        pi = {"pane": {"socket": USER, "id": "%1", "agent": "pi"}, "session_id": "s1", "cwd": "/w"}
        self.assertIn("還不能搬", m.refused(ch, pi, "session", "x"))
        b.posts.pauses.of = lambda cid: {"until": 1}
        self.assertIn("暫停", m.refused(ch, st, "session", "x"))


class Queued(Base):
    def test_a_turn_running_the_move_waits_for_it(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.world.add(USER, "main", shell=True)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w", "busy": True})
        rt = b.tmux.rt(ch)
        rt.turn = types.SimpleNamespace(pid="p1")
        self.go(b.mover.go(ch, {"cid": "7", "target": "user", "session": "main"}))
        st = b.channels_state["7"]
        self.assertEqual(st["move_after"]["turn"], "p1")
        self.assertEqual(self.world.opened, [])
        st["busy"] = False
        self.assertIsNone(b.mover.after_turn(ch, st, "p2"))

        async def ended():
            task = b.mover.after_turn(ch, st, "p1")
            await task
        self.go(ended())
        self.assertNotIn("move_after", st)
        self.assertEqual(st["pane_session"], "main")

    def test_another_turn_begun_drops_it(self):
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w",
                          "move_after": {"target": "user", "session": "main", "turn": "p1"}})
        self.go(b.mover.new_turn(ch, b.channels_state["7"], "p2"))
        self.assertNotIn("move_after", b.channels_state["7"])
        self.assertEqual(len(b.audits), 1)


class ByAgent(Base):
    def test_a_claude_asks_for_a_session_move_its_card_says_whose(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w"})
        posted = []

        async def deliver(to, text, intent=None, **kw):
            posted.append(text)
        with mock.patch.object(moving.deliver, "deliver", deliver):
            ok, said = self.go(b.mover.by_agent(ch, types.SimpleNamespace(id=9, name="lab"),
                                                {"move_place": "session", "move_target": "work", "why": "整理"}))
        self.assertTrue(ok, said)
        self.assertTrue(posted[0].startswith("✅"))
        self.assertIn("lab", b.audits[0])
        self.assertIn("整理", b.audits[0])

    def test_refused_said_to_the_claude(self):
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w", "bg": 1})
        ok, said = self.go(b.mover.by_agent(ch, None, {"move_place": "tmux", "move_target": "user", "move_session": "main",
                                                       "why": "x"}))
        self.assertFalse(ok)
        self.assertIn("背景", said)
        self.assertEqual(len(b.audits), 1, "a Claude's move refused leaves a line in #audit (manager 11-20)")
        self.assertIn("什麼都沒動", b.audits[0])
        self.assertIn("x", b.audits[0])

    def test_stopped_before_anything_sent_in_audit_for_a_claude_only(self):
        for by, lines in ((None, 0), ("lab", 1)):
            self.setUp()
            k = self.world.add(USER, "main", claude="s1", shell=True)
            pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
            b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": "/w"})
            b.mover.daemon_running = lambda: True

            async def shell_under(p):
                return True
            b.mover.shell_under = shell_under
            self.go(b.mover.go(ch, dict({"cid": "7", "target": "bot"}, **({"by": by, "why": "y"} if by else {}))))
            self.assertEqual(len(b.audits), lines, by)
            if lines:
                self.assertIn("daemon", b.audits[0])
                self.assertIn("什麼都沒動", b.audits[0])
            self.assertEqual(self.world.panes[k]["typed"], "")


if __name__ == "__main__":
    unittest.main()
