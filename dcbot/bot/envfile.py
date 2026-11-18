"""The bot's settings, read (C-110): its token and the two IDs from the core's secrets (one file each,
~/.config/altention/secrets/core/<name>, mode 600), the rest from the instance's plugins.json under "dcbot", and
what is still in the old settings file (~/.config/dcbot/env, KEY=VALUE lines, mode 600) for a key the new places do
not have. Given as the old file's KEY=VALUE names, so bot/config.py reads one dict. Standard library only at import,
and nothing is read when this is imported: bot/config.py reads all of it for the bot; tools/dcremote, on a machine
that may have no bot at all, asks for one name (stage 3b step 1.8). altention.config is imported when the settings
are read (a machine without an instance gets none of them, never an error).

DCBOT_ENV names a settings file to read instead (tests, rigs): then that file alone is read, as it always was, and
neither the secrets nor plugins.json (a test's settings never mixed with this machine's real ones). Set to nothing
it names no file at all."""
import os

BOT_SERVER = "dcbot"  # the name of a bot's own tmux server where the settings do not say (TMUX_SOCKET)
OLD = "~/.config/dcbot/env"  # COMPAT: ~/.config/dcbot/env 裡還有機器人設定的機器；0.x 期間都還讀，1.0 之前拿掉
STORE = "~/.config/altention/secrets/core"  # altention/secret_store.py's folder of the core's secrets
SECRETS = (("DISCORD_TOKEN", "discord-token"), ("OWNER_ID", "owner-id"), ("GUILD_ID", "guild-id"))
SETTINGS = (("CLAUDE_BIN", "claude_bin"), ("CLAUDE_MODEL", "model"), ("MAX_PARALLEL", "max_parallel"),
            ("TURN_TIMEOUT", "turn_timeout"), ("BRIDGE", "bridge"), ("USER_TMUX", "user_tmux"),
            ("TMUX_SOCKET", "tmux_socket"), ("INTERIM", "interim"), ("AGENT_THREADS", "agent_threads"),
            ("IDLE_HOURS", "idle_hours"), ("ARCHIVE_CATEGORY", "channels.archive"),
            ("AUDIT_CHANNEL", "channels.audit"), ("CHAT_CHANNEL", "channels.chat"), ("GUIDE_CHANNEL", "channels.guide"),
            ("SETUP_CHANNEL", "channels.setup"), ("SYSTEM_CHANNEL", "channels.system"))  # plugins.json dcbot.<…>


def path():
    """The old settings file: DCBOT_ENV, or the usual place when that is not set. Set to nothing it names no file (no
    settings), as it always did: it does not fall back to the usual place."""
    return os.path.expanduser(os.environ.get("DCBOT_ENV", OLD))


def secret_path(name):
    """Where the core's secret `name` (discord-token, owner-id, guild-id) is, from HOME now."""
    return os.path.join(os.path.expanduser(STORE), name)


def parse(path_):
    """The KEY=VALUE lines of the file at path_ ({} when there is none or it cannot be read)."""
    env = {}
    try:
        with open(path_) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def secret(name):
    """The core's secret `name` as stored (without its last newline), None when there is none or it cannot be read."""
    try:
        with open(secret_path(name), encoding="utf-8") as f:
            value = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    value = value[:-1] if value.endswith("\n") else value
    return value.strip() or None


def as_text(value):
    """A plugins.json value as the old file would have written it: true/false as 1/0; None for anything else."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def dotted(d, key):
    for part in key.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


def instance_settings():
    """The instance's plugins.json "dcbot" object, {} when there is none or it cannot be read."""
    try:
        from altention import config as alt
        got = alt.setting("dcbot")
    except Exception:
        return {}
    return got if isinstance(got, dict) else {}


def load(path_=None, secrets=True):
    """The bot's settings by their old names. path_ (or DCBOT_ENV set): that file alone. Else the new places first,
    then the old file for what they do not have; secrets False: not the core's secrets (a reader that wants one
    setting never has the token read into it)."""
    if path_ is not None:
        return parse(path_)
    if "DCBOT_ENV" in os.environ:
        return parse(path())
    env = parse(path())
    dc = instance_settings()
    for key, where in SETTINGS:
        value = as_text(dotted(dc, where))
        if value is not None:
            env[key] = value
    for key, name in SECRETS if secrets else ():
        value = secret(name)
        if value is not None:
            env[key] = value
    return env


def bot_server():
    """The name of the tmux server of the bot installed on this machine: what its settings say, or the usual one."""
    return load(secrets=False).get("TMUX_SOCKET", BOT_SERVER)
