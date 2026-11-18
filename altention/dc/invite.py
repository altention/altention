"""The permissions the bot asks for and the link that grants them (C-20, C-21; C-191: Administrator too), for the
installer (ops/discordapp.py), the bot (bot/perms.py: its own permissions, the #update card) and doctor. Standard
library only.

Since C-191 (owner 11-12, knowledge systems.md) the link asks for Administrator on top of the 14 the bot's features
use: the 14 stay in it, so a server owner who takes Administrator away later leaves the bot as it was before. The bot
cannot raise its own permissions; the owner opening the link again (guild_id filled in) changes the bot's role in
place, never removing the bot; the link without Administrator puts it back (tested on a test server, 11-12)."""
import urllib.parse

# The 14 permissions the bot's features use (docs.discord.com/developers/topics/permissions), by Discord's names.
PERMISSIONS = (
    ("VIEW_CHANNEL", 10), ("MANAGE_CHANNELS", 4), ("VIEW_AUDIT_LOG", 7), ("SEND_MESSAGES", 11),
    ("SEND_MESSAGES_IN_THREADS", 38), ("CREATE_PUBLIC_THREADS", 35), ("MANAGE_THREADS", 34), ("MANAGE_MESSAGES", 13),
    ("PIN_MESSAGES", 51), ("EMBED_LINKS", 14), ("ATTACH_FILES", 15), ("READ_MESSAGE_HISTORY", 16),
    ("ADD_REACTIONS", 6), ("USE_APPLICATION_COMMANDS", 31),
)
PERMISSION_BITS = sum(1 << bit for _, bit in PERMISSIONS)
ADMINISTRATOR = 1 << 3
WITH_ADMIN = PERMISSION_BITS | ADMINISTRATOR  # what a new installation's link asks for (manager 11-17)


def url(app_id, guild_id=None, admin=True):
    """The link that adds the bot to a server, or, with `guild_id`, changes its permissions on that one (the same
    page: "add to server"): both scopes, the 14 permissions and Administrator (`admin`), a server install."""
    q = [("client_id", app_id), ("scope", "bot applications.commands"),
         ("permissions", str(WITH_ADMIN if admin else PERMISSION_BITS)), ("integration_type", "0")]
    if guild_id:
        q.append(("guild_id", str(guild_id)))
    return "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(q)


def has_admin(bits):
    return bool(int(bits or 0) & ADMINISTRATOR)


def missing(bits):
    """The names of the 14 the bot lacks with these permissions (none with Administrator: it has them all)."""
    bits = int(bits or 0)
    if has_admin(bits):
        return []
    return [name for name, bit in PERMISSIONS if not bits & (1 << bit)]
