"""What a message that comes in from a tool (dcctl send-json and edit now, altention dc post --json in P1d) may do,
on top of deliver() (C-172a P1c, instance docs/refactor/c172a-design.md): whom it may ping, which buttons it may
carry, which message of the bot's it may change. Pure functions of what they are handed: each returns why a message
is refused (a sentence for the one who sent it), or None. The reserved frames stay bot/rawpost.py's (P0-6).

The sender (`who`) is a key of bot/senders.py, looked up where the tool ran (dcctl's sender_of(), C-186 since P1d):
the same system account, so a program set on it can still pass for another (manager 11-17).
Standard library only: tools/dcctl runs this with the system Python."""
import re

from . import senders, util

EVERYONE = re.compile(r"@(everyone|here)\b")
PINGS = ("owner", "user", "role")  # what a ping may name: the owner, or (when the instance lets it) a user or a role
CHANGEABLE = ("post",)  # the intents a tool may edit or delete: what a tool posted itself (bot/ledger.py)


def ping_parsed(ping):
    """`ping` as (kind, id) pairs: "owner", "user:ID", "role:ID"; None when one of them is neither."""
    out = []
    for p in ping or ():
        p = str(p).strip()
        kind, _, num = p.partition(":")
        if p == "owner":
            out.append(("owner", None))
        elif kind in ("user", "role") and num.isdigit():
            out.append((kind, int(num)))
        else:
            return None
    return out


def allow_of(said):
    """The instance's dcbot.json_mentions as what a ping may name: "any", or "owner" (the default, and anything else)."""
    return "any" if said == "any" else "owner"


def ping_refused(ping, allow="owner", how="send-json"):
    """Why a ping is refused, or None: the owner always may be; a user or a role only when the instance's
    dcbot.json_mentions is "any" (`allow`); @everyone and @here never."""
    if any(str(p).strip().lstrip("@") in ("everyone", "here") for p in ping or ()):
        return "%s：沒有送出：不能 @everyone 或 @here" % util.tool(how)
    got = ping_parsed(ping)
    if got is None:
        return "%s：沒有送出：--mention 只能寫 owner、user:ID 或 role:ID" % util.tool(how)
    if allow != "any" and any(kind != "owner" for kind, _ in got):
        return "%s：沒有送出：這個安裝只允許 @ 使用者本人（--mention owner）；要 @ 其他人或身分組，實例的 dcbot.json_mentions 要設成 \"any\"" % util.tool(how)
    return None


def mentions(ping, owner_id):
    """The payload's allowed_mentions for an accepted ping: nobody but whom it names (checked by ping_refused()); IDs
    as strings, as dcctl send writes them."""
    users = [str(owner_id) if kind == "owner" else str(num) for kind, num in ping_parsed(ping) or ()
             if kind in ("owner", "user") and (kind != "owner" or owner_id)]  # (no owner set: nobody)
    roles = [str(num) for kind, num in ping_parsed(ping) or () if kind == "role"]
    out = {"parse": []}
    if users:
        out["users"] = sorted(set(users))
    if roles:
        out["roles"] = sorted(set(roles))
    return out


def owner_pinged(body, owner_id, allowed=None):
    """Whether posting `body` pings the owner: <@owner> (or <@!owner>) written in its words (content, or V2 Text
    Displays; an embed's never ping) and let through by `allowed` (its allowed_mentions; default the body's own). A
    --mention owner that the words do not use pings nobody, and #audit does not say it did (L3's live test, manager
    11-17)."""
    if not owner_id:
        return False
    allowed = body.get("allowed_mentions") if allowed is None else allowed
    if str(owner_id) not in [str(u) for u in (allowed or {}).get("users") or ()]:
        return False
    content = body.get("content") if isinstance(body.get("content"), str) else ""
    words = content + "\n" + util.components_text(body.get("components"))
    return re.search(r"<@!?%s>" % re.escape(str(owner_id)), words) is not None


def everyone_refused(body, how="send-json"):
    """Why a payload is refused for writing @everyone or @here in its words (content, or V2 Text Displays), or None:
    it would not ping, but it reads as if it did (manager 11-16). An embed's words never ping and are not looked at."""
    content = body.get("content") if isinstance(body.get("content"), str) else ""
    if EVERYONE.search(content) or EVERYONE.search(util.components_text(body.get("components"))):
        return "%s：%s：內容裡有 @everyone 或 @here（不會響，但看起來像在叫所有人），請拿掉" % (
            util.tool(how), "沒有改" if how == "edit" else "沒有送出")
    return None


def custom_ids(components):
    """Every custom_id in a payload's components, through action rows, containers, sections and accessories."""
    out = []

    def walk(items):
        for c in items or ():
            if not isinstance(c, dict):
                continue
            if "custom_id" in c:
                out.append(c["custom_id"])
            walk(c.get("components"))
            if isinstance(c.get("accessory"), dict):
                walk([c["accessory"]])
    walk(components if isinstance(components, list) else ())
    return out


def buttons_refused(body, how="send-json"):
    """Why a payload is refused for its buttons or menus, or None: every custom_id a tool sends begins "user:" (the
    bot's own prefixes stay the bot's); a link button has none and is never refused."""
    bad = [str(c) for c in custom_ids(body.get("components")) if not str(c).startswith("user:")]
    if bad:
        return "%s：沒有送出：按鈕和選單的 custom_id 要以 user: 開頭（%s 不是；連結按鈕不用）" % (util.tool(how), "、".join(bad[:3]))
    return None


def change_refused(entry, who, how="edit", ids_of_name=None):
    """Why a tool may not edit a message the ledger has as `entry` (None: not there), or None: only the sender's own
    (in another's channel its line of where it comes from must survive the edit too, Posts.edited(): a Claude's
    without one is not editable there; a program's, which never has one, by its key alone; manager 11-17): a post of
    its own (`who`, a key of bot/senders.py, the same as the entry's from, or as what the words written there before
    P1d turn into: until bot/fromkeys.py has turned them, an edit of one's own is not refused, manager 11-17;
    `ids_of_name`: the text channels' IDs by name, for "#name", when known). A card (a question, a plan, a
    permission menu, an update or setup card) never; a board goes by its own way (--board); a 📨 relay card is not
    edited (it would lose where it comes from). Taking a message away goes by other rules (bot/deleting.py, C-205)."""
    if not entry:
        return "%s：沒有改：機器人的紀錄（ledger）裡沒有這則，只能改用 dcctl send／send-json 發的訊息" % util.tool(how)
    intent = entry.get("intent")
    if intent == "card":
        return "%s：沒有改：這則是機器人的卡片（提問、計畫、權限選單、更新或設定），按鈕和內容都由機器人管" % util.tool(how)
    if intent == "board":
        return "%s：沒有改：這則是看板，看板要用 --board 更新" % util.tool(how)
    if intent not in CHANGEABLE:
        return "%s：沒有改：這則不是用工具發的訊息（是機器人或 Claude 的回覆、通知或狀態）" % util.tool(how)
    if "card" in (entry.get("framed") or ()):  # (a post's 📨 card in another's channel: changed, it would read as
        return "%s：沒有改：這則是「📨 來自」轉傳卡片，改了就沒有來源；請重新發一則" % util.tool(how)  # nobody's; manager 11-17)
    said = entry.get("from")
    if said != who and senders.old_key(said, ids_of_name or (lambda name: ())) != who:
        return "%s：沒有改：這則是 %s 發的，只能改自己發的" % (util.tool(how), senders.who(entry.get("from")) or "機器人")
    return None


def refused(body, ping=(), allow="owner", how="send-json"):
    """Why a payload to post is refused by these rules, or None (the reserved frames: rawpost.refused())."""
    return ping_refused(ping, allow, how) or everyone_refused(body, how) or buttons_refused(body, how)
