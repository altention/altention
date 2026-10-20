"""Where the bot's own channels stand on the server (C-57 D1): a new installation gets its basic layout on its first
start, the main area (no category) and the system category at the bottom:
  - in the system category (config.SYSTEM_CATEGORY, "系統"): #audit and #guide;
  - in the main area: #update (the engine's updates, config.UPDATE_CHANNEL) and #chat (main.first_chat).
An installation that is not new is left as it is here (moving its channels is the reconcile's, ops/setup.py, through
the spool's move_to: posts.Posts.move_to). Any category the bot makes later is made with new_category(), which keeps
the system category last if it was last (an installation whose owner put it elsewhere keeps it there, manager
2026-10-20)."""
import discord

from . import config

GUIDE_TOPIC = "機器人的使用說明和更新紀錄。這裡不聊天，機器人不會回應這個頻道的訊息。"  # as tools/dcctl's guide() makes it
UPDATE_TOPIC = "引擎更新的消息：有新版、更新好了、出了問題，都在這裡。這裡不聊天。"


def system_is_last(guild):
    """Is the system category there and below every other category?"""
    cats = list(getattr(guild, "categories", []) or [])
    system = next((c for c in cats if c.name == config.SYSTEM_CATEGORY), None)
    return system is not None and all(c.position < system.position for c in cats if c is not system)


async def keep_system_last(guild):
    """The system category, if there is one, moved below every other category. Never raises. discord.py's move puts a
    channel before the first one whose position is at least the one asked for (abc.GuildChannel._move): asking for
    the last category's own position would put it just above that one (#code-review of 1af1489), so one past it."""
    cats = list(getattr(guild, "categories", []) or [])
    system = next((c for c in cats if c.name == config.SYSTEM_CATEGORY), None)
    if system is None:
        return
    last = max(c.position for c in cats)
    if system.position >= last and sum(1 for c in cats if c.position == last) == 1:
        return
    try:
        await system.edit(position=last + 1)
    except discord.HTTPException as e:
        print("[layout] could not move %s to the bottom: %s" % (config.SYSTEM_CATEGORY, e), flush=True)


async def new_category(guild, name):
    """A category the bot makes: made, and the system category kept below it if it was the last one before (one the
    owner put elsewhere is left there: the owner's own dividers, manager 2026-10-20)."""
    was_last = system_is_last(guild)
    cat = await guild.create_category(name)
    if name != config.SYSTEM_CATEGORY and was_last:
        await keep_system_last(guild)
    return cat


async def category(guild, name):
    """The category by that name, made (below the others when it is the system one) when there is none."""
    return discord.utils.get(guild.categories, name=name) or await new_category(guild, name)


async def channel_in(guild, name, topic, cat):
    """The text channel by that name: made in `cat` (None: the main area) when there is none; one standing in no
    category is moved into `cat`; one inside another category is left where the owner put it. None if Discord refuses."""
    ch = discord.utils.get(guild.text_channels, name=name)
    try:
        if ch is None:
            return await guild.create_text_channel(name, topic=topic, category=cat)
        if cat is not None and getattr(ch, "category", None) is None:
            await ch.edit(category=cat)
    except discord.HTTPException as e:
        print("[layout] #%s: %s" % (name, e), flush=True)
    return ch


async def first_layout(guild):
    """A new installation's basic layout. Returns the #audit channel (or None)."""
    try:
        system = await category(guild, config.SYSTEM_CATEGORY)
    except discord.HTTPException as e:
        print("[layout] could not make %s: %s" % (config.SYSTEM_CATEGORY, e), flush=True)
        system = None
    audit = await channel_in(guild, config.AUDIT_CHANNEL, "機器人做的每件事都記在這裡", system)
    await channel_in(guild, config.GUIDE_CHANNEL, GUIDE_TOPIC, system)
    await channel_in(guild, config.UPDATE_CHANNEL, UPDATE_TOPIC, None)
    return audit
