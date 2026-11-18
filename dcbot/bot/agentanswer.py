"""A Claude answering, for the owner, what another channel's Claude waits on (C-176 batch 2, manager 11-11).

`altention dc post <channel> --waiting` lists what that channel waits on (questions, plans, Claude Code's menus shown
with buttons), each with an id; `--answer <id>` with `--pick`/`--text`/`--key`/`--approve`/`--revise` and `--why`
answers one. The answer goes the way the owner's buttons go (ask.py's settle and plan results, menus.py's act/press
with its look at the screen again), so whatever the buttons refuse is refused here too. What is not done:
- a channel that turned it off (its state's `agent_answers` false: `!agentanswers off`);
- a sender with no channel of its own, or a channel answering itself;
- a menu counting down to Claude Code's own denial (auto mode's last check: manager 11-11, owner's only);
- a question already answered or closed, a menu changed or gone, a plan dealt with.
The reminder of a pinned Claude Code (cc_watch.remind(), C-187) is one of them too, in the channel it was posted in.
Every answer, and every one refused, is one #audit line; the card says who answered. The result goes back to the
sending Claude through its post's receipt (posts.replied()).
"""
import traceback

from . import ask, cc_health, deliver, messages

FLAG = "agent_answers"   # channels.json: False = this channel's Claude may not be answered by another (default: may)
WHY_MAX = 300

# words (C-176; #release)
BY = "\n-# 由 %s 回答"
WHO = "%s 的 Claude"
OFF = "這個頻道關掉了 Claude 代答（使用者用 `!agentanswers on` 可以打開）"
NO_SENDER = "認不出是哪個頻道的 Claude 在回答（要在有頻道的 Claude 裡用）"
SELF = "不能回答自己頻道的提問"
NOTHING = "#%s 現在沒有在等使用者回答的東西"
GONE_ASK = "這個提問已經答了、取消了，或 Claude 已經不在等了"
GONE_MENU = "這個選單已經不在了（答了、換了，或機器人重啟過）"
COUNTDOWN = "這是 Claude Code 在危險操作前的最後一道確認（例如有自動拒絕的倒數），只有使用者能按"
PANEL = "這是 Claude Code 的面板（例如 /model），只有使用者能操作"
GONE_PIN = "這個提醒已經答了，或已經不再停在舊版"
PIN = "p-ccpin"  # the one reminder of a pinned Claude Code (C-187): one at a time, so one fixed id
KEYS = {"enter": "enter", "esc": "esc", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "tab": "Tab",
        "space": "Space"}  # C-189 (manager 11-12): what a Claude may press on a menu besides a number or text
UNKNOWN = "沒有這個編號：先用 `altention dc post %s --waiting` 看現在的編號"
BAD = "%s"


def allowed(st):
    return st.get(FLAG) is not False


def who(sender_ch):
    return WHO % ("#%s" % sender_ch.name)


def vouch(post):
    """For the #audit lines only, after who(): whether the sender was looked up or only said (C-186 P0-2)."""
    from .posts import vouched
    return vouched(post)


def items(bridge, ch):
    """[(id, kind, text)] of what `ch` waits on now: questions and plans (state/asks), menus (Menus.open)."""
    out = []
    for ask_id, state in ask.waiting(ch.id):
        if state.get("kind") == "plan":
            out.append(("q-" + ask_id, "plan", plan_text(state)))
        else:
            out.append(("q-" + ask_id, "question", question_text(state)))
    for token, m in list(getattr(getattr(bridge, "menus", None), "open", {}).items()):
        if m.get("channel") == ch.id and menu_kind(m):
            out.append(("m-" + token, "menu", menu_text(m)))
    pinned, watch = cc_health.pin_asking(), getattr(bridge, "cc", None)
    if pinned and watch is not None and pinned["card"][0] == ch.id:  # (C-187) the reminder of a pinned Claude Code
        keep, new = watch.pin_choices(pinned)
        out.append((PIN, "pin", "%s\n- %s\n- %s" % (messages.cc("pin_long", old=pinned.get("version"),
                                                                  days=cc_health.pin_days()), keep, new)))
    return out


def menu_kind(m):
    """A numbered dialog (its buttons press a number, Enter, or type text, after the screen is read again: "shown"),
    not a panel (/model and the like: its own keys, `actions`), which stays the owner's (#code-review on 0fc0847)."""
    return "shown" in m and "actions" not in m


def last_check(m):
    """Does this menu look like Claude Code's last check before a risky call, whether or not its countdown was read
    (manager 11-11, after 2.1.322 reworded it): a row starting "⚠ Claude Code will" in what its card shows of it
    (menus.py keeps the excerpt), or "Dangerous" in its title, what it asks or that excerpt. Refused when in doubt: Claude Code rewording it again makes it the
    owner's to press, never a Claude's."""
    from altention.connectors.claude_code import panels
    shown = (m.get("shown") or [""])[0] or ""
    text = "\n".join([m.get("title") or "", shown, m.get("excerpt") or ""])
    return any(panels.AUTO_DENY_START.match(r) for r in text.split("\n")) or "dangerous" in text.lower()


def question_text(state):
    lines = []
    for i, q in enumerate(state.get("questions") or []):
        if str(i) in (state.get("answers") or {}):
            continue
        lines.append("  題目：%s%s" % ((q.get("question") or "").strip(), "（可以選多個）" if q.get("multiSelect") else ""))
        lines += ["    - %s%s" % (o.get("label"), "：" + o["description"] if o.get("description") else "")
                  for o in q.get("options") or []]
    return "\n".join(lines)


def plan_text(state):
    """The plan itself (ask.post_plan keeps it in the record), its first 60 rows; a record from before that: the card's
    question, as before."""
    text = state.get("plan") or ((state.get("questions") or [{}])[0].get("question") or "")
    rows = text.strip().splitlines()
    return "  計畫：\n" + "\n".join("    " + line for line in rows[:60]) + ("\n    …（還有 %d 行）" % (len(rows) - 60) if len(rows) > 60 else "")


def menu_text(m):
    shown = m.get("shown") or ["", []]
    lines = ["  %s" % (m.get("title") or "選單"), "  " + (shown[0] or "").replace("\n", "\n  ")]
    lines += ["    %s. %s" % (n, label) for n, label in shown[1]]
    if m.get("deadline") or last_check(m):
        lines.append("  ⏳ Claude Code 危險操作前的最後一道確認：只有使用者能按")
    return "\n".join(lines)


def waiting_said(bridge, ch):
    got = items(bridge, ch)
    if not got:
        return NOTHING % ch.name
    head = "#%s 在等的東西%s：" % (ch.name, "" if allowed(bridge.host.chan(ch)) else "（這個頻道關掉了 Claude 代答）")
    return "\n".join([head] + ["[%s] %s\n%s" % (i, {"question": "提問", "plan": "計畫", "menu": "終端機選單",
                                                         "pin": "停在舊版 Claude Code 的提醒"}[k], t)
                               for i, k, t in got])


async def answer(bridge, ch, sender_ch, post):
    """(ok, what to tell the sending Claude). Refusals are said in #audit too."""
    audit = bridge.host.audit
    why = " ".join(str(post.get("why") or "").split())[:WHY_MAX]
    ref = str(post.get("answer") or "")

    async def refuse(text):
        await audit("#%s：%s想回答 %s，沒送：%s" % (ch.name, (who(sender_ch) + vouch(post) + " ") if sender_ch is not None else "", ref, text))
        return False, "沒送：" + text
    if sender_ch is None:
        return await refuse(NO_SENDER)
    if sender_ch.id == ch.id:
        return await refuse(SELF)
    if not allowed(bridge.host.chan(ch)):
        return await refuse(OFF)
    if ref.startswith("q-"):
        return await answer_ask(bridge, ch, sender_ch, ref[2:], post, why, refuse)
    if ref.startswith("m-"):
        return await answer_menu(bridge, ch, sender_ch, ref[2:], post, why, refuse)
    if ref == PIN:
        return await answer_pin(bridge, ch, sender_ch, post, why, refuse)
    return await refuse(UNKNOWN % ch.name)


def picked(q, pick):
    """The answer string for one question from `--pick`: an option's label as written (ask.chosen()'s shape: labels
    joined by ", " for several); None if a label is not one of its options."""
    labels = [o.get("label") for o in q.get("options") or []]
    if pick in labels:
        return pick
    parts = [p.strip() for p in pick.split(",")] if q.get("multiSelect") else [pick]
    return ", ".join(parts) if parts and all(p in labels for p in parts) else None


async def answer_ask(bridge, ch, sender_ch, ask_id, post, why, refuse):
    asks = bridge.asks
    state = ask.load(ask_id)
    if state is None or str(state.get("channel")) != str(ch.id):
        return await refuse(GONE_ASK)
    by = who(sender_ch)
    if state.get("kind") == "plan":
        if post.get("approve"):
            ask.forget(ask_id)
            await ask.deliver_to(state.get("host"), ask_id, {"approve": True})
            await edit(ch, state, "0", "✅ **計畫已核准**，Claude 開始執行。" + BY % by)
            await asks.closed(ch, ask_id)
            await asks.plan_done(ch, ask_id, "計畫已由 %s 核准" % by)
            await bridge.host.audit("#%s：%s核准了計畫（為什麼：%s）" % (ch.name, by + vouch(post) + " ", why))
            return True, "已核准 #%s 的計畫" % ch.name
        revise = str(post.get("revise") or "").strip()
        if not revise:
            return await refuse(BAD % "計畫要用 --approve 或 --revise \"要改的地方\"")
        ask.forget(ask_id)
        await ask.deliver_to(state.get("host"), ask_id, {"reason": "The user's other Claude read the plan for the user and "
                                                                    "wants it changed before approving: %s\nRevise the plan "
                                                                    "and present it again." % revise})
        await edit(ch, state, "0", "✏️ **計畫退回修改**\n" + revise[:1700] + BY % by)
        await asks.closed(ch, ask_id)
        await asks.plan_done(ch, ask_id, "計畫已由 %s 退回修改" % by)
        await bridge.host.audit("#%s：%s要求修改計畫：%s（為什麼：%s）" % (ch.name, by + vouch(post) + " ", ask.cut(revise, 80),
                                                                       why))
        return True, "已退回 #%s 的計畫，要它修改" % ch.name
    left = [i for i in range(len(state["questions"])) if str(i) not in state["answers"]]
    picks, text = list(post.get("pick") or []), str(post.get("text") or "").strip()
    if text:
        if len(left) != 1 or picks:
            return await refuse(BAD % "--text 只用在只剩一題、而且沒有 --pick 的時候")
        given = [(left[0], text[:1500])]
    else:
        if len(picks) != len(left):
            return await refuse(BAD % ("還有 %d 題沒答，--pick 要給 %d 個（照題目順序）" % (len(left), len(left))))
        given = []
        for qi, pick in zip(left, picks):
            got = picked(state["questions"][qi], pick)
            if got is None:
                return await refuse(BAD % ("「%s」不是第 %d 題的選項" % (pick, qi + 1)))
            given.append((qi, got))
    done = []
    for qi, got in given:
        # Each answer on the record as it is now (#code-review on 0fc0847): read again, the owner may have answered a
        # question meanwhile (that one is theirs, never written over), or the question been closed; then settled at
        # once (settle(): no await from its look to its save), and only then the card edited.
        fresh = ask.load(ask_id)
        if fresh is None:
            return await refuse(GONE_ASK + (partly(done) if done else ""))
        if str(qi) in fresh["answers"]:
            return await refuse("第 %d 題使用者已經答了%s" % (qi + 1, partly(done)))
        if not await asks.settle(ch, ask_id, fresh, qi, got, by=by, why=why, vouch=vouch(post)):
            return await refuse(GONE_ASK + (partly(done) if done else ""))
        done.append(got)
        await edit(ch, fresh, str(qi), ask.answered_text(fresh["questions"][qi], got) + BY % by)
    return True, "已回答 #%s 的提問：%s" % (ch.name, "；".join(done))


def partly(done):
    return "（前面已經答了：%s）" % "；".join(done) if done else ""


async def edit(ch, state, qi, content):
    try:
        await deliver.deliver(ch, content=content[:1990], view=None, intent="card", edit=(state.get("messages") or {})[qi])
    except Exception:  # (the card is what shows it; the answer itself still goes)
        import traceback
        traceback.print_exc()


class Press(object):
    """What menus.act() needs of an interaction, for a key a Claude presses (C-176): the channel, who (`by`, `why`:
    the card's line, #audit), and what it would have been told (`told`: a refusal; `did`: what was pressed;
    `changed`: the screen was another one)."""

    def __init__(self, channel, by, why):
        self.channel, self.by, self.why = channel, by, why
        self.told, self.did, self.changed, self.vouch = [], None, False, ""
        press = self

        class Response(object):
            async def defer(self):
                pass

            async def send_message(self, text=None, ephemeral=False, **kw):
                press.told.append(text or "")

            async def edit_message(self, **kw):
                press.told.append(GONE_MENU)

            async def send_modal(self, modal):
                press.told.append(GONE_MENU)

        class Followup(object):
            async def send(self, text=None, ephemeral=False, **kw):
                press.told.append(text or "")
        self.response, self.followup = Response(), Followup()

    def note(self, did=None, changed=False):
        self.did, self.changed = did, changed


async def answer_menu(bridge, ch, sender_ch, token, post, why, refuse):
    menus = bridge.menus
    m = menus.open.get(token)
    if m is None or m.get("channel") != ch.id:
        return await refuse(GONE_MENU)
    if not menu_kind(m):
        return await refuse(PANEL)
    if m.get("deadline") or last_check(m):
        return await refuse(COUNTDOWN)
    key, text = str(post.get("key") or "").strip().lower(), str(post.get("text") or "")
    if text and not key:
        what = "text"
    elif key.isdigit() and 0 < int(key) < 10 and not text:
        what = key
    elif key in KEYS and not text:  # (C-189: the keys the owner's /key would press on a menu, as named keys only)
        what = KEYS[key]
    else:
        return await refuse(BAD % "選單要用 --key <數字>、--key %s，或只給 --text \"…\"" % "／".join(KEYS))
    if what.isdigit() and int(what) not in [n for n, _ in (m.get("shown") or ["", []])[1]]:
        return await refuse(BAD % ("選單沒有選項 %s" % what))
    p = Press(ch, who(sender_ch), why)
    p.vouch = vouch(post)
    await menus.act(p, token, what, text)
    if p.changed:
        return False, "沒送：畫面已經變了（#%s 的選單換了或關了），請重新 --waiting" % ch.name
    if p.did is None:
        return await refuse(p.told[-1] if p.told else GONE_MENU)
    return True, "已在 #%s 的選單%s" % (ch.name, p.did)


async def answer_pin(bridge, ch, sender_ch, post, why, refuse):
    """C-187 (manager 11-12): the reminder of a pinned Claude Code answered as its buttons would: --pick one of their
    labels. Its buttons come off, the channel is told who chose, and how to change it back."""
    pinned, watch = cc_health.pin_asking(), getattr(bridge, "cc", None)
    if not pinned or watch is None or pinned["card"][0] != ch.id:
        return await refuse(GONE_PIN)
    keep, new = watch.pin_choices(pinned)
    picks = list(post.get("pick") or [])
    if len(picks) != 1 or picks[0] not in (keep, new):
        return await refuse(BAD % ("--pick 要是「%s」或「%s」" % (keep, new)))
    said = await watch.choose_pin(picks[0] == keep, by=who(sender_ch), why=why, mark=vouch(post))
    if said is None:
        return await refuse(GONE_PIN)
    try:
        await deliver.deliver(ch, view=None, intent="card", key="ccpin", edit=pinned["card"][1])
    except Exception:
        traceback.print_exc()
    try:
        await deliver.deliver(ch, said, silent=True, intent="status")
    except Exception:
        traceback.print_exc()
    return True, "已回答 #%s 停在舊版 Claude Code 的提醒：%s" % (ch.name, picks[0])
