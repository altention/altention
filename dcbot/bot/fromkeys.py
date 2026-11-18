"""The ledger's senders as P1d keeps them (bot/senders.py), from the words written before, once (C-172a P1d, manager
11-17): on the first round of the bot's that knows its server, with the text channels' names as they are then.
Turned: "頻道 <id> 的 Claude（自稱）", "#name" (one text channel of that name now; two or none: left as it was),
"<machine> 上的 Claude／程式（folder）". Left: a tmux pane's number, anything else. When done: a mark next to the
ledger, one #audit line (how many turned, how many left). Stopped half way (a restart before the ledger was written):
again at the next start; a key is never turned twice."""
import os
import time
import traceback

from . import ledger, senders


def mark_path():
    return os.path.join(os.path.dirname(ledger.path()), "ledger-from-keys")


def done():
    return os.path.exists(mark_path())


def ids_in(guild):
    """`ids_of_name` for old_key(): the IDs of `guild`'s text channels of a name (None: no server known, none)."""
    return lambda name: [c.id for c in getattr(guild, "text_channels", None) or () if c.name == name]


def machine_of(host):
    """The machine list's name for a hostname, or None (altention.config.name_of; a broken list: None)."""
    try:
        from altention import config as altconfig
        return altconfig.name_of(host)
    except Exception:
        return None


def fresh(said, ids_of_name):
    """A sender as the ledger keeps it now: written in the words before P1d (a dcctl send noted by an older dcctl,
    taken in after the turn: manager 11-17), its key; else as it is."""
    return senders.old_key(said, ids_of_name, machine_of) or said


def turn(data, ids_of_name):
    """Turn the senders of `data` (ledger.read()'s shape) in place: (how many turned, how many left as they were).
    Entries with no sender (the bot's own) are neither."""
    turned = left = 0
    for rows in data["channels"].values():
        for _, e in rows:
            said = e.get("from")
            if said is None or senders.parse(said) is not None:
                continue
            k = senders.old_key(said, ids_of_name, machine_of)
            if k is None:
                left += 1
            else:
                e["from"] = k
                turned += 1
    return turned, left


async def once(bot, guild_id):
    """turn() over the ledger in memory, unless done before; the mark and the #audit line after it is saved."""
    if done():
        return
    try:
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        by_name = {}
        for ch in guild.text_channels:
            by_name.setdefault(ch.name, []).append(ch.id)
        (turned, left), written = ledger.rewrite(lambda data: turn(data, lambda name: by_name.get(name, ())))
        if not written:  # (no mark: all of it again at the next start; turning a key twice changes nothing)
            return
        os.makedirs(os.path.dirname(mark_path()), exist_ok=True)
        with open(mark_path(), "w") as f:
            f.write("%d\n" % time.time())
        await bot.audit("[ledger] 寄件人改成固定的鍵：轉了 %d 則，留原樣 %d 則（同名的頻道、找不到的名字、tmux 窗格號不轉；留原樣的不能再用 dcctl edit 改）"
                        % (turned, left))
    except Exception:
        traceback.print_exc()
