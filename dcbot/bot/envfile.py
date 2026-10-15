"""The bot's settings file (KEY=VALUE lines, mode 600), read. Standard library only, and nothing is read when this
is imported: bot/config.py reads all of it for the bot; tools/dcremote, on a machine that may have no bot at all,
asks for one name (stage 3b step 1.8)."""
import os

BOT_SERVER = "dcbot"  # the name of a bot's own tmux server where the file does not say (TMUX_SOCKET)


def path():
    """Where the file is: DCBOT_ENV, or the usual place when that is not set. Set to nothing it names no file (no
    settings), as it always did: it does not fall back to the usual place."""
    return os.path.expanduser(os.environ.get("DCBOT_ENV", "~/.config/dcbot/env"))


def load(path_=None):
    env = {}
    try:
        with open(path() if path_ is None else path_) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def bot_server():
    """The name of the tmux server of the bot installed on this machine: what its settings say, or the usual one."""
    return load().get("TMUX_SOCKET", BOT_SERVER)
