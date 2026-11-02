"""The 填入 button for a plugin's secret (C-77 G3b).

`altention dc post <channel> --secret <plugin>/<name> ["text"]` posts the text with a button; the owner presses it,
Discord shows a box (a modal), and what is typed there is handed to the core's store, which writes the file and says
it in #audit (altention/secret_store.py, C-77 G3a). The bot never writes the file and keeps no copy.

- Only a name the plugin declares in plugin.json's secrets[] gets a button (secret_store.declared(), read in a thread,
  READ_SECS at most); the declaration's `label` names the button and the box.
- The button's custom_id is `sec:<token>` and the box's `secm:<token>`: 32 hex characters from secrets.token_hex(16).
  Which secret it is (plugin, name, label) is kept in the channel's state under `secret_boxes` by that token (the last
  KEEP, for KEEP_DAYS), so nothing a client sends says which secret it writes, and both work after a restart: the box
  is not left in discord.py's own store (stop() right after it is shown); its answer comes to on_interaction by
  custom_id like the button.
- Only the owner may press it or send the box; anyone else is told, alone. A box closed without sending sends nothing:
  nothing happens and the button stays.
- The value goes to `secret_store.py set <plugin> <name> --stdin --via discord` on stdin, never on a command line,
  with a time limit (STORE_SECS). Its lines (存好了／沒有存, then which other machines use the plugin and how to send
  it there: the bot never sends it on, manager 2026-10-24) are what the owner is told, alone. The value is never
  logged, written, kept in the state, echoed, or put in a message or an exception's text: a failure says only its kind
  or exit code. Discord's box has no hidden field: the posted message says so (the owner's ruling).
- Discord takes at most MAX characters in a box: a longer value is for the terminal (`altention secret set`).
"""
import asyncio
import os
import re
import secrets
import sys
import time

import discord

from . import config, messages
from .util import split_message

KEEP, KEEP_DAYS = 20, 7          # buttons remembered in a channel's state
READ_SECS = 2                    # the plugin's declaration read before posting, at most this long
STORE_SECS = 90                  # the core's store, at most this long (its #audit post takes up to 60 s)
MAX = 4000                       # Discord's longest text input
TITLE_MAX = 45                   # Discord's longest modal title and input label
REPLY_MAX = 1900                 # the store's lines told to the owner, at most this long (one message)
TOKEN = re.compile(r"^[0-9a-f]{32}$")
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
STORE = os.path.join(os.path.dirname(config.ROOT), "altention", "secret_store.py")


def say(key, /, **data):
    return messages.words("secret", key, data)


def declared(plugin, name):
    """(label, None) when the plugin declares the secret `name` here, else (None, why): the core's own reading."""
    from altention import plugin as alt_plugin, secret_store
    from altention.text import t
    try:
        if name not in secret_store.declared(plugin):
            return None, t("secret.not_declared", plugin=plugin, name=name)
    except secret_store.Refused as e:
        return None, str(e)
    rows, _ = alt_plugin.on_this_machine()
    for n, _folder, d, _where, _why in rows:
        if n == plugin and d is not None:
            for s in d.get("secrets", []):
                if s.get("name") == name:
                    return (s.get("label") or name), None
    return name, None


async def declared_now(plugin, name):
    try:
        return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, declared, plugin, name), READ_SECS)
    except Exception as e:  # (a timeout, a plugin.json that breaks the reader): not posted
        print("[secret] the declaration of %s cannot be read (%s)" % (plugin, type(e).__name__), flush=True)
        return None, say("unreadable")


def remember(st, plugin, name, label, now=None):
    """A new button's token, with which secret it is kept in the channel's state `st` (oldest dropped)."""
    now = time.time() if now is None else now
    kept = st.setdefault("secret_boxes", {})
    for token in [t for t, b in kept.items() if not isinstance(b, dict) or not isinstance(b.get("at"), (int, float))
                  or now - b["at"] > KEEP_DAYS * 86400]:
        kept.pop(token)
    while len(kept) >= KEEP:
        kept.pop(min(kept, key=lambda t: kept[t]["at"]))
    token = secrets.token_hex(16)
    kept[token] = {"plugin": plugin, "name": name, "label": label, "at": now}
    return token


def found(st, token, now=None):
    """What the token stands for, or None: unknown, malformed, or older than KEEP_DAYS."""
    now = time.time() if now is None else now
    b = (st.get("secret_boxes") or {}).get(token) if TOKEN.match(token or "") else None
    if not isinstance(b, dict) or not isinstance(b.get("at"), (int, float)) or now - b["at"] > KEEP_DAYS * 86400:
        return None
    if not (NAME.match(str(b.get("plugin"))) and NAME.match(str(b.get("name")))):
        return None
    return b


def button(label, token):
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label=say("button", label=label)[:80], style=discord.ButtonStyle.primary,
                                 custom_id="sec:" + token))
    return v


def box(label, token):
    m = discord.ui.Modal(title=say("box_title", label=label)[:TITLE_MAX], custom_id="secm:" + token, timeout=None)
    m.add_item(discord.ui.TextInput(label=say("box_field", label=label)[:TITLE_MAX], custom_id="value",
                                    style=discord.TextStyle.paragraph, placeholder=say("box_hint")[:100],
                                    max_length=MAX, required=True))
    return m


def typed(data):
    """The text input's value in a modal's answer (rows, or the newer label components), or None."""
    def walk(items):
        for c in items or ():
            if not isinstance(c, dict):
                continue
            if c.get("custom_id") == "value" and isinstance(c.get("value"), str):
                return c["value"]
            got = walk(c.get("components")) if c.get("components") else walk([c["component"]]) if c.get("component") else None
            if got is not None:
                return got
        return None
    return walk((data or {}).get("components"))


async def store(plugin, name, value):
    """Hand `value` to the core's store. The line for the owner (its own, or why it did not answer)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, STORE, "set", plugin, name, "--stdin", "--via", "discord",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError as e:
        return say("failed", why=type(e).__name__)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(value.encode("utf-8")), STORE_SECS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return say("slow", secs=STORE_SECS)
    lines = [x for x in out.decode("utf-8", "replace").splitlines() if x.strip()]
    if proc.returncode in (0, 1) and lines:
        return "\n".join(lines)[:REPLY_MAX]  # its lines as it printed them: 存好了, and which other machines use the plugin
    return say("failed", why=say("exit_code", code=proc.returncode))


class Secrets(object):
    def __init__(self, bot):
        self.bot = bot

    async def post(self, ch, text, post, embeds_of=None):
        """Post `text` (or the default line) with the note under it, the 填入 button on the last message; `embeds_of`:
        makes cards of a text (posts elsewhere than the sender's channel). (None, that message) or (why not, None)."""
        plugin, _, name = str(post.get("secret") or "").partition("/")
        if not (NAME.match(plugin) and NAME.match(name)):
            return say("bad_spec"), None
        label, why = await declared_now(plugin, name)
        if why is not None:
            return why, None
        token = remember(self.bot.chan(ch), plugin, name, label)
        self.bot.save()
        whole = "%s\n%s" % (text or say("default", plugin=plugin, label=label), say("note", plugin=plugin, name=name))
        view, message = button(label, token), None
        if embeds_of is not None:
            embeds = embeds_of(whole)
            for i, e in enumerate(embeds):
                message = await (ch.send(embed=e, view=view) if i == len(embeds) - 1 else ch.send(embed=e))
        else:
            parts = split_message(whole)
            for i, part in enumerate(parts):
                message = await ch.send(part, view=view if i == len(parts) - 1 else None,
                                        allowed_mentions=discord.AllowedMentions.none())
        return None, message

    async def on_interaction(self, interaction):
        cid = (interaction.data or {}).get("custom_id") or ""
        if interaction.type == discord.InteractionType.component and cid.startswith("sec:"):
            step = self.pressed
        elif interaction.type == discord.InteractionType.modal_submit and cid.startswith("secm:"):
            step = self.sent
        else:
            return False
        try:
            await step(interaction, cid.partition(":")[2])
        except Exception as e:  # never the traceback: a modal's answer is in the frames' data
            print("[secret] %s failed (%s)" % (step.__name__, type(e).__name__), flush=True)
        return True

    def lookup(self, interaction, token):
        st = self.bot.channels_state.get(str(getattr(interaction.channel, "id", ""))) or {}
        return found(st, token)

    async def pressed(self, interaction, token):
        alone = lambda text: interaction.response.send_message(text, ephemeral=True)  # noqa: E731
        if interaction.user.id != config.OWNER_ID:
            return await alone(say("owner_only"))
        b = self.lookup(interaction, token)
        if b is None:
            return await alone(say("expired"))
        m = box(b["label"], token)
        await interaction.response.send_modal(m)
        m.stop()  # its answer comes to on_interaction by custom_id, also after a restart

    async def sent(self, interaction, token):
        alone = lambda text: interaction.response.send_message(text, ephemeral=True)  # noqa: E731
        if interaction.user.id != config.OWNER_ID:
            return await alone(say("owner_only"))
        b = self.lookup(interaction, token)
        if b is None:
            return await alone(say("expired"))
        value = typed(interaction.data)
        if value is None:
            return await alone(say("failed", why=say("no_value")))
        await interaction.response.defer(ephemeral=True, thinking=True)  # the store may take longer than Discord's 3 s
        try:
            line = await store(b["plugin"], b["name"], value)
        finally:
            value = None
        await interaction.followup.send(line, ephemeral=True)
