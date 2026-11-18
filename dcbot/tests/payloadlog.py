"""What the bot hands Discord, recorded against a baseline (C-172a P1b: every send, edit and delete of the bot's own
messages moves into deliver(), and not one field of what Discord is given may change on the way).

Each scenario drives the bot's own code (Posts, TmuxBridge.post_reply …) against recording channels; what reaches a
channel's send(), a message's edit() and delete() is written down field by field: content, embeds (to_dict()),
files (name and a hash of the bytes), the view's components (type, custom id, label, style, url), silent,
allowed_mentions, and any other keyword by its repr. The baseline, tests/samples/payloads/<batch>.json, is written on
the commit before the batch moves anything; tests/test_payloads_same.py runs the scenarios and compares.

    python -m tests.payloadlog --write p1b1   # the baseline (only on the commit before the move)
    python -m tests.payloadlog --check p1b1   # compare: exit status 1, and what differs, when anything does
"""
import asyncio
import base64
import hashlib
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from altention import testing  # noqa: E402
testing.guard_home()  # run by hand too (--write): a throwaway home, so no setting of this installation's is recorded
from tests import tmuxguard  # noqa: F401,E402  (no scenario reaches a real tmux server)

BASELINE = os.path.join(HERE, "samples", "payloads", "%s.json")


def shown(value):
    """A value as it is recorded: plain values as they are; a discord object by its dict."""
    import discord
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, discord.Embed):
        return {"embed": value.to_dict()}
    if isinstance(value, discord.File):
        value.fp.seek(0)
        return {"file": value.filename, "sha1": hashlib.sha1(value.fp.read()).hexdigest()[:12]}
    if isinstance(value, discord.AllowedMentions):
        return {"allowed_mentions": value.to_dict()}
    if isinstance(value, discord.ui.View):
        return {"view": [[type(c).__name__, getattr(c, "custom_id", None), getattr(c, "label", None),
                          str(getattr(c, "style", None)), getattr(c, "url", None)] for c in value.children]}
    if isinstance(value, (list, tuple)):
        return [shown(v) for v in value]
    if isinstance(value, Sent):  # (a message referred to: a reply's reference, by its id)
        return {"message": value.id}
    return repr(value)


class Sent(object):
    _n = [1000]

    def __init__(self, ch, mid=None):
        Sent._n[0] += 1
        self.channel, self.id = ch, mid if mid is not None else Sent._n[0]

    async def edit(self, *args, **kw):
        self.channel.log.append(["edit", self.channel.name, "m" if self.id else None] + [shown(a) for a in args]
                                + [{k: shown(v) for k, v in sorted(kw.items())}])
        return self

    async def delete(self, *args, **kw):
        self.channel.log.append(["delete", self.channel.name])


class Channel(object):
    """A text channel that writes down what it is handed."""
    def __init__(self, cid, name, log):
        self.id, self.name, self.log, self.category = cid, name, log, None
        self.guild = types.SimpleNamespace(id=1)

    async def send(self, *args, **kw):
        self.log.append(["send", self.name] + [shown(a) for a in args] + [{k: shown(v) for k, v in sorted(kw.items())}])
        return Sent(self)

    def get_partial_message(self, mid):
        return Sent(self, mid)

    async def edit(self, **kw):  # (the channel itself: its topic, its category; not a message)
        self.log.append(["channel.edit", self.name, sorted(kw)])


def posts_world(log):
    from bot import posts
    from tests.test_posts import Bot
    bot = Bot()
    a, b, n = Channel(1, "st-1", log), Channel(2, "st-0", log), Channel(3, "notifications", log)
    bot.chans = {1: a, 2: b, 3: n, 99: Channel(99, "audit", log)}
    bot.guild.text_channels = [a, b, n]
    bot.channels_state = {"1": {"pane": {"id": "%1"}, "talk_limit": 2}, "2": {"tmux": "dc-x", "talk_limit": 2}}
    return bot, posts.Posts(bot), a, b


def run(coro):
    async def go():
        await coro
        for _ in range(5):
            await asyncio.sleep(0)
    asyncio.run(go())


def posts_scenarios():
    """The messages posts.py puts on Discord, one scenario each."""
    out = {}
    from bot import config

    def scenario(name, steps):
        log = []
        bot, p, a, b = posts_world(log)
        mock = __import__("unittest").mock
        with mock.patch.object(config, "OWNER_ID", 42), mock.patch.object(config, "GUILD_ID", 7), \
                mock.patch("time.strftime", lambda fmt, *t: "11-16 07:50"):  # (no real ids; a board's 更新於 the same)
            steps(bot, p, a, b)
        out[name] = log
    payload = base64.b64encode(b"file bytes").decode()
    scenario("card", lambda bot, p, a, b: run(p.handle({"channel": "st-0", "text": "hi", "from_channel": "1", "verified": True})))
    scenario("cards_long", lambda bot, p, a, b: run(p.handle({"channel": "st-0", "text": "x" * 5000, "from_channel": "1",
                                                                "verified": True})))
    scenario("card_file", lambda bot, p, a, b: run(p.handle({"channel": "st-0", "text": "see", "from_channel": "1",
                                                               "verified": True, "file_name": "a.txt", "file_b64": payload})))
    scenario("own", lambda bot, p, a, b: run(p.handle({"channel": "st-1", "text": "mine " * 500, "from_channel": "1",
                                                        "verified": True})))
    scenario("own_file_only", lambda bot, p, a, b: run(p.handle({"channel": "st-1", "text": "", "from_channel": "1",
                                                                  "verified": True, "file_name": "b.png", "file_b64": payload})))
    scenario("own_reserved", lambda bot, p, a, b: run(p.handle({"channel": "st-1", "text": "📨 **來自 #x**\nhi",
                                                                 "from_channel": "1", "verified": True})))
    scenario("program", lambda bot, p, a, b: run(p.handle({"channel": "st-0", "text": "cron says", "from_name": "lap 上的程式（w）",
                                                            "from_host": "lap"})))

    def held(bot, p, a, b):
        for i in range(4):
            run(p.handle({"channel": "st-0", "text": "m%d" % i, "from_channel": "1", "verified": True}))
        run(p.talk_continue(b, a, {"why": "w", "verified": True}))
    scenario("talk_limit", held)

    def board(bot, p, a, b):
        run(p.handle({"channel": "st-0", "board": "看板", "text": "a\n" * 1500, "from_channel": "1", "verified": True}))
        run(p.handle({"channel": "st-0", "board": "看板", "text": "short", "from_channel": "1", "verified": True}))
    scenario("board", board)
    scenario("notify", lambda bot, p, a, b: run(p.handle({"notify": True, "channel": "st-0", "text": "done", "from_name": "cron"})))
    scenario("notify_elsewhere", lambda bot, p, a, b: run(p.handle({"notify": True, "channel": "gone", "text": "done",
                                                                     "from_name": "cron"})))

    def asking(bot, p, a, b):
        import time as _t
        p.from_post[2] = (1, _t.time())
        run(p.asking(b, "9-1", 77))
        run(p.answered(b, "9-1", "計畫已核准"))
    scenario("asking", asking)
    return out


def bridge_scenarios():
    """The final reply, words on the way: tmux_bridge.post_reply and the interim."""
    out = {}
    from bot import tmux_bridge
    from tests.test_turn_end import Host

    def scenario(name, steps):
        log = []
        ch = Channel(5, "c", log)
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        st = bridge.host.chan(ch)
        steps(bridge, ch, st)
        out[name] = log
    scenario("reply", lambda br, ch, st: run(br.post_reply(ch, st, [("the reply", [])], {"foot": "-# Bash×1 · 3 秒"})))
    scenario("reply_long", lambda br, ch, st: run(br.post_reply(ch, st, [("y" * 4500, [])], {"foot": "-# 9 秒"})))
    scenario("reply_parts", lambda br, ch, st: run(br.post_reply(ch, st, [("one", []), ("two", [])], {})))
    scenario("interim", lambda br, ch, st: run(br.interim(ch, br.rt(ch), texts=["on the way", "z" * 2500])))
    return out


def batch2_scenarios():
    """P1b batch 2: question and plan cards, menus, the owner's mention lines, posts waiting to go again, the usage
    limit, pauses, a Claude answering for the owner."""
    import tempfile
    from unittest import mock
    from bot import agentanswer, ask, calls, config, menus, posts, quota, tmux_bridge
    from altention.tests.connectors.claude_code import samples  # (after bot: it puts the engine on the path)
    from tests import fakepane
    from tests.test_turn_end import Host
    out = {}
    tmp = tempfile.mkdtemp(prefix="payloadlog-")

    def scenario(name, steps):
        log = []
        Sent._n[0] = 1000  # (the messages' ids the same whatever ran before)
        ch = Channel(5, "c", log)
        ids = iter(range(1, 10 ** 6))
        with mock.patch.multiple(config, OWNER_ID=42, GUILD_ID=7, STATE=tmp), \
                mock.patch.multiple(ask, ASKS=os.path.join(tmp, name, "asks"), ANSWERS=os.path.join(tmp, name, "answers")), \
                mock.patch("time.time", lambda: 1800000000.0), \
                mock.patch("uuid.uuid4", lambda: types.SimpleNamespace(hex="%032x" % next(ids))):
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            steps(log, ch, bridge, bridge.host.chan(ch))
        out[name] = log

    q1 = {"question": "Which one?", "header": "Pick", "options": [{"label": "A", "description": "a"}, {"label": "B"}]}
    q2 = {"question": "And?", "header": "More", "multiSelect": True, "options": [{"label": "X"}, {"label": "Y"}]}

    def questions(log, ch, br, st):
        a = br.asks
        run(a.post(ch, {"ask_id": "7-1", "questions": [q1, q2]}))
        run(a.post(ch, {"ask_id": "7-2", "questions": [q1]}))
        run(a.typed(ch, "my own"))
        run(a.post_plan(ch, {"ask_id": "7-3", "plan": "1. step\n2. done"}))
        run(a.cancel(ch))
    scenario("ask.questions_plan_cancel", questions)

    def agent(log, ch, br, st):
        ask.save("8-1", {"channel": str(ch.id), "questions": [q1], "answers": {}, "messages": {"0": 900}})
        sender = types.SimpleNamespace(id=7, name="lead")
        run(agentanswer.answer(br, ch, sender, {"answer": "q-8-1", "pick": ["A"], "why": "w"}))
        ask.save("8-2", {"channel": str(ch.id), "kind": "plan", "questions": [{"question": "p"}], "answers": {},
                         "messages": {"0": 901}})
        run(agentanswer.answer(br, ch, sender, {"answer": "q-8-2", "approve": True, "why": "w"}))
    scenario("agentanswer.question_plan", agent)

    def quota_(log, ch, br, st):
        q = quota.Quota(br)
        with open(os.path.join(os.path.dirname(samples.__file__), "screens", "2.1.310", "quota", "armed.txt"),
                  encoding="utf-8") as f:
            armed = f.read()

        async def nothing(*a, **k):
            return None
        br.usage_limited = br.usage_maybe_back = nothing
        run(q.turn_failed(ch, st, {"last_assistant_message": "limit"}, None, armed))
        run(q.quota_resumed(ch, st, {}, None, None))
        run(q.quota_resumed(ch, st, {}, None, None))
        run(q.quota_stale(ch, st, {"message": "press enter"}, None, None))
        run(q.quota_stopped(ch, st, {"message": "stopped"}, None, None))
    scenario("quota.armed_resumed_stale_stopped", quota_)

    def calls_(log, ch, br, st):
        run(calls.call(ch, st, "ask:1", "question", "Which one?", about=900))
        run(calls.done(ch, st, "ask:1"))

        async def where():
            return ch
        run(calls.system(where, "usage", "e1", calls.line("usage_limit"), ["- #c"], channel=5))
        run(calls.system(where, "usage", "e1", calls.line("usage_limit"), ["- #c"], channel=6))
        run(calls.over(where, "usage", "恢復了"))
    scenario("calls.mention_done_system_over", calls_)

    def unsent_(log, ch, br, st):
        u = br.unsent
        run(u.offer(ch, st, "hi", "沒送出", types.SimpleNamespace(id=55), code="claude_code.dialog"))
        run(u.offer(ch, st, "post", "沒送出", posts.Origin(ch, auto=True, sender=6), code="claude_code.dialog"))
        sender = Channel(6, "lead", log)
        br.host.get_channel = lambda cid: sender if cid == 6 else ch
        run(u.say_from(ch, {"from": 6, "text": "post"}, "過期了"))
    scenario("unsent.offer_and_sender", unsent_)

    def menus_(log, ch, br, st):
        first = samples.screen("permission")
        views = [first]

        async def raw_screen(t):
            return views.pop(0) if len(views) > 1 else views[0]
        st["tmux"] = "s"
        with fakepane.use(raw_screen=raw_screen, keys=mock.AsyncMock(), alive=mock.AsyncMock(return_value=True)), \
                mock.patch.object(menus.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(br.host, "audit", mock.AsyncMock(), create=True):
            run(br.menus.post(ch, st, {"message": "Claude needs your permission"}))
            token = next(iter(br.menus.open))
            p = agentanswer.Press(ch, "#lead 的 Claude", "w")
            views[:] = [first, samples.screen("idle-fresh")]
            run(br.menus.act(p, token, "1"))
            run(br.menus.post(ch, st, {"message": "again"}))
            br.menus.open.clear()
            views[:] = [first]
            run(br.menus.look_again(ch, st))
            br.menus.open.clear()
            views[:] = [samples.screen("idle-fresh")]
            run(br.menus.look_again(ch, st))
    scenario("menus.post_press_restore_close", menus_)

    def pauses_(log, ch, br, st):
        from tests.test_posts import Bot
        bot = Bot()
        bot.chans = {5: ch}
        bot.channels_state = {"5": {"tmux": "dc-x"}}
        ps = posts.Posts(bot)
        run(ps.pauses.pause(ch, None, "#lead", {"pause": 1800003600, "why": "usage"}))
        run(ps.pauses.resume(ch))
    scenario("pauses.card_resume", pauses_)

    def more_menus(log, ch, br, st):
        first, trust, panel = samples.screen("permission"), samples.screen("trust-dialog"), samples.screen("model-panel")
        views = [first]

        async def raw_screen(t):
            return views.pop(0) if len(views) > 1 else views[0]
        st["tmux"] = "s"
        with fakepane.use(raw_screen=raw_screen, keys=mock.AsyncMock(), alive=mock.AsyncMock(return_value=True)), \
                mock.patch.object(menus.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(br.host, "audit", mock.AsyncMock(), create=True):
            run(br.menus.post(ch, st, {"message": "Claude needs your permission"}))
            token = next(iter(br.menus.open))
            views[:] = [trust]  # (another dialog stands there now: changed(), nothing pressed)
            run(br.menus.act(agentanswer.Press(ch, "#lead 的 Claude", "w"), token, "1"))
            br.menus.open.clear()
            views[:] = [panel]
            run(br.menus.show_panel(ch, "s", lambda body, view=None: ch.send(body, view=view), "/model"))
            token = next(iter(br.menus.open))
            views[:] = [panel, panel, samples.screen("idle-fresh")]
            run(br.menus.act(agentanswer.Press(ch, "#lead 的 Claude", "w"), token, "kDown"))
    scenario("menus.changed_and_panel", more_menus)

    def more_unsent(log, ch, br, st):
        u = br.unsent
        run(u.offer(ch, st, "post", "沒送出", posts.Origin(ch, auto=True, sender=6), code="claude_code.dialog"))
        token = next(iter(u.kept(st)))
        st["unsent"][token]["msg"] = 950

        async def submit(message, text, **kw):
            return None
        br.submit = submit
        with fakepane.use(raw_screen=mock.AsyncMock(return_value=samples.screen("idle-fresh")),
                          alive=mock.AsyncMock(return_value=True)):
            st["tmux"] = "s"
            run(u.hand_again(ch, st))  # (handed: its notice loses its button, a line under it says so)
        run(u.offer(ch, st, "hi", "沒送出", types.SimpleNamespace(id=55), code="claude_code.dialog"))
        token = next(iter(u.kept(st)))

        class Response(object):
            async def edit_message(self, **kw):
                pass
        pressed = types.SimpleNamespace(type=__import__("discord").InteractionType.component,
                                        data={"custom_id": "resend:%d:%s" % (ch.id, token)},
                                        user=types.SimpleNamespace(id=42), channel=ch, response=Response(),
                                        message=Sent(ch, 960))
        pressed.message.content = "沒送出"
        run(u.on_interaction(pressed))  # (sent: its notice says so)
        run(u.offer(ch, st, "again", "沒送出", types.SimpleNamespace(id=56), code="claude_code.dialog"))
        token = next(iter(u.kept(st)))

        async def failing(message, text, **kw):
            raise RuntimeError("tmux gone")
        br.submit = failing
        pressed.data = {"custom_id": "resend:%d:%s" % (ch.id, token)}
        try:
            run(u.on_interaction(pressed))  # (failed on the way: a new notice with its button)
        except RuntimeError:
            pass
    scenario("unsent.handed_pressed_failed", more_unsent)

    def typed_not_taken(log, ch, br, st):
        a = br.asks
        run(a.post(ch, {"ask_id": "9-1", "questions": [q1]}))

        async def taken_elsewhere(*args, **kw):
            return False
        a.settle = taken_elsewhere
        run(a.typed(ch, "late words"))
    scenario("ask.typed_not_taken", typed_not_taken)

    def pin(log, ch, br, st):
        from bot import cc_health
        card = {"version": "9.9.8", "reminded": 5, "card": [5, 970], "path": "/x"}
        watch = types.SimpleNamespace(pin_choices=lambda pinned=None: ("繼續停 7 天", "改用新版的簡易模式"))

        async def choose(keep, by=None, why=None, mark=""):
            return "好，這台繼續用 Claude Code 9.9.8，7 天後再問你。\n-# 由 %s 回答。" % by
        watch.choose_pin = choose
        br.cc = watch
        with mock.patch.object(cc_health, "pin_asking", lambda data=None: card), \
                mock.patch.object(cc_health, "pin_days", lambda: 7):
            run(agentanswer.answer(br, ch, types.SimpleNamespace(id=7, name="lead"),
                                   {"answer": "p-ccpin", "pick": ["繼續停 7 天"], "why": "w"}))
    scenario("agentanswer.pin", pin)
    return out


def batch3_scenarios(part="a"):
    """`part` "b": batch 3b's, the messages of main.py, archive.py and process.py (p1b3b). Otherwise P1b batch 3a: the bot's own messages in the smaller modules (sources, screen text, catch-up, history,
    secrets, Activity, the setup card, the Claude Code pin reminder, update cards, the question before Claude Code
    starts, terminal channels, agents' status lines and threads, the file browser, and the rest of tmux_bridge's:
    /btw, the confirmation, another conversation's words, a turn that failed)."""
    import datetime
    import tempfile
    from unittest import mock
    from bot import config
    from tests.test_turn_end import Host
    out = {}
    tmp = tempfile.mkdtemp(prefix="payloadlog-")

    def run_value(coro):
        got = []

        async def go():
            got.append(await coro)
        run(go())
        return got[0]

    def scenario(name, steps):
        if (name.split(".")[0] in ("main", "archive", "process")) != (part == "b"):
            return  # (batch 3b's three modules: their own baseline, p1b3b)
        log = []
        ch = Channel(5, "c", log)
        Sent._n[0] = 1000  # (a message referred to by its id: the same in every run of a scenario)
        noise = iter(range(1, 10 ** 6))
        with mock.patch.multiple(config, OWNER_ID=42, GUILD_ID=7, STATE=tmp), \
                mock.patch("time.strftime", lambda fmt, *t: "11-16 07:50"), \
                mock.patch("time.time", lambda: 1800000000.0), \
                mock.patch("os.urandom", lambda n: next(noise).to_bytes(n, "big")):  # (a button's own custom id)
            steps(log, ch, Host(ch))
        out[name] = log

    def sources_(log, ch, host):
        from bot import sources
        folder = os.path.join(tmp, "sources")
        msg = types.SimpleNamespace(id=900)
        getmtime = mock.patch("os.path.getmtime", lambda p: 1800000000.0)  # (the clock is fixed: the file as new)
        getmtime.start()
        sources.remember(msg, ch.id, [{"kind": "圖", "lang": "python", "tool": "matplotlib", "source": "plot()"},
                                      {"kind": "圖", "lang": "dot", "tool": "graphviz", "source": "x" * 3000}], 2,
                         folder=folder)
        host.get_channel = lambda cid: ch
        try:
            with mock.patch.object(sources, "FOLDER", folder):
                run(sources.show(host, types.SimpleNamespace(message_id=900, channel_id=ch.id)))
        finally:
            getmtime.stop()
    scenario("sources.show", sources_)

    def screens_(log, ch, host):
        from bot import screens
        host.get_channel = lambda cid: ch
        host.channels_state = {"5": {"screens": {"901": "line\n" * 900}}}
        run(screens.show_text(host, types.SimpleNamespace(message_id=901, channel_id=5)))
    scenario("screens.show_text", screens_)

    def catchup_(log, ch, host):
        from bot import catchup
        now = datetime.datetime(2027, 1, 15, 8, 0, tzinfo=datetime.timezone.utc)

        class Msg(object):
            def __init__(self, mid, age, content="hello"):
                self.id, self.channel, self.content = mid, ch, content
                self.author = types.SimpleNamespace(id=42, bot=False)
                self.created_at = now - datetime.timedelta(seconds=age)
                self.jump_url = "https://discord.com/x/%d" % mid
        old = [Msg(11, 99999), Msg(12, 99998)]
        fresh = [Msg(13, 10), Msg(14, 5, content="!keys C-c")]

        def history(after=None, limit=None, oldest_first=True):
            async def gen():
                for m in old + fresh:
                    yield m
            return gen()
        ch.history = history
        host.get_channel = lambda cid: ch

        async def on_message(m):
            pass
        host.on_message = on_message

        class Now(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        with mock.patch.object(catchup, "SEEN", os.path.join(tmp, "seen.json")), \
                mock.patch.object(catchup.datetime, "datetime", Now):
            c = catchup.Catchup(host)
            host.c = c
            run(c.deliver({"5": 10}))
    scenario("catchup.deliver", catchup_)

    def history_(log, ch, host):
        from bot import history
        path = os.path.join(tmp, "s.jsonl")
        with open(path, "w") as f:
            for i in range(3):
                f.write(json.dumps({"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-10-01T01:00:00Z",
                                    "message": {"content": "question %d" % i}}, separators=(",", ":")) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "answer %d" % i}]}}, separators=(",", ":")) + "\n")

        async def backfill(c, messages):
            pass
        host.backfill = backfill
        st = {}
        run(history.show(host, ch, st, "aaaaaaaa-1", path))
        run(history.show(host, ch, st, "aaaaaaaa-1", path))  # (shown before: its last turns)
        st = host.chan(ch)
        st["session_id"] = "bbbbbbbb-2"

        async def unreachable(*a, **kw):
            return 255, ""
        remote = types.SimpleNamespace(dcremote=unreachable)
        run(history._show_remote(host, ch, st, "lap", "bbbbbbbb-2", "/x.jsonl", False, remote))
        turns = [["human", "q %d" % i, "a %d" % i, "2026-10-01T01:00:00Z"] for i in range(4)]

        async def answered(*a, **kw):
            return 0, json.dumps({"turns": turns})
        st["shown"] = ["bbbbbbbb-2"]
        run(history._show_remote(host, ch, st, "lap", "bbbbbbbb-2", "/x.jsonl", False,
                                 types.SimpleNamespace(dcremote=answered)))
    scenario("history.show_and_remote", history_)

    def secrets_(log, ch, host):
        from bot import secretbox
        import discord
        s = secretbox.Secrets(host)

        async def declared(plugin, name):
            return "API token", None
        tokens = iter(["%032x" % 1, "%032x" % 2])
        with mock.patch.object(secretbox, "declared_now", declared), \
                mock.patch("secrets.token_hex", lambda n=None: next(tokens)):
            run(s.post(ch, "please fill " + "y" * 2500, {"secret": "demo/api-token"}))
            run(s.post(ch, "", {"secret": "demo/api-token"},
                       embeds_of=lambda text: [discord.Embed(description=text[:50]), discord.Embed(description="2")]))

            class Followup(object):
                async def send(self, *a, **kw):
                    pass

            class Response(object):
                async def defer(self, **kw):
                    pass
            token = "%032x" % 2

            async def store(plugin, name, value):
                return True, "存好了"
            with mock.patch.object(secretbox, "store", store), mock.patch.object(secretbox, "typed", lambda d: "v"):
                i = types.SimpleNamespace(user=types.SimpleNamespace(id=42), channel=ch, response=Response(),
                                          followup=Followup(), message=Sent(ch, 910), data={})
                run(s.sent(i, token))
    scenario("secretbox.post_and_filled", secrets_)

    def activity_(log, ch, host):
        from bot import activity
        import discord
        a = activity.Activity(host)

        async def pages_now(rows=None):
            return {("demo", "board"): {"title": "Demo 看板"}}
        tokens = iter(["%032x" % n for n in (11, 12, 13)])
        with mock.patch.object(activity, "pages_now", pages_now), \
                mock.patch("secrets.token_hex", lambda n=None: next(tokens)):
            run(a.post(ch, "z" * 2500, {"activity": "demo/board"}))
            run(a.post(ch, "", {"activity": "demo/board"}))
            run(a.post(ch, "", {"activity": "demo/board"},
                       embeds=[discord.Embed(description="1"), discord.Embed(description="2")]))
    scenario("activity.post", activity_)

    def setup_(log, ch, host):
        from bot import setup
        s = setup.Setup(host)
        host.channels_state["5"] = {"cwd": "/w"}
        run(s.offer(ch, ("m", "first")))
        run(s.offer(ch))  # (a card is up already: nothing)

        async def left(c, rec, cwd):
            return False
        s.left = left
        st = host.chan(ch)
        st["kind"] = "terminal"

        async def ensure(c, st):
            raise RuntimeError("no shell")
        host.terminal = types.SimpleNamespace(ensure=ensure)
        run(s.done(ch, st, "終端機"))
    scenario("setup.offer_done", setup_)

    def pin_(log, ch, host):
        from bot import calls, cc_health, cc_watch

        async def where():
            return ch
        w = cc_watch.Watch(types.SimpleNamespace(notify_channel=where), probe=None, started=1)
        with mock.patch.object(calls, "mention", lambda: "<@42>"), \
                mock.patch.object(cc_health, "pin_card", lambda cid, mid: None):
            run(w.remind({"version": "9.9.8"}, 7))
    scenario("cc_watch.remind", pin_)

    class Opened(Sent):
        """A message a thread can be opened from (the thread writes down what it is handed as a channel does)."""
        async def create_thread(self, name, auto_archive_duration=60):
            self.channel.log.append(["create_thread", self.channel.name, name])
            thread = Channel(800, "thread", self.channel.log)
            self.channel.threads[thread.id] = thread
            return thread

    def threaded(ch):
        async def send(*args, **kw):
            await Channel.send(ch, *args, **kw)
            return Opened(ch)
        ch.send, ch.threads = send, {}
        return ch

    def update_card(log, ch, host):
        from bot import update_cards
        go = {"id": "go", "label": "更新", "style": "primary", "action": "update_go", "arg": "v0.3.0"}
        later = {"id": "later", "label": "之後再說", "action": "later"}
        more = {"id": "more", "label": "看要做什麼", "action": "details"}
        ask_ = {"id": "ask", "label": "請 Claude 幫忙", "style": "danger", "action": "ask_claude", "arg": "請讀 /x/y.md"}
        host.get_guild = lambda gid: types.SimpleNamespace(text_channels=[])
        cards = update_cards.Cards(host)
        with mock.patch.object(update_cards, "FOLDER", os.path.join(tmp, "update-cards")):
            update_cards.save("u1", {"channel": 5})
            run(cards.show({"card": {"id": "u1", "text": "🆕 有新版", "buttons": [go, later, more, ask_],
                                     "details": "先做 A\n再做 B"}}))
            run(cards.show({"card": {"id": "u1", "text": "🆕 有新版（改過）", "buttons": [go, later, more, ask_]}}))
            card = update_cards.load("u1")
            run(cards.do_details(None, "u1", card, more))
            card["details"] = "很長的說明\n" * 400
            run(cards.do_details(None, "u1", card, more))
            run(cards.do_ask_claude(None, "u1", card, ask_))
    scenario("update_cards.show_details_ask", update_card)

    def startup_(log, ch, host):
        from altention.connectors.claude_code import screen
        from altention.tests.connectors.claude_code import samples
        from bot import startup_ask, tmux_bridge
        import discord

        def real(state):
            with open(os.path.join(samples.HERE, "2.1.303", "extra", "I", state + ".txt"), encoding="utf-8") as f:
                return f.read()
        br = tmux_bridge.TmuxBridge(host)
        s = br.startup
        dialog = real("imports-colour-120x50")
        pane = types.SimpleNamespace(t={"socket": "/x", "id": "%1"})
        token = run_value(s.post(ch, pane, screen.startup_choice(dialog)))
        run(s.done(token, "對話開起來了"))
        token = run_value(s.post(ch, pane, screen.startup_choice(dialog)))
        run(s.done(token, ""))
        audit = mock.AsyncMock()
        host.audit = audit
        for views in ([dialog], [samples.screen("idle-fresh")]):
            token = run_value(s.post(ch, pane, screen.startup_choice(dialog)))

            class Response(object):
                async def defer(self):
                    pass
            click = types.SimpleNamespace(type=discord.InteractionType.component, data={"custom_id": "sta:%s:1" % token},
                                          user=types.SimpleNamespace(id=42), channel=ch, response=Response())
            fake = types.SimpleNamespace(raw_screen=mock.AsyncMock(side_effect=views + views), keys=mock.AsyncMock())
            with mock.patch.object(startup_ask.tmuxio, "pane", lambda t, fake=fake: fake):
                run(s.on_interaction(click))
    scenario("startup_ask.post_done_pressed", startup_)

    def terminal_(log, ch, host):
        from bot import terminal
        t = terminal.Terminal(host)
        pane = {"id": "%1", "socket": "/x"}

        async def ensure(c, st):
            return pane
        t.ensure = ensure
        nothing = mock.AsyncMock(return_value="")
        codes = iter([[], ["0"]])

        async def code_list(st, done):
            return next(codes, ["0"])
        t.codes = code_list
        watched = []
        with mock.patch.object(terminal.tmuxio, "tmux", nothing), \
                mock.patch.object(terminal, "cursor_line", mock.AsyncMock(return_value=3)), \
                mock.patch.object(terminal.asyncio, "ensure_future", lambda c: watched.append(c)):
            run(t.run(ch, "seq 1 3000"))
        with mock.patch.object(terminal, "screen_since", mock.AsyncMock(return_value="1\n2\n" * 2000 + "$ ")), \
                mock.patch.object(terminal.tmuxio, "exists", mock.AsyncMock(return_value=True)), \
                mock.patch.object(terminal.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch("time.time", lambda: 1800000004.0):
            run(watched[0])

        async def broken(c, st):
            raise RuntimeError("no shell")
        t.ensure = broken
        t.running.clear()
        run(t.run(ch, "ls"))
    scenario("terminal.run_watch_failed", terminal_)

    def agents_(log, ch, host):
        from bot import agents
        threaded(ch)
        host.get_channel = lambda cid: ch.threads.get(cid, ch)
        a = agents.Agents(host)
        st = host.chan(ch)
        ev = {"agent_id": "a1", "host": "lap", "agent_type": "Explore", "agent_meta": {"description": "找檔案"}}
        run(a.start(ch, st, ev))
        room = st["rooms"]["a:a1"]
        a.add(ch, "a:a1", "Read x.py")
        a.add(ch, "a:a1", "z" * 2500)
        run(a.flush(ch, "a:a1", room, everything=True))
        run(a.restatus(ch, room, agents.agent_status(st["subs"]["a1"], done=True, now=1800000009.0)))
        run(a.start(ch, st, dict(ev, agent_id="a2", agent_run="r1")))
        st["rooms"]["wf:r1"]["title"] = "整理"
        run(a.retitle(ch, st["rooms"]["wf:r1"]))
        run(a.close(ch, st, "wf:r1"))
    scenario("agents.room_thread_status", agents_)

    def files_(log, ch, host):
        from bot import files
        import discord
        f = files.Files(host)
        host.files = f
        host.channels_state["5"] = {"cwd": "/w", "files": {"host": None, "root": "/w"}}

        async def render(b):
            return "📂 `%s`" % b.get("path"), [["button", "up", "⬆️", False, 0], ["button", "close", "關掉", False, 0],
                                               ["select", "open", "打開…", [["a.py", "a.py", None]], 1]], None

        async def call(host_, req):
            return {"found": [[p, "/w/" + p, False] for p in req.get("paths") or []]}
        f.render = render
        with mock.patch.object(files, "call", call):
            run(f.start_channel(ch))
            run(f.typed(ch, "some words"))  # (a new browser at the bottom, the old one deleted)
            browser = host.channels_state["5"]["files"]["browser"]

            class Response(object):
                async def defer(self, **kw):
                    pass
            msg = Sent(ch, browser)
            click = types.SimpleNamespace(type=discord.InteractionType.component, data={"custom_id": "fb:close"},
                                          user=types.SimpleNamespace(id=42), channel=ch, response=Response(),
                                          message=msg)
            run(f.on_interaction(click))
            run(f.add_links(ch, host.chan(ch), Sent(ch, 990), "see bot/a.py:12 and b.md"))
    scenario("files.browser_links", files_)

    def bridge_(log, ch, host):
        from bot import tmux_bridge
        from altention.connectors.claude_code import hooks as cc_hooks
        threaded(ch)
        br = tmux_bridge.TmuxBridge(host)
        asked = []

        async def btw_ask(c, thread, question):
            asked.append(question)
        br.btw_ask = btw_ask

        async def out(text):
            await ch.send(text)
        run(br.btw_open(ch, "what is x?", out=out))
        run(br.confirm(types.SimpleNamespace(channel=ch), "/exit now"))
        run(br.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "abc", "prompt": '<cross-session-message from-name="st-1">hi from st-1</cross-session-message>',
                       "dc_from": cc_hooks.PEER, "dc_peer": "st-1", "dc_text": "hi from st-1"}))
        run(br.failed(ch, {"dc_why": "overloaded_error", "error": "529", "last_assistant_message": "Overloaded"}, None))
    scenario("tmux_bridge.btw_confirm_peer_failed", bridge_)

    def process_show(log, ch, host):
        from bot import process
        opened = threaded(ch)

        async def fetch_message(mid):
            m = Opened(ch, mid)
            m.content = "the reply"
            return m
        ch.fetch_message = fetch_message
        host.get_channel = lambda cid: ch.threads.get(cid) if cid in ch.threads else (ch if cid == 5 else None)
        st = host.chan(ch)
        st["turns"] = {"901": ["/x.jsonl", 0, 9, None], "902": ["/x.jsonl", 0, 9, 800], "903": ["/x.jsonl", 0, 9, None],
                       "904": ["/x.jsonl", 0, 9, None]}
        st["turn_hosts"] = {"903": "lap"}
        p = lambda mid: types.SimpleNamespace(channel_id=5, message_id=mid, guild_id=7)  # noqa: E731
        run(process.show(host, p(900)))  # (no record)
        ch.threads[800] = Channel(800, "thread", log)
        run(process.show(host, p(902)))  # (its thread is there)
        with mock.patch.object(process, "remote_pieces", mock.AsyncMock(return_value=(None, process.UNREACHABLE))):
            run(process.show(host, p(903)))
        with mock.patch.object(process, "pieces_of", lambda st, mid: None):
            run(process.show(host, p(904)))
        pieces = ["🧑 **你說的**\ndo it"] + ["🔧 Bash `ls`\n" + "o" * 1500 for _ in range(process.MAX_MESSAGES + 3)]
        with mock.patch.object(process, "pieces_of", lambda st, mid: pieces):
            run(process.show(host, p(901)))
        del opened
    scenario("process.show", process_show)

    def process_expand(log, ch, host):
        from bot import process
        thread = Channel(800, "thread", log)

        async def fetch_message(mid):
            return types.SimpleNamespace(content="cut " + process.MORE % 10)
        thread.fetch_message = fetch_message
        st = host.chan(ch)
        st["turns"] = {"901": ["/x.jsonl", 0, 9, 800]}
        st["turn_hosts"] = {"901": "lap"}
        st["more"] = {"950": [901, 1, "text", 3000, 0], "951": [901, 1, "text", 3000, 960], "952": [999, 1, "text", 0, 0],
                      "953": [901, 1, "text", 3000, 0]}
        p = lambda mid: types.SimpleNamespace(channel_id=800, message_id=mid, guild_id=7)  # noqa: E731
        run(process.expand(host, p(940), thread, st))  # (not remembered)
        run(process.expand(host, p(951), thread, st))  # (gone on with already)
        process.EXPANDING.add((800, "953"))
        try:
            run(process.expand(host, p(953), thread, st))  # (on its way)
        finally:
            process.EXPANDING.discard((800, "953"))
        run(process.expand(host, p(952), thread, st))  # (its turn gone)
        with mock.patch.object(process, "remote_part", mock.AsyncMock(return_value=(None, process.BEHIND))):
            run(process.expand(host, p(950), thread, st))
        got = {"text": "line\n" * 900, "total": 9000, "next": 7500, "of": {"kind": "said", "last": True}}
        with mock.patch.object(process, "remote_part", mock.AsyncMock(return_value=(got, None))):
            run(process.expand(host, p(950), thread, st))
    scenario("process.expand", process_expand)

    def archive_(log, ch, host):
        from bot import archive
        import discord
        a = archive.Archive(host)
        host.channels_state["5"] = {"archived": {"cwd": "/w", "kind": "plain"}}
        run(a.offer(ch))  # (not a conversation: restore)
        ch2 = Channel(6, "d", log)
        host.channels_state["6"] = {"archived": {"cwd": "/w", "session_id": "s1", "pane": {"id": "%1"}}}
        run(a.offer(ch2))
        ch2.category = types.SimpleNamespace(name=config.ARCHIVE_CATEGORY)

        async def refused(*a_, **k):
            raise RuntimeError("full")
        with mock.patch.object(archive, "is_shelf", lambda c: True), \
                mock.patch.object(archive.traceback, "print_exc", lambda: None):  # (Discord refused it: said in the log)
            a.unarchive = refused
            run(a.leave(ch2, {}, "/w"))
        cat = types.SimpleNamespace(name="工作")

        async def moved(*a_, **k):
            return cat
        a.unarchive = moved

        class Response(object):
            async def edit_message(self, **kw):
                pass

            async def send_message(self, *a_, **kw):
                pass
        click = lambda c, what: types.SimpleNamespace(  # noqa: E731
            type=discord.InteractionType.component, data={"custom_id": "arch:%d:%s" % (c.id, what)},
            user=types.SimpleNamespace(id=42), channel=c, response=Response(), followup=None)
        run(a.on_interaction(click(ch, "restore")))

        async def put(c, force=False):
            return "📦 已封存。", None
        a.put = put
        run(a.on_interaction(click(ch, "force")))
        tmux = types.SimpleNamespace(start_now=mock.AsyncMock(return_value=True))
        host.tmux = tmux
        with mock.patch.object(archive.tmuxio, "standing", mock.AsyncMock(return_value="alive")), \
                mock.patch.object(archive.tmuxio, "label", lambda pane: "tmux s:0"), \
                mock.patch.object(archive.routes, "bind_any", mock.AsyncMock()):
            run(a.back(click(ch2, "back"), ch2, {"cwd": "/w", "session_id": "s1", "pane": {"id": "%1"}}))
        run(a.back(click(ch2, "back"), ch2, {"cwd": "/w"}))
        run(a.back(click(ch2, "back"), ch2, {"cwd": "/w", "session_id": "s1", "tmux": "dc-d"}))
        with mock.patch.object(archive.tmuxio, "user_socket", lambda: "/sock"):
            run(a.back(click(ch2, "back"), ch2, {"cwd": "/w", "session_id": "s1", "pane_session": "work"}))
    scenario("archive.offer_restore_back", archive_)

    def a_bot(ch):
        """main's Bot as tests make it (no Discord connection), its channels file in the scenario's folder."""
        from bot import main as botmain
        with mock.patch.object(botmain, "CHANNELS", os.path.join(tmp, "channels.json")), \
                mock.patch.object(botmain.Bot, "save", lambda self: None):
            bot = botmain.Bot()
        bot.save = lambda: None
        bot.audit = mock.AsyncMock()
        bot.get_channel = lambda cid: ch if cid == ch.id else None
        return botmain, bot

    def main_ready(log, ch, host):
        botmain, bot = a_bot(ch)
        flag, notice = os.path.join(tmp, "restart-requested"), os.path.join(tmp, "notice.txt")
        with open(flag, "w") as f:
            json.dump({"channel": 5}, f)
        with open(notice, "w") as f:
            f.write("更新好了")
        bot.pumping = True
        bot.get_guild = lambda gid: None
        with mock.patch.object(botmain, "RESTART_FLAG", flag), mock.patch.object(botmain, "NOTICE", notice), \
                mock.patch.object(botmain, "git", lambda *a: "abc1234"), \
                mock.patch.object(botmain, "version_note", lambda: "test"), \
                mock.patch.object(botmain, "client_options", lambda: {}):
            try:
                run(bot.on_ready())
            except Exception:
                pass  # (what comes after the line on_ready says in the channel is not this scenario's)
        bot.audit = botmain.Bot.audit.__get__(bot)
        bot.audit_ch = ch
        with mock.patch("builtins.print"):  # (audit() prints each line too)
            run(bot.audit("一行\n" + "長" * 2500))
    scenario("main.ready_and_audit", main_ready)

    def main_failback(log, ch, host):
        from tests import test_failback as tf
        for setting, push in (("ask", 0), ("auto", 0)):
            t = tf.Bot("test_auto_fails_back")
            t.setUp()
            try:
                b = t.make()

                async def notify_channel():
                    return ch
                b.tmux.notify_channel = notify_channel
                t.alive(b, 10)
                with mock.patch.object(tf.main.traceback, "print_exc", lambda: None):
                    t.run_round(b, setting=setting, push=push)
            finally:
                t.doCleanups()
        t = tf.Bot("test_auto_fails_back")
        t.setUp()
        try:
            b = t.make()
            t.alive(b, 10)
            response = mock.Mock(defer=mock.AsyncMock(), send_message=mock.AsyncMock())
            interaction = types.SimpleNamespace(user=types.SimpleNamespace(id=42), message=Sent(ch, 930),
                                                data={"custom_id": "failback:%d" % b.lease.epoch}, response=response,
                                                followup=mock.Mock(send=mock.AsyncMock()))

            async def dcremote(h, *args, **kw):
                return 0, ""
            with mock.patch.object(tf.main.remote, "inventory", lambda: {"machines": tf.MACHINES}), \
                    mock.patch.object(tf.main.remote, "this_machine", lambda: "host-b"), \
                    mock.patch.object(tf.main.remote, "dcremote", dcremote), \
                    mock.patch.object(tf.main, "STATE", tempfile.mkdtemp(dir=tmp)), \
                    mock.patch.object(tf.main.config_mod, "write_bot_host", lambda m, e: True), \
                    mock.patch.object(tf.standby, "bot_panes", lambda s: tf.FOUND):
                run(b.failback_pressed(interaction))
        finally:
            t.doCleanups()
    scenario("main.failback_ask_done_pressed", main_failback)

    def main_replay(log, ch, host):
        botmain, bot = a_bot(ch)
        bot_user = types.SimpleNamespace(id=1)
        now = __import__("datetime").datetime(2027, 1, 15, 8, 0, tzinfo=__import__("datetime").timezone.utc)

        def history(limit=None):
            async def gen():
                yield types.SimpleNamespace(author=bot_user, content="old reply\n-# 3 秒 · p0", created_at=now)
            return gen()
        ch.history = history
        with mock.patch.object(type(bot), "user", bot_user), \
                mock.patch.object(botmain.events, "posted_ids", lambda texts: ["p0"]), \
                mock.patch.object(bot, "claim_turn", lambda cid, pid: pid != "p0", create=True), \
                mock.patch.object(botmain.cc_sessions, "transcript", lambda cwd, sid: "/x.jsonl"), \
                mock.patch.object(botmain.transcript, "replies_since", lambda path, since: [{"pid": "p1", "final": "答 " * 1500}]), \
                mock.patch.object(botmain.remote, "this_machine", lambda: "lap"):
            run(bot.replay_one("5", {"cwd": "/w", "session_id": "s1"}))
    scenario("main.replay_one", main_replay)

    def main_first_chat(log, ch, host):
        botmain, bot = a_bot(ch)

        async def ensure(guild, name, topic):
            return ch
        bot.ensure_channel = ensure
        alt = types.SimpleNamespace(instance_dir=lambda: "/w/instance")
        with mock.patch.object(botmain.remote, "alt", lambda: alt):
            run(bot.first_chat(object()))
    scenario("main.first_chat", main_first_chat)

    def main_messages(log, ch, host):
        import discord
        botmain, bot = a_bot(ch)
        bot.catchup.mark = lambda m: True
        bot.posts.owner_spoke = lambda cid: None
        said = []

        async def command(c, text, send):
            said.append(text)
            await send("答：" + text)
            await send("有按鈕", view=None)
        bot.command = command
        me = types.SimpleNamespace(id=42, bot=False)

        def msg(c, text):
            return types.SimpleNamespace(webhook_id=None, author=me, mentions=[], channel=c, content=text,
                                         attachments=[], stickers=[], poll=None)
        plain, files_, term = Channel(11, "notes", log), Channel(12, "files", log), Channel(13, "term", log)
        bot.channels_state.update({"11": {"plain": True}, "12": {"kind": "files", "cwd": "/w"},
                                   "13": {"kind": "terminal", "cwd": "/w"}, "5": {"cwd": "/w", "configured": True}})
        with mock.patch.object(botmain, "drill_sender", lambda: None):
            for c, text in ((plain, "!help"), (files_, "!status"), (term, "!help"), (ch, "!help")):
                run(bot.on_message(msg(c, text)))

            class Thread(discord.Thread):
                def __init__(self):
                    pass
                parent = None
            thread = Thread()
            thread.id, thread.parent_id, thread.name = 800, 5, "t"
            thread.send = Channel(800, "thread", log).send
            run(bot.on_message(msg(thread, "hello")))
    scenario("main.on_message", main_messages)

    def main_turns(log, ch, host):
        botmain, bot = a_bot(ch)
        st = bot.chan(ch)
        st.update({"cwd": "/w", "cwd_from": "category", "configured": True, "mode": "print"})
        ch.category = types.SimpleNamespace(name="工作")
        message = types.SimpleNamespace(channel=ch, add_reaction=mock.AsyncMock())

        class Typing(object):
            async def __aenter__(self):
                pass

            async def __aexit__(self, *a):
                pass
        ch.typing = lambda: Typing()
        clock = iter(range(1800000000, 1800001000, 6))
        res = types.SimpleNamespace(session_id="s2", error=None, text="the answer " * 300, tools={"Bash": 2}, cost=0.12)

        async def run_turn(text, sid, cwd, note, permission, on_progress=None, **kw):
            for i in range(6):
                await on_progress("Bash step %d" % i)
            return res
        bot.tmux.chosen_for = mock.AsyncMock(return_value=(None, "default"))
        with mock.patch.object(botmain, "run_turn", run_turn), \
                mock.patch.object(botmain.time, "time", lambda: float(next(clock))), \
                mock.patch.object(botmain.attachments, "folders", mock.AsyncMock(return_value=[])), \
                mock.patch.object(bot, "needs_setup", lambda c, s: False), \
                mock.patch.object(bot, "print_tail", lambda cid, st, sid: ""):
            run(bot.deliver(message, "do it"))  # (the folder said once, then the turn: 處理中 sent, edited, deleted)
            bot.tmux.chosen_for = mock.AsyncMock(side_effect=botmain.NotOpened(botmain.NotOpened.NOT_STARTED, "這個頻道還沒開好"))
            run(bot.turn(message, "again"))

            async def broken(m, t):
                raise RuntimeError("壞了")
            bot.loop = None

            class Loop(object):
                def create_task(self, coro):
                    run(coro)
            bot.loop = Loop()
            with mock.patch.object(bot, "print_turn", broken), mock.patch.object(botmain.traceback, "print_exc", lambda: None):
                bot.queue_turn(message, "x")
    scenario("main.deliver_turn_queue", main_turns)

    def main_held(log, ch, host):
        botmain, bot = a_bot(ch)
        st = bot.chan(ch)
        st.update({"cwd": "/w", "configured": True, "mode": "tmux", "busy": True})
        clock = iter(range(1800000000, 1800001000, 3))
        lock = __import__("asyncio").Lock()
        bot.tmux.rt = lambda c: types.SimpleNamespace(lock=lock)
        calls_ = iter([True, True, True, False])
        bot.tmux.live_target = mock.AsyncMock(side_effect=lambda c: next(calls_, False))
        bot.tmux.close = mock.AsyncMock()
        with mock.patch.object(botmain.time, "time", lambda: float(next(clock))), \
                mock.patch.object(botmain.asyncio, "sleep", mock.AsyncMock()):
            run(bot.leave_window(ch, st, "print"))

        class Full(object):
            def locked(self):
                return True

            async def acquire(self):
                pass

            def release(self):
                pass
        bot.sem, bot.sem_waiting = Full(), 2
        st["mode"] = "print"
        message = types.SimpleNamespace(channel=ch, add_reaction=mock.AsyncMock())
        with mock.patch.object(bot, "held", lambda s: "print"), mock.patch.object(bot, "leave_window", mock.AsyncMock()), \
                mock.patch.object(bot, "turn", mock.AsyncMock()):
            run(bot.print_turn(message, "x"))
    scenario("main.held_window_queued", main_held)

    def main_attach(log, ch, host):
        botmain, bot = a_bot(ch)
        bot.setup.cards[5] = 940
        bot.archive.offered = set()
        with mock.patch.object(botmain.routes, "window", lambda pane: "work:0"):
            try:
                run(bot.attach_here(None, {"id": "%3"}, ch))
            except Exception:
                pass  # (what comes after the card's line is attach_one's, below)
        bot.channels_state["5"] = {"cwd": "/w", "session_id": "sid-old", "pane": {"id": "%1", "socket": "/s"}}
        cat = types.SimpleNamespace(name="proj", text_channels=[])
        guild = types.SimpleNamespace(categories=[])
        bot.backfill = mock.AsyncMock()

        class Loop(object):
            def create_task(self, coro):
                coro.close()
        bot.loop = Loop()
        pane = {"host": "lap", "socket": "/s", "id": "%3", "pid": "900", "session": "work", "window": "0", "cwd": "/w/proj"}

        async def dcremote(h, *args, **kw):
            return 0, json.dumps({"session_id": "sid-new", "turns": []})
        with mock.patch.object(botmain.layout, "category", mock.AsyncMock(return_value=cat)), \
                mock.patch.object(botmain.archive, "remembered_by", lambda states, p: None), \
                mock.patch.object(botmain.routes, "archived_channel", lambda chans, h, p, proj: None), \
                mock.patch.object(bot, "left_behind", mock.AsyncMock(return_value=ch)), \
                mock.patch.object(botmain.routes, "unbind_any", mock.AsyncMock()), \
                mock.patch.object(botmain.routes, "bind_any", mock.AsyncMock()), \
                mock.patch.object(botmain.remote, "dcremote", dcremote), \
                mock.patch.object(botmain.remote, "altention_cmd", lambda h=None: "altention"):
            run(bot.attach_one(guild, pane, "proj"))
            local = dict(pane, host=None)
            local.pop("host")
            path = os.path.join(tmp, "attach.jsonl")
            with open(path, "w") as f:
                for i in range(2):
                    f.write(json.dumps({"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-10-01T01:00:00Z",
                                        "message": {"content": "q %d" % i}}, separators=(",", ":")) + "\n")
                    f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "a %d" % i}]}},
                                       separators=(",", ":")) + "\n")
            bot.channels_state["5"] = {"cwd": "/w", "session_id": "sid-new", "pane": {"id": "%1", "socket": "/s"}}
            with mock.patch.object(botmain.remote, "this_machine", lambda: "lap"), \
                    mock.patch.object(botmain.cc_sessions, "of_pane", lambda pid, sess: {"cwd": "/w/proj"}), \
                    mock.patch.object(botmain.tmuxio, "tmux", mock.AsyncMock(return_value=(0, "screen"))), \
                    mock.patch.object(botmain.cc_sessions, "live_session", lambda cwd, screen, rec: "sid-new"), \
                    mock.patch.object(botmain.cc_sessions, "transcript", lambda cwd, sid: path):
                run(bot.attach_one(guild, local, "proj"))
        run(botmain.Bot.backfill(bot, ch, ["one", "two"]))
    scenario("main.attach_and_backfill", main_attach)
    return out


def batch4_scenarios(p1c=False):
    """P1b batch 4: what dcctl hands Discord for send-json, edit and send. send-json and edit go to the bot's queue
    (delivered by deliver()), send stays straight to Discord; what reaches Discord, the #audit line, what dcctl says
    and the messages dcctl edit may change afterwards stay the same. Run against the old dcctl too: then nothing is
    queued and dcctl's own calls are what is recorded.

    `p1c` (C-172a P1c, the rules a tool's message goes by): the same scenarios, the ledger holding what each edits (a
    post of the sender's own, as since P1a), what the ledger has of channel 10 afterwards recorded too, and more
    scenarios for what P1c refuses or changes: @everyone written, custom_ids, --mention, editing a post of another
    channel's Claude or a card, dcctl pin. Recorded before P1c changes anything (the baseline), then again with it:
    the difference is what P1c changes."""
    import importlib.machinery
    import importlib.util
    import io
    import tempfile
    from unittest import mock
    from bot import config, postbox
    loader = importlib.machinery.SourceFileLoader("dcctl_payloadlog", os.path.join(os.path.dirname(HERE), "tools", "dcctl"))
    dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_payloadlog", loader))
    loader.exec_module(dcctl)
    chans = [{"id": "10", "name": "lab", "type": 0}, {"id": "11", "name": config.AUDIT_CHANNEL, "type": 0}]
    out = {}

    from bot import ledger, pending
    mine = {"intent": "post", "from": "channel:5"}

    def scenario(name, argv, stdin="", sent=(), content="old", extra=None, noted=None):
        log = []
        home = tempfile.mkdtemp(prefix="payloadlog-home-")
        state = os.path.join(home, ".local", "state", "altention")
        os.makedirs(state)
        if sent:
            with open(os.path.join(state, "dcctl-sent.json"), "w") as f:
                json.dump(list(sent), f)
        book = os.path.join(home, "ledger.json")
        if p1c:
            if noted is None:
                noted = {mid: mine for mid in sent}
            with open(book, "w") as f:
                json.dump({"channels": {"10": [[mid, dict(e, at=1.0)] for mid, e in noted.items()]}}, f, ensure_ascii=False)

        def call(method, path, body=None):  # (dcctl's own way to Discord)
            if method == "GET" and path == "/users/@me":
                return {"id": "1"}
            if method == "GET" and "/messages/" in path:
                return dict({"id": path.rsplit("/", 1)[1], "author": {"id": "1"}, "content": content}, **(extra or {}))
            if method == "POST" and path == "/channels/11/messages":
                log.append(["audit", body["content"]])
                return {"id": "950"}
            log.append([method, path, body])
            return {"id": "900"} if method == "POST" else {"id": path.rsplit("/", 1)[1]}

        class Http(object):  # (the bot's: discord.py's request with a route)
            async def request(self, route, json=None, **kw):
                path = route.url[len(route.BASE):]
                log.append([route.method, path, json])
                return {"id": "900"} if route.method == "POST" else {"id": path.rsplit("/", 1)[1]}

        bot, ps, _, _ = posts_world(log)
        bot.http = Http()
        thread = Channel(777, "a-thread", log)
        thread.parent_id = 10
        bot.chans.update({10: Channel(10, "lab", log), 5: Channel(5, "st-5", log), 777: thread})  # (send-json's
        # channels as the bot sees them since it goes altention dc post --json's way: C-172a P1d)

        async def audit(text):
            log.append(["audit", text])
        bot.audit = audit

        def drain(_secs=None):  # (the bot's round while dcctl waits for its receipt)
            for path, post in postbox.waiting():
                run(ps.safely(post, path))
                postbox.done(path)
        said, errs = io.StringIO(), io.StringIO()
        ledger.reset()
        with mock.patch.dict(os.environ, dict({"HOME": home}, **({"DCBOT_LEDGER": book} if p1c else {}))), \
                mock.patch.multiple(postbox, SPOOL=os.path.join(home, "posts"), RECEIPTS=os.path.join(home, "receipts")), \
                mock.patch.object(config, "OWNER_ID", 42), mock.patch.object(dcctl, "call", call), \
                mock.patch.object(dcctl, "channels", lambda *a, **k: chans), \
                mock.patch.object(dcctl, "listing_for", lambda *a, **k: chans), \
                mock.patch.object(dcctl, "sender", lambda: {"key": "channel:5", "words": "<#5> 的 Claude（查證過）"}), \
                mock.patch.object(dcctl.time, "sleep", drain), \
                mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", said), \
                mock.patch("sys.stderr", errs if p1c else sys.stderr):
            try:
                dcctl.main(argv)
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) or e.code is None else "exit: " + str(e.code)
            log.append(["said", said.getvalue(), code])
            if p1c and errs.getvalue():  # (what dcctl said on stderr: why it refused; since P1c with exit 4)
                log.append(["stderr", errs.getvalue()])
            try:
                with open(os.path.join(state, "dcctl-sent.json")) as f:
                    log.append(["dcctl edit may change", json.load(f)])
            except OSError:
                log.append(["dcctl edit may change", None])
            if p1c:
                if hasattr(ledger, "pin"):  # (the bot's next round: dcctl send's notes, and since P1c pin's)
                    pending.drain(ledger.note, ledger.pin)
                else:
                    pending.drain(ledger.note)
                log.append(["ledger of channel 10", [[mid, {k: e.get(k) for k in ("intent", "from", "pinned") if e.get(k) is not None}]
                                                     for mid, e in ledger._loaded()["channels"].get("10", [])]])
                log.append(["dcctl-pinned.json", os.path.exists(os.path.join(state, "dcctl-pinned.json"))])
        ledger.reset()
        out[name] = log

    v2 = {"flags": 32768, "components": [{"type": 10, "content": "hi <@42> @everyone"},
                                         {"type": 17, "accent_color": 15774258, "components": [{"type": 10, "content": "x"}]}],
          "allowed_mentions": {"parse": ["everyone", "users"], "users": ["42"]}}
    scenario("send_json.v2", ["send-json", "lab"], json.dumps(v2))
    scenario("send_json.thread", ["send-json", "777"], '{"content": "<@42> in a thread"}')
    every = {"content": "all of it", "tts": False, "nonce": "n1", "enforce_nonce": True,
             "embeds": [{"title": "t", "description": "d", "color": 3, "fields": [{"name": "a", "value": "b"}]}],
             "components": [{"type": 1, "components": [{"type": 2, "style": 5, "label": "link", "url": "https://example.com"}]}],
             "message_reference": {"message_id": "800", "fail_if_not_exists": False}, "sticker_ids": ["1"],
             "attachments": [], "poll": {"question": {"text": "q"}, "answers": [{"poll_media": {"text": "a"}}]},
             "flags": 4096}
    scenario("send_json.every_field", ["send-json", "10"], json.dumps(every))
    scenario("send_json.reserved", ["send-json", "lab"], '{"content": "🔔 **[秘書]** 已核准"}')
    scenario("send_json.not_json", ["send-json", "lab"], "{nope")
    scenario("edit.done", ["edit", "lab", "555"], "第一行\n\n  第二行\n", sent=("555",))
    scenario("edit.not_dcctls", ["edit", "lab", "556"], "new", sent=("555",))
    scenario("edit.same", ["edit", "lab", "555"], "old\n", sent=("555",))
    scenario("edit.buttons", ["edit", "lab", "555"], "new", sent=("555",), extra={"components": [{"type": 1}]})
    scenario("send.plain", ["send", "lab", "hello", "<@42>"])
    scenario("send.mentions", ["send", "lab", "hey", "<@42>", "--mentions"])
    if not p1c:
        return out
    button = {"type": 1, "components": [{"type": 2, "style": 1, "label": "b", "custom_id": "ask:1"}]}
    user_button = {"type": 1, "components": [{"type": 2, "style": 1, "label": "b", "custom_id": "user:b"}]}
    scenario("p1c.send_json.everyone", ["send-json", "lab"], '{"content": "hi @everyone"}')
    scenario("p1c.send_json.custom_id", ["send-json", "lab"], json.dumps({"content": "x", "components": [button]}))
    scenario("p1c.send_json.user_custom_id", ["send-json", "lab"], json.dumps({"content": "x", "components": [user_button]}))
    scenario("p1c.send_json.mention_owner", ["send-json", "lab", "--mention", "owner"], '{"content": "<@42> look"}')
    scenario("p1c.send_json.mention_user", ["send-json", "lab", "--mention", "user:43"], '{"content": "<@43> look"}')
    scenario("p1c.send.everyone", ["send", "lab", "hi", "@here"])
    scenario("p1c.edit.everyone", ["edit", "lab", "555"], "all @everyone", sent=("555",))
    scenario("p1c.edit.anothers_post", ["edit", "lab", "555"], "new", sent=("555",),
             noted={"555": dict(mine, **{"from": "channel:6"})})
    scenario("p1c.edit.card", ["edit", "lab", "555"], "new", sent=("555",), noted={"555": {"intent": "card", "from": None}})
    scenario("p1c.edit.board", ["edit", "lab", "555"], "new", sent=("555",), noted={"555": dict(mine, intent="board")})
    scenario("p1c.edit.not_noted", ["edit", "lab", "555"], "new", sent=("555",), noted={})
    scenario("p1c.pin", ["pin", "lab", "555"], sent=("555",))
    scenario("p1c.unpin", ["pin", "lab", "555", "--unpin"], sent=("555",), noted={"555": dict(mine, pinned=True)})
    return out


def all_scenarios(batch):
    if batch == "p1b1":
        out = {}
        out.update({"posts." + k: v for k, v in posts_scenarios().items()})
        out.update({"bridge." + k: v for k, v in bridge_scenarios().items()})
        return out
    if batch == "p1b2":
        return batch2_scenarios()
    if batch == "p1b3":
        return batch3_scenarios()
    if batch == "p1b3b":
        return batch3_scenarios("b")
    if batch == "p1b4":
        return batch4_scenarios()
    if batch == "p1c":
        return batch4_scenarios(p1c=True)
    raise SystemExit("payloadlog：不認得 %s" % batch)


def main(argv):
    if len(argv) != 2 or argv[0] not in ("--write", "--check"):
        raise SystemExit(__doc__)
    batch = argv[1]
    got = json.loads(json.dumps(all_scenarios(batch), ensure_ascii=False))
    path = BASELINE % batch
    if argv[0] == "--write":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("寫了 %d 個情境到 %s" % (len(got), path))
        return 0
    with open(path, encoding="utf-8") as f:
        want = json.load(f)
    bad = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
    for k in bad:
        print("不一樣：%s\n  原本：%s\n  現在：%s" % (k, json.dumps(want.get(k), ensure_ascii=False)[:600],
                                              json.dumps(got.get(k), ensure_ascii=False)[:600]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
