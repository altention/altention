"""The 打開 button of a plugin's Discord Activity page (C-54a).

`altention dc post <channel> --activity <plugin>/<page> [--arg k=v …] "text"` posts the text with a button that opens
that page. A plugin declares its pages in plugin.json (altention.plugin.check()):
    "activity": {"pages": [{"id": "files", "title": "看檔案", "access": "owner" | "guild"}]}
"owner" (the default): only the owner may open it; "guild": anyone in this server.

- The button's custom_id is `act:<token>`: 32 hex characters from `secrets.token_hex(16)`. What it opens (plugin, page,
  args) is kept in the channel's state under `activity` by that token (the last KEEP, for KEEP_DAYS), so a button
  works after a restart and nothing a client sends says which page it is: an unknown token opens nothing, nor one
  older than KEEP_DAYS (also when no newer button of the channel has dropped it yet).
- On a press, the plugins' declarations are read again (in a thread, READ_SECS at most: Discord wants an answer within
  3 s, and LAUNCH_ACTIVITY must be that answer) and the page's access *now* decides; a page gone, a plugin off, or
  declarations that cannot be read: nothing opens. An "owner" page pressed by anyone else: they are told, alone.
- Then the intent is written (intents()) and the bot answers LAUNCH_ACTIVITY. The plugin's back end, having checked
  who opened it, looks for that user's latest intent in that channel to know which page to show.
- Whether the application's Activities are on (its EMBEDDED flag) is known before the press: Discord answers
  LAUNCH_ACTIVITY with 400 (error 50234) without it, and after a failed first answer an interaction takes no other
  (404, "Unknown interaction": measured on the real application, #code-review-2 on 48739c8). `embedded` holds it:
  from the gateway's READY at the start, then read by watch() every FLAGS_EVERY on a task of its own; a press only
  looks at it (no network, Discord's 3 seconds). Known off: the owner is told and nothing is written or launched. On,
  or not known (never read: the network failed): launched; a refusal all the same is logged in one line and marks it
  off, so the next press says so. A read that fails keeps what was known.

The intents file, one for each plugin: ~/.local/state/altention/activity/<plugin>.json (worked out from HOME at call
time), mode 600, written whole through a temporary file:
    {"intents": [{"user": "<id>", "channel": "<id>", "guild": "<id>", "page": "<page>", "args": {…},
                  "access": "owner" | "guild", "at": <unix seconds>}]}
oldest first; the last INTENTS_KEPT, of the last INTENTS_SECS. A plugin reads only its own file and never writes it.
What is in it is no secret (who pressed what, where): every program of the owner's user can read the owner's files,
so a file mode keeps out other users, not other plugins."""
import asyncio
import json
import os
import re
import secrets
import tempfile
import time
import traceback

import discord

from . import config
from .util import split_message

KEEP, KEEP_DAYS = 50, 7          # buttons remembered in a channel's state
INTENTS_KEPT, INTENTS_SECS = 20, 600
READ_SECS = 2                    # the declarations read again on a press, at most this long
FLAGS_EVERY = 600                # the application's flags read again (watch())
NOT_EMBEDDED = 50234             # Discord's error code: LAUNCH_ACTIVITY without the EMBEDDED flag
embedded = {"on": None}          # are the application's Activities on: True, False, or None (not known)
TOKEN = re.compile(r"^[0-9a-f]{32}$")
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
NOT_SET_UP = "Activity 還沒設好，打不開：到 #setup 跟 Claude 說「幫我裝完 %s」。"


def intents_path(plugin):
    """Where `plugin`'s intents are written (from HOME now: a test with its own HOME writes nowhere else)."""
    return os.path.join(os.path.expanduser("~"), ".local", "state", "altention", "activity", plugin + ".json")


def pages(rows=None):
    """{(plugin, page): {"title", "access"}} of the plugins on this machine (plugin.on_this_machine(); `rows` for the
    tests). Raises when the declarations cannot be read: the caller opens nothing then."""
    from altention import plugin
    if rows is None:
        rows = plugin.on_this_machine()[0]
    out = {}
    for name, _, d, where, _ in rows:
        if where != "on" or not isinstance(d, dict) or not isinstance(d.get("activity"), dict):
            continue
        for p in d["activity"].get("pages") or []:
            if isinstance(p, dict) and isinstance(p.get("id"), str):
                access = p.get("access", "owner")
                out[(name, p["id"])] = {"title": p.get("title") if isinstance(p.get("title"), str) else p["id"],
                                        "access": access if access in ("owner", "guild") else None}
    return out


async def pages_now(rows=None):
    """pages() in a thread, READ_SECS at most; None when it fails or takes longer."""
    try:
        return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, pages, rows), READ_SECS)
    except Exception as e:
        print("[activity] the plugins' pages cannot be read (%s: %s): nothing opens" % (type(e).__name__, e), flush=True)
        return None


def remember(st, plugin, page, args, now=None):
    """A new button's token, with what it opens kept in the channel's state `st` (oldest dropped)."""
    now = time.time() if now is None else now
    kept = st.setdefault("activity", {})
    for token in [t for t, b in kept.items() if not isinstance(b, dict) or not isinstance(b.get("at"), (int, float))
                  or now - b["at"] > KEEP_DAYS * 86400]:
        kept.pop(token)  # (a time that is not a number, written by hand: as gone as pressed() takes it)
    while len(kept) >= KEEP:
        kept.pop(min(kept, key=lambda t: kept[t]["at"]))
    token = secrets.token_hex(16)
    kept[token] = {"plugin": plugin, "page": page, "args": dict(args or {}), "at": now}
    return token


def button(title, token):
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label=("打開 %s" % title)[:80], style=discord.ButtonStyle.primary, custom_id="act:" + token))
    return v


def write_intent(plugin, intent, now=None):
    """Add `intent` to `plugin`'s intents file (INTENTS_KEPT, INTENTS_SECS), mode 600. Raises OSError."""
    now = time.time() if now is None else now
    path = intents_path(plugin)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        with open(path) as f:
            old = json.load(f).get("intents") or []
    except (OSError, ValueError, AttributeError):
        old = []
    keep = [i for i in old if isinstance(i, dict) and isinstance(i.get("at"), (int, float)) and now - i["at"] <= INTENTS_SECS]
    keep = (keep + [intent])[-INTENTS_KEPT:]
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + plugin + ".")
    try:
        with os.fdopen(fd, "w") as f:  # mkstemp makes it 600
            json.dump({"intents": keep}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def note_flags(flags):
    """Take the application's flags (discord.ApplicationFlags, or anything with `embedded`) as known; None: not read."""
    on = getattr(flags, "embedded", None)
    if isinstance(on, bool) and on != embedded["on"]:
        print("[activity] the application's Activities are %s" % ("on" if on else "off (its EMBEDDED flag is not set)"), flush=True)
    if isinstance(on, bool):
        embedded["on"] = on


class Activity(object):
    def __init__(self, bot):
        self.bot = bot

    async def watch(self):
        """The application's flags, from the gateway's READY first, then read again every FLAGS_EVERY (a task of its
        own: never on a press). A read that fails keeps what was known."""
        try:
            note_flags(getattr(self.bot, "application_flags", None))
        except Exception:
            pass
        while True:
            try:
                note_flags((await self.bot.application_info()).flags)
            except Exception as e:
                print("[activity] the application's flags cannot be read (%s): %s" % (type(e).__name__,
                      "kept as %s" % embedded["on"] if embedded["on"] is not None else "not known"), flush=True)
            await asyncio.sleep(FLAGS_EVERY)

    async def post(self, ch, text, post, embeds=None):
        """Post `text` (split at Discord's 2000), or `embeds` (cards), whole, with the 打開 button for post["activity"]
        ("<plugin>/<page>") on the last message. (None, that message) or (why it was not posted, None)."""
        plugin, _, page = str(post.get("activity") or "").partition("/")
        args = post.get("args") if isinstance(post.get("args"), dict) else {}
        if not (NAME.match(plugin) and NAME.match(page)):
            return "--activity 要寫成 <插件>/<頁>", None
        found = await pages_now()
        if found is None:
            return "讀不到插件的設定，沒有貼出", None
        spec = found.get((plugin, page))
        if spec is None:
            return "沒有開著的插件 %s 宣告了 Activity 頁 %s" % (plugin, page), None
        st = self.bot.chan(ch)
        token = remember(st, plugin, page, args)
        self.bot.save()
        view, message = button(spec["title"], token), None
        if embeds:
            for i, e in enumerate(embeds):
                message = await (ch.send(embed=e, view=view) if i == len(embeds) - 1 else ch.send(embed=e))
        else:
            parts = split_message(text) or ["打開 %s" % spec["title"]]
            for i, part in enumerate(parts):
                message = await ch.send(part, view=view if i == len(parts) - 1 else None,
                                        allowed_mentions=discord.AllowedMentions.none())
        return None, message

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        cid = (interaction.data or {}).get("custom_id") or ""
        if not cid.startswith("act:"):
            return False
        try:
            await self.pressed(interaction, cid[4:])
        except Exception:
            traceback.print_exc()
        return True

    async def pressed(self, interaction, token):
        alone = lambda text: interaction.response.send_message(text, ephemeral=True)  # noqa: E731
        st = self.bot.channels_state.get(str(getattr(interaction.channel, "id", ""))) or {}
        b = (st.get("activity") or {}).get(token) if TOKEN.match(token) else None
        if not isinstance(b, dict) or not isinstance(b.get("at"), (int, float)) or time.time() - b["at"] > KEEP_DAYS * 86400:
            return await alone("這顆按鈕已經失效了，請 Claude 再貼一次。")
        found = await pages_now()
        if found is None:
            return await alone("現在讀不到插件的設定，沒有打開。等一下再按一次。")
        spec = found.get((b.get("plugin"), b.get("page")))
        if spec is None:
            return await alone("這一頁已經不在了（插件關了，或不再提供這一頁）。")
        user = interaction.user.id
        if spec["access"] == "owner" and user != config.OWNER_ID:
            return await alone("這一頁只有擁有者能打開。")
        if spec["access"] not in ("owner", "guild") or \
                (spec["access"] == "guild" and getattr(interaction, "guild_id", None) != config.GUILD_ID):
            return await alone("這一頁不能在這裡打開。")  # (a "guild" page: this server's people, in this server)
        if embedded["on"] is False:  # known before the one answer Discord takes (a refused LAUNCH_ACTIVITY ends the interaction)
            return await alone(NOT_SET_UP % b["plugin"])
        intent = {"user": str(user), "channel": str(interaction.channel.id), "guild": str(getattr(interaction, "guild_id", "") or ""),
                  "page": b["page"], "args": b.get("args") or {}, "access": spec["access"], "at": time.time()}
        try:
            write_intent(b["plugin"], intent)
        except OSError as e:
            print("[activity] the intent for %s cannot be written (%s)" % (b["plugin"], e), flush=True)
            return await alone("寫不進要打開哪一頁（%s），沒有打開。" % type(e).__name__)
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as e:  # no second answer: the interaction is gone (404), so nothing more is sent
            if getattr(e, "code", None) == NOT_EMBEDDED:
                embedded["on"] = False  # the next press says it (watch() takes it back once the flag is there)
            print("[activity] LAUNCH_ACTIVITY refused (%s, code %s): %s; the presser sees Discord's own failure"
                  % (getattr(e, "status", "?"), getattr(e, "code", "?"), str(getattr(e, "text", ""))[:200]), flush=True)
