"""Configuration: secrets live outside the repo in ~/.config/dcbot/env (KEY=VALUE lines, mode 600)."""
import os

from . import envfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = envfile.path()
STATE = os.path.join(ROOT, "state")  # gitignored runtime state


def load_env(path=ENV_FILE):
    return envfile.load(path)


_env = load_env()
TOKEN = _env.get("DISCORD_TOKEN", "")
OWNER_ID = int(_env.get("OWNER_ID", "0") or 0)
GUILD_ID = int(_env.get("GUILD_ID", "0") or 0)
CLAUDE = _env.get("CLAUDE_BIN") or os.path.expanduser("~/.local/bin/claude")
MODEL = _env.get("CLAUDE_MODEL", "opus")
MAX_PARALLEL = int(_env.get("MAX_PARALLEL", "3"))
TURN_TIMEOUT = int(_env.get("TURN_TIMEOUT", "1800"))  # seconds per Claude turn


def channel_name(key, default):
    """The name of one of the bot's own channels: the instance's settings (plugins.json "dcbot.channels.<key>", e.g.
    {"dcbot": {"channels": {"guide": "manual"}}}), else the env file's <KEY>_CHANNEL, else `default`. Unreadable
    settings: the default, nothing raised (this runs on import, in the tools too)."""
    try:
        from altention import config as alt
        said = alt.setting("dcbot.channels." + key)
    except Exception:
        said = None
    if isinstance(said, str) and said.strip().lstrip("#"):
        return said.strip().lstrip("#")
    return _env.get(key.upper() + "_CHANNEL") or default


AUDIT_CHANNEL = channel_name("audit", "audit")
CHAT_CHANNEL = channel_name("chat", "chat")  # made once, on a new installation's first start: a conversation in the instance folder
ARCHIVE_CATEGORY = _env.get("ARCHIVE_CATEGORY", "封存")  # where a detached session's channel goes
NOT_PROJECTS = ("機器人", "文字頻道", "語音頻道", ARCHIVE_CATEGORY)  # categories that do not stand for a project folder
GUIDE_CHANNEL = channel_name("guide", "guide")  # the manual (GUIDE.md) lives here; the bot never answers in it
BRIDGE = _env.get("BRIDGE", "tmux")  # default mode for a channel: "tmux" (interactive session) or "print" (claude -p)
USER_TMUX = _env.get("USER_TMUX", "")  # the owner's own tmux socket; empty: the default one (plain `tmux`)
TMUX_SOCKET = _env.get("TMUX_SOCKET", envfile.BOT_SERVER)  # the bot's own tmux server: tmux -L dcbot attach -t <session>
INTERIM = _env.get("INTERIM", "1") != "0"  # tmux mode: also post what Claude says between tool calls
AGENT_THREADS = _env.get("AGENT_THREADS", "1") != "0"  # subagents and workflows get a thread each
IDLE_HOURS = float(_env.get("IDLE_HOURS", "12"))  # close an idle tmux session after this long (it resumes on demand)
