"""Who a message the bot sends may ping (messaging review P0-1). The client's default is nobody(): a reply, a card, or a
<@…>, role or @everyone in what Claude wrote pings no one. The two lines that call the owner (a long turn's footer,
the talk limit's ⏸️) pass owner_only()."""
import discord

from . import config


def nobody():
    return discord.AllowedMentions.none()


def owner_only():
    """Pings the owner and nobody else (a reply's author neither); no owner set: nobody."""
    return discord.AllowedMentions(everyone=False, roles=False, replied_user=False,
                                   users=[discord.Object(config.OWNER_ID)] if config.OWNER_ID else False)
