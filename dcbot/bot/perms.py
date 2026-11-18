"""The bot's own permissions on the server (C-191; owner 11-12, manager 11-17): looked at once it is online and
whenever its role changes, in the background (from the cache Discord's events keep, no request: never in the pump's
way; anything that goes wrong is skipped, nothing waits on it), and kept in state/permissions.json for doctor.

- The first time: one #audit line of what they are. A change since the last time: one #audit line, before and after
  (Discord's audit log does not record it).
- No Administrator: one card in #update (once, ever, through the post queue; doctor keeps saying it after) with the link that grants it on
  this server (altention/dc/invite.py: the 14 the features use and Administrator) and why; given later: the card says
  so, with the link that takes it back; taken back after that: the same card asks again (edited, never a new one). Nothing in the bot needs Administrator today; a feature that will asks
  has_admin() first and says need_admin() instead of failing.

The bot cannot raise its own permissions: the owner opens the link (the same page as adding it: nothing reinstalled,
the bot never removed) or turns Administrator on for its role by hand."""
import asyncio
import json
import os
import time
import traceback

from altention.dc import invite

from . import config, messages, postbox

FILE = "permissions.json"  # in config.STATE (synced to the standby: bot/standby.py SYNCED)
CARD = "bot-admin"  # the #update card's id (bot/update_cards.py)


def path():
    return os.path.join(config.STATE, FILE)


def load():
    try:
        with open(path()) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def save(st):
    os.makedirs(config.STATE, exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path())


def said(bits):
    """Permissions in the owner's words: whether Administrator is there, and which of the 14 are missing."""
    gone = invite.missing(bits)
    return messages.perms("admin" if invite.has_admin(bits) else "no_admin") + \
        ("，" + messages.perms("missing", names="、".join(gone)) if gone else "")


def has_admin(bot):
    """Whether the bot has Administrator on the server now (from the cache); False when that cannot be told."""
    try:
        me = bot.get_guild(config.GUILD_ID).me
        return invite.has_admin(me.guild_permissions.value)
    except Exception:
        return False


def need_admin():
    """What a feature that needs Administrator says when has_admin() is False."""
    return messages.perms("need_admin")


class Perms(object):
    def __init__(self, bot):
        self.bot = bot
        self.lock = asyncio.Lock()

    def soon(self):
        """Look in the background (on_ready, a role of the bot's changed): never waited on."""
        try:
            self.bot.loop.create_task(self.check())
        except Exception:
            traceback.print_exc()

    async def check(self):
        try:
            async with self.lock:
                await self.look()
        except Exception:  # (a look that breaks is skipped: the next event or start looks again)
            print("[perms] could not look at the bot's permissions:", flush=True)
            traceback.print_exc()

    async def look(self):
        guild = self.bot.get_guild(config.GUILD_ID)
        me = getattr(guild, "me", None)
        if me is None:  # (not ready, or not on the server: nothing known)
            return
        bits = int(me.guild_permissions.value)
        app = getattr(self.bot, "application_id", None)
        st = load()
        was = st.get("bits") if isinstance(st.get("bits"), int) else None
        now = dict(st, bits=bits, admin=invite.has_admin(bits), missing=invite.missing(bits), at=time.time(),
                   guild=guild.id)
        if app:
            now["grant_url"], now["revert_url"] = invite.url(app, guild.id), invite.url(app, guild.id, admin=False)
        save(now)  # (first: a card or a line that fails is not said twice by looking again)
        if was is None:
            await self.bot.audit(messages.perms("audit_now", state=said(bits), bits=bits))
        elif was != bits:
            await self.bot.audit(messages.perms("audit_changed", before=said(was), after=said(bits), was=was, bits=bits))
        if not now.get("grant_url"):
            return
        if not now["admin"] and (not st.get("carded") or st.get("granted")):  # (taken back after it was given: the
            # same card says again how to give it, not that it is there; #code-review on 5c7b6a1e)
            card({"buttons": [], "text": messages.perms("card", grant=now["grant_url"], revert=now["revert_url"])})
            now.pop("granted", None)
            save(dict(now, carded=st.get("carded") or time.time()))
        elif now["admin"] and st.get("carded") and not st.get("granted"):
            card({"buttons": [], "close": True,
                  "text": messages.perms("granted", revert=now["revert_url"], when=time.strftime("%m-%d %H:%M"))})
            save(dict(now, granted=time.time()))


def card(fields):
    """The #update card, left in the bot's own post queue (a file, at once): the pump shows it like altention.dc.card's
    (bot/update_cards.py), tried again when Discord does not take it."""
    postbox.write({"card": dict(fields, id=CARD, at=time.time()), "from_name": "機器人（權限檢查）"})
