"""Claude's AskUserQuestion tool, shown in Discord as buttons and menus.

In a terminal the tool opens a menu; nobody on Discord can operate that. Instead `dchook --ask` (a
PreToolUse hook for that tool only) sends the questions here as an "Ask" event and waits. Each question
becomes one Discord message: a button per option, or a menu when several may be chosen, plus "其他…" for a
typed answer. When every question is answered, the answers are written to state/answers/<ask_id>.json,
the hook hands them to the tool, and Claude carries on.

Plan mode works the same way (`dchook --plan`, a "Plan" event): the plan is posted with two buttons, approve
or ask for changes, and the click decides in place of the terminal's "ready to code?" dialog.

What is waiting is kept on disk (state/asks/) and the buttons are found by their custom_id, not by an
in-memory view, so a question asked before a bot restart can still be answered after it.
"""
import json
import os
import time
import traceback

import discord

from . import awaiting, config
from .util import split_message

ASKS = os.path.join(config.STATE, "asks")        # questions waiting for the owner
ANSWERS = os.path.join(config.STATE, "answers")  # answers waiting for the hook
PREFIX = "ask"


def cut(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def question_text(q, i, total):
    """The message for one question: what is asked, and each option with its explanation."""
    head = "❓ **%s**%s　%s%s" % (cut(q.get("header") or "問題", 60), "（%d/%d）" % (i + 1, total) if total > 1 else "",
                               (q.get("question") or "").strip(), "　（可複選）" if q.get("multiSelect") else "")
    rows = []
    for o in q.get("options") or []:
        rows.append("- **%s**%s" % (cut(o.get("label"), 80), "：" + cut(o["description"], 300) if o.get("description") else ""))
        if o.get("preview"):
            rows.append("```\n%s\n```" % str(o["preview"]).replace("```", "'''")[:500])
    return (head + "\n" + "\n".join(rows))[:1950]


def answered_text(q, answer, done="✅"):
    return ("%s **%s**　%s\n→ %s" % (done, cut(q.get("header") or "問題", 60), (q.get("question") or "").strip(), answer))[:1950]


def custom_id(ask_id, qi, what):
    """`what`: an option index (a button), "s" (the menu) or "o" (type your own)."""
    return "%s:%s:%d:%s" % (PREFIX, ask_id, qi, what)


def parse_id(cid):
    parts = (cid or "").split(":")
    if len(parts) != 4 or parts[0] != PREFIX or not parts[2].isdigit():
        return None
    return parts[1], int(parts[2]), parts[3]


def chosen(q, what, values=()):
    """The answer string for a click: the option's label, or the labels picked in the menu."""
    opts = q.get("options") or []
    picks = [int(v) for v in values] if what == "s" else [int(what)] if what.isdigit() else []
    labels = [opts[i]["label"] for i in picks if 0 <= i < len(opts)]
    return ", ".join(labels) or None


def view_for(ask_id, qi, q):
    view = discord.ui.View(timeout=None)
    opts = (q.get("options") or [])[:25]
    if q.get("multiSelect") or len(opts) > 4:
        view.add_item(discord.ui.Select(
            custom_id=custom_id(ask_id, qi, "s"), placeholder="選好之後點選單外面送出", min_values=1,
            max_values=len(opts) if q.get("multiSelect") else 1,
            options=[discord.SelectOption(label=cut(o.get("label"), 100), value=str(i),
                                          description=cut(o.get("description"), 100) or None)
                     for i, o in enumerate(opts)]))
    else:
        for i, o in enumerate(opts):
            view.add_item(discord.ui.Button(label=cut(o.get("label"), 80), custom_id=custom_id(ask_id, qi, str(i)),
                                            style=discord.ButtonStyle.primary))
    view.add_item(discord.ui.Button(label="其他…", custom_id=custom_id(ask_id, qi, "o"),
                                    style=discord.ButtonStyle.secondary))
    return view


def _path(folder, ask_id):
    return os.path.join(folder, os.path.basename(ask_id) + ".json")


def read(ask_id):
    """The record of a question as it is on disk, or None."""
    try:
        with open(_path(ASKS, ask_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load(ask_id):
    """The record of a question that is still open, or None: one that is closed and only owed to its machine (its
    result still on the way there: Asker.hand()) is open to nobody."""
    state = read(ask_id)
    return None if isinstance(state, dict) and "owed" in state else state


def save(ask_id, state):
    os.makedirs(ASKS, exist_ok=True)
    tmp = _path(ASKS, ask_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, _path(ASKS, ask_id))
    awaiting.note()  # (opened, answered in part, or closed and only owed: what waits for the owner may have changed)


def forget(ask_id):
    try:
        os.remove(_path(ASKS, ask_id))
    except OSError:
        pass
    awaiting.note()


GONE = ("This conversation's Discord channel was deleted, so nobody can answer there. Ask in plain text here in the "
        "terminal instead.")  # to Claude, for a question of a conversation whose channel is no more


def gone_result(plan=False):
    """What the hook of a question (or of a plan) is handed when its channel is no more: a question is refused with
    the reason; a plan is left to the terminal's own dialog."""
    return {} if plan else {"reason": GONE}


def undelivered():
    """The machines a result is still owed to (read from the records: this waits on nothing)."""
    return sorted(set(str(s["host"]) for _, s in waiting(owed=True) if s.get("host")))


async def deliver_to(host, ask_id, result):
    """deliver(), also to a hook waiting on another machine. False when it did not get there (that machine does not
    answer just now)."""
    if host:
        from . import remote
        code, _ = await remote.dcremote(host, "answer", ask_id, input=json.dumps(result, ensure_ascii=False))
        return code == 0
    deliver(ask_id, result)
    return True


def deliver(ask_id, result):
    """Hand the hook its result: {"answers": {question: answer}} or {"reason": why there is none}."""
    os.makedirs(ANSWERS, exist_ok=True)
    tmp = _path(ANSWERS, ask_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(tmp, _path(ANSWERS, ask_id))


def waiting(channel_id=None, owed=False):
    """(ask_id, state) of every question still open, optionally only one channel's. `owed`: instead, the ones that
    are closed and whose result is still to be handed to a hook on another machine."""
    try:
        names = [n[:-5] for n in os.listdir(ASKS) if n.endswith(".json")]
    except OSError:
        return []
    out = [(n, read(n)) for n in sorted(names)]
    return [(n, s) for n, s in out if isinstance(s, dict) and ("owed" in s) == owed
            and (channel_id is None or str(s.get("channel")) == str(channel_id))]


def prune(days=1):
    """Answers no hook ever collected."""
    try:
        for n in os.listdir(ANSWERS):
            p = os.path.join(ANSWERS, n)
            if os.path.getmtime(p) < time.time() - days * 86400:
                os.remove(p)
    except OSError:
        pass


class OtherModal(discord.ui.Modal):
    """The "其他…" button: a box to type an answer that is not among the options."""
    def __init__(self, asker, ask_id, qi, q):
        super().__init__(title="自己輸入答案", timeout=1800)
        self.asker, self.ask_id, self.qi = asker, ask_id, qi
        self.text = discord.ui.TextInput(label=cut(q.get("header") or "答案", 45), style=discord.TextStyle.paragraph,
                                         max_length=1500, placeholder=cut(q.get("placeholder") or q.get("question"), 100))
        self.add_item(self.text)

    async def on_submit(self, interaction):
        await self.asker.record(interaction, self.ask_id, self.qi, str(self.text.value).strip())


class Asker(object):
    """`audit` is the bot's audit(text) coroutine."""
    def __init__(self, audit):
        self.audit = audit
        self.on_done = None  # coroutine (channel, ask_id, "已回答" / "已取消"): bot/posts.py updates its notice
        self.on_owed = None  # called when a result is owed to another machine (hand()): the bridge starts its task

    async def post(self, ch, ev):
        """An Ask event: show the questions. Returns False (and tells the hook) if there is nothing to show."""
        ask_id, qs = ev["ask_id"], [q for q in ev.get("questions") or [] if isinstance(q, dict)]
        if not qs:
            await deliver_to(ev.get("host"), ask_id, {"reason": "The question had no content."})
            return False
        state = {"channel": str(ch.id), "questions": qs, "answers": {}, "messages": {}, "ts": time.time(),
                 "host": ev.get("host")}
        try:
            for i, q in enumerate(qs):
                view = view_for(ask_id, i, q)
                msg = await ch.send(question_text(q, i, len(qs)), view=view)
                view.stop()  # clicks are handled by custom_id in on_interaction, not by this object
                state["messages"][str(i)] = msg.id
        except Exception as e:
            traceback.print_exc()
            await deliver_to(ev.get("host"), ask_id, {"reason": "The question could not be shown on Discord (%s). Ask in "
                                                             "plain text instead." % type(e).__name__})
            return False
        save(ask_id, state)
        return True

    async def post_plan(self, ch, ev):
        """A Plan event: show the plan, with buttons to approve it or to say what should change."""
        ask_id, plan = ev["ask_id"], (ev.get("plan") or "").strip() or "（Claude 沒有附上計畫內容）"
        q = {"header": "計畫", "question": "Claude 寫好計畫了，要照這個執行嗎？"}
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="核准，開始執行", custom_id=custom_id(ask_id, 0, "y"),
                                        style=discord.ButtonStyle.success))
        view.add_item(discord.ui.Button(label="要修改…", custom_id=custom_id(ask_id, 0, "o"),
                                        style=discord.ButtonStyle.secondary))
        try:
            for part in split_message("📋 **Claude 的計畫**\n" + plan):
                await ch.send(part)
            msg = await ch.send("📋 **%s**　%s" % (q["header"], q["question"]), view=view)
            view.stop()
        except Exception as e:
            traceback.print_exc()
            await deliver_to(ev.get("host"), ask_id, {"reason": "The plan could not be shown on Discord (%s). Describe it "
                                                             "in plain text and ask the user to reply." % type(e).__name__})
            return False
        save(ask_id, {"channel": str(ch.id), "kind": "plan", "questions": [q], "answers": {}, "messages": {"0": msg.id},
                      "ts": time.time(), "host": ev.get("host")})
        return True

    async def on_interaction(self, interaction):
        """A click on one of our buttons or menus. Returns True if the interaction was ours."""
        if interaction.type != discord.InteractionType.component:
            return False
        ref = parse_id((interaction.data or {}).get("custom_id"))
        if ref is None:
            return False
        ask_id, qi, what = ref
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能回答。", ephemeral=True)
            return True
        state = load(ask_id)
        if state is None or not 0 <= qi < len(state["questions"]):
            await interaction.response.edit_message(content="⌛ 這個問題已經失效（已回答、已取消，或 Claude 沒有在等了）。",
                                                    view=None)
            return True
        q = state["questions"][qi]
        if what == "o":
            await interaction.response.send_modal(OtherModal(self, ask_id, qi, q))
            return True
        if what == "y" and state.get("kind") == "plan":
            forget(ask_id)
            await deliver_to(state.get("host"), ask_id, {"approve": True})
            await interaction.response.edit_message(content="✅ **計畫已核准**，Claude 開始執行。", view=None)
            await self.audit("#%s 核准了 Claude 的計畫" % getattr(interaction.channel, "name", "DM"))
            return True
        answer = chosen(q, what, (interaction.data or {}).get("values") or ())
        if answer is None:
            await interaction.response.send_message("沒有選到東西，再試一次。", ephemeral=True)
            return True
        await self.record(interaction, ask_id, qi, answer)
        return True

    async def record(self, interaction, ask_id, qi, answer):
        state = load(ask_id)
        if state is None or not answer:
            return await interaction.response.send_message("這個問題已經失效，或答案是空的。", ephemeral=True)
        q = state["questions"][qi]
        if state.get("kind") == "plan":  # typed into "要修改…": the plan goes back with this as the reason
            forget(ask_id)
            await deliver_to(state.get("host"), ask_id, {"reason": "The user read the plan on Discord and wants it changed before approving: "
                                       "%s\nRevise the plan and present it again." % answer})
            await interaction.response.edit_message(content="✏️ **計畫退回修改**\n" + answer[:1800], view=None)
            return await self.audit("#%s 要求修改 Claude 的計畫：%s" % (getattr(interaction.channel, "name", "DM"),
                                                                 cut(answer, 80)))
        await interaction.response.edit_message(content=answered_text(q, answer), view=None)
        await self.settle(interaction.channel, ask_id, state, qi, answer)

    async def typed(self, ch, text):
        """A message typed in the channel while Claude waits on exactly one unanswered question (not a plan):
        it is the answer, as if typed into "其他…". Returns True if it was taken as one."""
        open_asks = [(a, s) for a, s in waiting(ch.id) if s.get("kind") != "plan"]
        if len(open_asks) != 1 or not text.strip():
            return False
        ask_id, state = open_asks[0]
        left = [i for i in range(len(state["questions"])) if str(i) not in state["answers"]]
        if len(left) != 1:
            return False  # several questions still open: which one this answers is anybody's guess
        qi, answer = left[0], text.strip()[:1500]
        try:
            await ch.get_partial_message(state["messages"][str(qi)]).edit(
                content=answered_text(state["questions"][qi], answer), view=None)
        except Exception:
            traceback.print_exc()
        await self.settle(ch, ask_id, state, qi, answer)
        return True

    async def settle(self, ch, ask_id, state, qi, answer):
        """Record one answer; once every question has one, hand them all to the waiting hook."""
        state["answers"][str(qi)] = answer
        if len(state["answers"]) < len(state["questions"]):
            return save(ask_id, state)
        forget(ask_id)
        await deliver_to(state.get("host"), ask_id, {"answers": dict((qq.get("question") or "", state["answers"][str(i)])
                                         for i, qq in enumerate(state["questions"]))})
        if self.on_done is not None:
            await self.on_done(ch, ask_id, "已回答")
        await self.audit("#%s 回答 Claude 的提問：%s" % (
            getattr(ch, "name", "DM"),
            "；".join("%s → %s" % (cut(qq.get("header") or qq.get("question"), 30), cut(state["answers"][str(i)], 60))
                      for i, qq in enumerate(state["questions"]))))

    async def hand(self, ask_id, state, result):
        """A question is closed, and its hook is handed `result`. A hook of this machine: at once. One on another
        machine: never waited for here (whoever closes a question may be the bot's round of events, which waits on no
        other machine): the record stays, marked as owed its result, and the bridge's task for that machine sends it
        (`on_owed` starts it; again()), now and until it gets there. One road for every result that goes to another
        machine after its question is closed."""
        if not state.get("host"):
            forget(ask_id)
            await deliver_to(None, ask_id, result)  # (this machine's: a file written, nothing waited for)
            return
        save(ask_id, dict(state, owed=result))
        if self.on_owed is not None:
            self.on_owed()

    async def gone(self, ch):
        """The channel was deleted: whatever it still asks is let go at once (#issues 21: the hook waited for an
        answer nobody could give, up to its limit of half a day, and the conversation under it stood still). Nothing
        is edited: the messages went with the channel. A notice of it in another channel is updated."""
        for ask_id, state in waiting(ch.id):
            await self.hand(ask_id, state, gone_result(plan=state.get("kind") == "plan"))
            if self.on_done is not None:
                try:
                    await self.on_done(ch, ask_id, "已取消")
                except Exception:
                    traceback.print_exc()

    async def again(self, host, limit=12 * 3600):
        """What is owed to hooks on `host` (hand()) is sent; how many are still to go. One that has waited past the
        hook's own limit is forgotten. This waits on another machine: never called from the bot's round of events
        (the bridge runs it in a task of its own, one for a machine: refuse_again())."""
        left = 0
        for ask_id, state in waiting(owed=True):
            if state.get("host") != host:
                continue
            if time.time() - (state.get("ts") or 0) > limit or await deliver_to(host, ask_id, state["owed"]):
                forget(ask_id)
            else:
                left += 1
        return left

    async def cancel(self, ch, reason="The user interrupted before answering."):
        """The turn that asked is over (stopped, reset, or it ended): close whatever is still open."""
        for ask_id, state in waiting(ch.id):
            await self.hand(ask_id, state, {"reason": reason})
            if self.on_done is not None:
                await self.on_done(ch, ask_id, "已取消")
            for i, mid in state.get("messages", {}).items():
                if i in state["answers"]:
                    continue
                try:
                    q = state["questions"][int(i)]
                    await ch.get_partial_message(mid).edit(content=answered_text(q, "（已取消，沒有回答）", "⌛"), view=None)
                except Exception:
                    pass
