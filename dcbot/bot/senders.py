"""Who sent a message a tool posted, as the ledger keeps it (C-172a P1d, C-186; manager 11-17): a fixed key, put
into words only when shown, so a channel renamed is still the same sender and a tmux pane's number (which changes)
is never one.

  channel:<channel id>            the Claude of that channel (DCBOT_CHANNEL, or a pane the bot knows as that channel's)
  claude:<machine>:<folder>       a Claude Code with no channel of its own, on that machine, in that folder
  program:<machine>:<folder>      no Claude Code above it: cron, a script, a terminal

Who it is was looked up where the command ran (altention.dc.sender.claude_env(): the nearest Claude Code above the
command, by that Claude's own environment), on the bot's machine or, handed over by dcctl from another (hand_over),
on that one, its answer carried in ALTENTION_SENDER beside ALTENTION_HANDED. Same system account (messaging review
3.3, C-186 P0-2): a program set on it can write both itself and pass for another; this guards against mistakes and
casual imitation, not forgery.

The ledger before P1d wrote words ("頻道 <id> 的 Claude（自稱）", "#name", "<machine> 上的程式（folder）"): old_key()
turns them into keys once (bot/fromkeys.py). Standard library only: tools/dcctl runs this with the system Python."""
import re

KINDS = ("channel", "claude", "program")


def key(channel=None, machine=None, folder=None, claude=False):
    """The key of a sender: its channel when it has one, else a Claude Code (`claude`) or a program on `machine`
    in `folder` (its last part)."""
    if channel is not None and str(channel).isdigit():
        return "channel:%s" % channel
    folder = str(folder or "").rstrip("/").rsplit("/", 1)[-1] or "?"
    return "%s:%s:%s" % ("claude" if claude else "program", machine or "?", folder)


def parse(k):
    """(kind, channel id or machine, folder or None) of a key; None for anything else (an old entry's words)."""
    if not isinstance(k, str):
        return None
    kind, _, rest = k.partition(":")
    if kind == "channel" and rest.isdigit():
        return kind, rest, None
    if kind in ("claude", "program"):
        machine, sep, folder = rest.partition(":")
        if sep and machine and folder:
            return kind, machine, folder
    return None


def label(k, name_of=None):
    """A key in words: "#name" (by `name_of(channel id)`, when it knows) or "<#id>" for a channel's Claude,
    "<machine> 上的 Claude（folder）", "<machine> 上的程式（folder）"; anything else as it is."""
    got = parse(k)
    if got is None:
        return k
    kind, a, b = got
    if kind == "channel":
        name = name_of(int(a)) if name_of else None
        return "#%s" % name if name else "<#%s>" % a
    return "%s 上的%s（%s）" % (a, " Claude" if kind == "claude" else "程式", b)


def who(k, name_of=None):
    """label() as the one who sent it: a channel's is "<#id> 的 Claude" (or "#name 的 Claude")."""
    got = parse(k)
    return label(k, name_of) + (" 的 Claude" if got and got[0] == "channel" else "") if got else label(k, name_of)


OLD_CHANNEL = re.compile(r"^頻道 (\d+) 的 Claude（自稱）$")
OLD_NAMED = re.compile(r"^#(\S+)$")
OLD_PLACE = re.compile(r"^(.+?) 上的( Claude|程式)（(.*)）$")


def old_key(said, ids_of_name, machine_of=None):
    """The key for what the ledger wrote of a sender before P1d, or None to leave it as it is: "頻道 <id> 的
    Claude（自稱）", "#name" (`ids_of_name(name)`: the IDs of the text channels named so now; turned only when
    there is one: two of one name are not guessed between, a name none has now (renamed since) is left), "<machine> 上的 Claude／程式（folder）".
    Not dcctl's "<machine> 上的程式（自稱）" (no folder), nor "<machine> 的 tmux 窗格 %N（自稱）": a pane's number is
    no one's for long (manager 11-17). A machine written as its hostname (altention dc post wrote that) by the
    list's name when `machine_of(hostname)` knows it (manager 11-17), else as it was."""
    if not isinstance(said, str) or parse(said) is not None:
        return None
    m = OLD_CHANNEL.match(said)
    if m:
        return key(channel=m.group(1))
    m = OLD_NAMED.match(said)
    if m:
        ids = list(ids_of_name(m.group(1)) or ())
        return key(channel=ids[0]) if len(ids) == 1 else None
    m = OLD_PLACE.match(said)
    if m and m.group(3) and m.group(3) != "自稱":  # ("<machine> 上的程式（自稱）": dcctl's words before P1d said no folder)
        return key(machine=(machine_of(m.group(1)) if machine_of else None) or m.group(1), folder=m.group(3),
                   claude=m.group(2) == " Claude")
    return None
