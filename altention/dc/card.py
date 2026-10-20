"""altention.dc.card: a card in the update channel, one Discord message kept up to date in place, with buttons the
bot acts on (C-57; the bot's side is dcbot/bot/update_cards.py). Standard library only; runs on any machine, like
altention dc post (the post goes to dcctl post on the bot's machine and waits in its spool while the bot is down).

  from altention.dc import card
  card.show("update-v0.3.0", text, buttons=[{"id": "go", "label": "更新", "style": "primary",
                                             "action": "update_go", "arg": "v0.3.0"}], details=None, channel=None)
  card.close("update-v0.3.0", text=None)

card_id: letters, digits and ".-_", at most 40: the same id is always the same message (posted the first time,
edited after; posted again if deleted). text: the whole card each time (cut at TEXT_MAX, said so). buttons: the
whole row each time, at most BUTTONS_MAX; [] for none. Each button {"id", "label", "style", "action", "arg"}:
  update_go   arg "vX.Y.Z": the bot takes the buttons away and runs `bin/altention update go <arg> --card <card_id>`
              in a session of its own (setsid), its output in ~/.local/state/altention/update-go.log
  later       the card says it was put off, buttons away
  details     the card's `details` posted under it (as a file when long)
  ask_claude  arg: what the setup channel's Claude is handed (the bot refuses over 6000 characters and says so)
details: the long text `details` posts (at most DETAILS_MAX). channel: the update channel by default
(core.update_channel, else "update"); a channel that is not there: the default notification channel, said so.
close(): the buttons away, and the text replaced when given. Both return True once the post is queued; anything
else raises CardError with why.
"""
import os
import re
import socket
import time

from altention.dc import post as dcpost

ID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
BUTTON_ID = re.compile(r"^[A-Za-z0-9._-]{1,20}$")  # upd:<card id>:<button id> stays within Discord's 100
VERSION = re.compile(r"^v\d+\.\d+\.\d+$")
ACTIONS = ("update_go", "later", "details", "ask_claude")
STYLES = ("primary", "secondary", "danger")
TEXT_MAX, DETAILS_MAX, BUTTONS_MAX, LABEL_MAX = 1800, 8000, 5, 80


class CardError(Exception):
    pass


def update_channel():
    try:
        from altention import config
        said = config.setting("core.update_channel")
    except Exception:
        said = None
    return said.strip().lstrip("#") if isinstance(said, str) and said.strip().lstrip("#") else "update"


def check_button(b):
    if not isinstance(b, dict):
        raise CardError("按鈕要是 dict：%r" % (b,))
    if not isinstance(b.get("id"), str) or not BUTTON_ID.match(b["id"]):
        raise CardError("按鈕的 id 只能是英數和 .-_，最多 20 字：%r" % b.get("id"))
    if not isinstance(b.get("label"), str) or not b["label"].strip() or len(b["label"]) > LABEL_MAX:
        raise CardError("按鈕 %s 的 label 要有字，最多 %d 字" % (b["id"], LABEL_MAX))
    if b.get("style", "secondary") not in STYLES:
        raise CardError("按鈕 %s 的 style 只能是 %s" % (b["id"], "、".join(STYLES)))
    if b.get("action") not in ACTIONS:
        raise CardError("按鈕 %s 的 action 只能是 %s" % (b["id"], "、".join(ACTIONS)))
    if b["action"] == "update_go" and not (isinstance(b.get("arg"), str) and VERSION.match(b["arg"])):
        raise CardError("update_go 的 arg 要是版本（vX.Y.Z）：%r" % b.get("arg"))
    if b["action"] == "ask_claude" and not (isinstance(b.get("arg"), str) and b["arg"].strip()):
        raise CardError("ask_claude 的 arg 要有字")
    return {"id": b["id"], "label": b["label"].strip(), "style": b.get("style", "secondary"), "action": b["action"],
            "arg": b.get("arg") if b["action"] in ("update_go", "ask_claude") else None}


def build(card_id, text, buttons=(), details=None, channel=None, close=False, host=None):
    """The post for dcctl post (a dict). Raises CardError when something given is not what a card takes."""
    if not isinstance(card_id, str) or not ID.match(card_id):
        raise CardError("card_id 只能是英數和 .-_，最多 40 字：%r" % (card_id,))
    if not close and not (isinstance(text, str) and text.strip()):
        raise CardError("卡片要有內容")
    if text is not None and not isinstance(text, str):
        raise CardError("text 要是字串")
    if text and len(text) > TEXT_MAX:
        text = text[:TEXT_MAX - 30].rstrip() + "\n…（太長，後面截掉了）"
    buttons = list(buttons or [])
    if len(buttons) > BUTTONS_MAX:
        raise CardError("按鈕最多 %d 顆" % BUTTONS_MAX)
    buttons = [check_button(b) for b in buttons]
    if len(set(b["id"] for b in buttons)) != len(buttons):
        raise CardError("按鈕的 id 重複了")
    if details is not None and (not isinstance(details, str) or len(details) > DETAILS_MAX):
        raise CardError("details 要是字串，最多 %d 字" % DETAILS_MAX)
    host = host or socket.gethostname().split(".")[0]
    where = (channel or update_channel()).strip().lstrip("#")
    return {"card": {"id": card_id, "text": text, "buttons": [] if close else buttons, "details": details, "close": bool(close),
                     "at": time.time()},  # the bot applies a card only over an older one (a retried post comes late)
            "channel": where, "from_name": "%s 上的 altention update" % host, "from_host": host}


def send(post):
    try:
        code, said = dcpost.deliver(post, capture=True)
    except (Exception, SystemExit) as e:  # the machine list unreadable, ssh missing; no bot machine: dc post's sys.exit
        # (#code-review-2 of 12a1556: a SystemExit went past the caller's `except CardError` and ended update go)
        raise CardError("送不出去：%s: %s" % (type(e).__name__, e))
    if code != 0:
        raise CardError("送不出去：%s" % (said.strip()[-300:] or "結束碼 %d" % code))
    return True


def show(card_id, text, buttons=(), details=None, channel=None):
    return send(build(card_id, text, buttons, details, channel))


def close(card_id, text=None, channel=None):
    return send(build(card_id, text, [], None, channel, close=True))
