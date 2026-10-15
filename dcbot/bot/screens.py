"""/screen: the terminal as a picture, its text one tap away.

The screen is posted as a PNG (bot/screenshot.py) with a 📝 reaction already on it; when the owner taps 📝,
the same screen as text (what /screen used to post) comes as a reply, for copying. The text is the one
captured with the picture, kept in the channel's state (st["screens"]: message id -> text), so it matches
the picture even after the screen has moved on.
"""
import asyncio
import io
import traceback

import discord

from . import tmuxio
from .util import split_message

EMOJI = "📝"
KEEP = 10  # screens remembered per channel


def fenced(text, limit=1900):
    """The screen as code blocks, split between lines so each fits in a message."""
    rows = [r.rstrip() for r in text.replace("```", "'''").split("\n") if r.strip(" ─")] or ["（空白）"]
    return ["```\n%s\n```" % part for part in split_message("\n".join(rows), limit - 8)]


def remember(st, message_id, text):
    screens = st.setdefault("screens", {})
    screens[str(message_id)] = text
    for old in list(screens)[:-KEEP]:
        screens.pop(old, None)


async def post(bot, ch, t, send):
    """Post the screen of `t` (a session name or pane address) as a picture. `send` posts a message and
    returns it; falls back to the text if the picture cannot be made."""
    colored, cols = await tmuxio.color_screen(t)
    text = await tmuxio.screen(t)
    try:
        from . import screenshot
        png = await asyncio.get_running_loop().run_in_executor(None, screenshot.render, colored, cols)
    except Exception:
        traceback.print_exc()
        for part in fenced(text):
            await send(part)
        return
    msg = await send("-# 按 %s 看文字版" % EMOJI, file=discord.File(io.BytesIO(png), "screen.png"))
    if msg is None:
        return
    remember(bot.chan(ch), msg.id, text)
    bot.save()
    try:
        await ch.get_partial_message(msg.id).add_reaction(EMOJI)
    except discord.HTTPException:
        pass


async def show_text(bot, payload):
    """The owner tapped 📝 on a screen picture: reply with its text."""
    ch = bot.get_channel(payload.channel_id)
    text = ((bot.channels_state.get(str(payload.channel_id)) or {}).get("screens") or {}).get(str(payload.message_id))
    if ch is None or text is None:
        return
    ref = ch.get_partial_message(payload.message_id)
    for i, part in enumerate(fenced(text)):
        await (ch.send(part, reference=ref, mention_author=False) if i == 0 else ch.send(part))
