#!/usr/bin/env python3
"""The bot's settings moved out of its old settings file (C-110, manager 2026-10-30): the token and the two IDs into
the core's secrets (altention/secret_store.py: ~/.config/altention/secrets/core/<name>, one file each, 600), the
other keys into the instance's plugins.json under "dcbot", then the old file deleted, but only once no version
that reads the old file alone can come back (manager 2026-11-03): on the bot's machine the supervisor's last_good has
to be a version with this file in it (a rollback goes there); until then the old file stays beside the new places,
the same values in both. install.sh's dcbot.token step and the bot (in the background, once it is on Discord) run
it; a machine moves only its own file (nothing over ssh). `altention update undo` to a version without it writes
the old file back first (restore_old()).

  python3 altention/botmove.py        move now; one line says what happened (nothing when there is no old file)

The order, each step only once the one before it held; any failure stops there with the old file as it was, and
the bot goes on reading it (dcbot/bot/envfile.py reads the new places first, then the old file):
1. the old file read, every key in it known (one not in the table: nothing moves, its name is said);
2. on the bot's machine (the list says so, config.bot_machine), its other keys written into plugins.json under the
   instance's git lock and committed; a key plugins.json has already, with another value, is left as plugins.json
   has it and named. On another machine (a standby keeps a token, C-53) only the secrets move and the other keys
   are dropped, named. The list or plugins.json unreadable, a machine the list does not know by name (it may be
   the bot's under another name), or a secret the store has already with another value (the old file may be the
   newer one, edited by hand: manager 2026-11-03): nothing moves, the old file stays, the names are said (doctor
   says it too: pending());
3. each secret written into the store (a new file made 600, then moved over);
4. each read back and compared, by length and hash, never shown;
5. the old file deleted, its folder too when that is left empty, once proven() (else it stays, said: copied).
Running it again changes nothing. No value, length or hash of a secret is ever printed, logged or put in an error:
an error is said by its kind only. Exit status: 0 moved or nothing to move, 1 not moved (the line says why).
Standard library only, Python 3.8; HOME is read at call time.
"""
import hashlib
import json
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import config  # noqa: E402
from altention.text import t  # noqa: E402

OLD = config.BOT_OLD_SETTINGS
SECRETS = (("DISCORD_TOKEN", "discord-token"), ("OWNER_ID", "owner-id"), ("GUILD_ID", "guild-id"))
SETTINGS = (("CLAUDE_BIN", "claude_bin", str), ("CLAUDE_MODEL", "model", str), ("MAX_PARALLEL", "max_parallel", int),
            ("TURN_TIMEOUT", "turn_timeout", int), ("BRIDGE", "bridge", str), ("USER_TMUX", "user_tmux", str),
            ("TMUX_SOCKET", "tmux_socket", str), ("INTERIM", "interim", bool),
            ("AGENT_THREADS", "agent_threads", bool), ("IDLE_HOURS", "idle_hours", float),
            ("ARCHIVE_CATEGORY", "channels.archive", str), ("AUDIT_CHANNEL", "channels.audit", str),
            ("CHAT_CHANNEL", "channels.chat", str), ("GUIDE_CHANNEL", "channels.guide", str),
            ("SETUP_CHANNEL", "channels.setup", str), ("SYSTEM_CHANNEL", "channels.system", str))
# the same table as dcbot/bot/envfile.py's SECRETS and SETTINGS (ops/tests/test_botmove.py checks they agree)


LOCK_WAIT = 30  # seconds for the instance's git lock (the bot's start waits on it at most this long)


class Stop(Exception):
    """Nothing more moves; str(e) is the reason in plain words, never a value."""

    def __init__(self, why, todo="", uses_store=False):
        Exception.__init__(self, why)
        self.todo = todo
        self.uses_store = uses_store  # the bot reads the store's value now, not the old file's


def old_path():
    return os.path.expanduser(OLD)


def read_old(path):
    """{key: value} of the old file's KEY=VALUE lines, read as the bot reads them (envfile.parse). Only a regular
    file is opened (a FIFO there would hang whoever opened it)."""
    import stat
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise Stop(t("botmove.why_unreadable", kind="not a file"), t("botmove.todo_unreadable"))
    except OSError as e:
        raise Stop(t("botmove.why_unreadable", kind=type(e).__name__), t("botmove.todo_unreadable"))
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise Stop(t("botmove.why_unreadable", kind=type(e).__name__), t("botmove.todo_unreadable"))
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def typed(kind, text):
    """The old file's text as plugins.json keeps it: a number (finite) or true/false; None for a number that does
    not read as one (checks() stops the move on it, naming the key)."""
    import math
    if kind is bool:
        return text != "0"
    if kind in (int, float):
        try:
            n = kind(text)
        except ValueError:
            return None
        if not math.isfinite(n):  # nan, inf: no JSON number, no setting
            return None
        return int(n) if kind is float and n == int(n) else n
    return text


def same(a, b):
    """Is plugins.json's value `a` the setting the old file's text `b` gives (typed alike)?"""
    return a == b or (isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float))
                      and not isinstance(b, bool) and float(a) == float(b))


def write_settings(values):
    """values {dotted: value} into plugins.json's "dcbot", under the instance's git lock, committed. A key already
    there with another value is left as it is. Returns the dotted keys left. Stop when it cannot be done."""
    from altention import sync
    inst = config.instance_dir()
    if not inst:
        raise Stop(t("botmove.why_no_instance"), t("botmove.todo_install"))
    path = os.path.join(inst, "plugins.json")
    repo = sync.Repo(inst)
    kept = []
    try:
        with sync.locked(repo, wait=LOCK_WAIT):
            try:
                with open(path, encoding="utf-8") as f:
                    raw = f.read()
                d = json.loads(raw)
            except FileNotFoundError:
                raw, d = "", {}
            except (OSError, ValueError) as e:
                raise Stop(t("botmove.why_settings", kind=type(e).__name__), t("botmove.todo_settings"))
            if not isinstance(d, dict) or not isinstance(d.get("dcbot", {}), dict):
                raise Stop(t("botmove.why_settings", kind="shape"), t("botmove.todo_settings"))
            changed = False
            for key, value in sorted(values.items()):
                node = d.setdefault("dcbot", {})
                parts = key.split(".")
                for part in parts[:-1]:
                    nxt = node.setdefault(part, {})
                    if not isinstance(nxt, dict):
                        raise Stop(t("botmove.why_settings", kind="shape"), t("botmove.todo_settings"))
                    node = nxt
                if parts[-1] in node:
                    if not same(node[parts[-1]], value):  # a setting, not a secret: both values said
                        kept.append(t("botmove.kept_item", name=key, old=value, new=node[parts[-1]]))
                    continue
                node[parts[-1]] = value
                changed = True
            if not changed:
                return kept
            text = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
            json.loads(text)  # what is written reads back
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            repo.git("add", "--", "plugins.json", timeout=60)
            if repo.git("diff", "--cached", "--quiet", "--", "plugins.json", timeout=60).returncode != 0:
                who = [] if repo.git("config", "user.email", timeout=20).stdout.strip() else \
                    ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]  # as setup.py's commit()
                repo.git(*(who + ["commit", "-q", "-m", "dcbot: the bot's settings moved here from its old settings file",
                                  "--", "plugins.json"]), timeout=60)  # not committed: the sync commits it
    except sync.Busy:
        raise Stop(t("botmove.why_busy"), t("botmove.todo_again"))
    except OSError as e:
        raise Stop(t("botmove.why_settings", kind=type(e).__name__), t("botmove.todo_settings"))
    return kept


def digest(data):
    return len(data), hashlib.sha256(data).digest()


def write_secrets(values):
    """values {name: text} into the core's store (one there already with the same value: left as it is; with another
    value: Stop, though checks() has stopped the move before that).
    Each read back and compared by length and hash. Stop when one cannot be written."""
    from altention import secret_store
    for name, value in values:
        data = value.encode("utf-8")
        try:
            with open(secret_store.path("core", name), "rb") as f:
                there = f.read()
        except FileNotFoundError:
            there = None
        except OSError as e:
            raise Stop(t("botmove.why_store", kind=type(e).__name__), t("botmove.todo_again"))
        if there is not None:
            there = there[:-1] if there.endswith(b"\n") else there
            if digest(there) != digest(data):  # store_differs() stops the move before this: never reached then
                raise Stop(t("botmove.why_differs", keys=name), t("botmove.todo_differs"))
            continue
        try:
            secret_store.write("core", name, value)
        except (OSError, secret_store.Refused) as e:
            raise Stop(t("botmove.why_store", kind=type(e).__name__), t("botmove.todo_again"))
        try:
            with open(secret_store.path("core", name), "rb") as f:
                back = f.read()
        except OSError as e:
            raise Stop(t("botmove.why_store", kind=type(e).__name__), t("botmove.todo_again"))
        if digest(back) != digest(data):
            raise Stop(t("botmove.why_check"), t("botmove.todo_again"))


AUDIT_TIMEOUT = 60


def tell_audit(line):
    """The line in #audit (altention dc post), for a move made outside the bot (install.sh): the bot says its own.
    A failure is said on stderr, never raised: the move is done (or not) either way."""
    import subprocess
    channel = "@audit"  # the bot's own #audit, by the ID it keeps (C-201 6.2): its name may have been changed
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "altention")
    try:
        r = subprocess.run([sys.executable, tool, "dc", "post", str(channel), line], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=AUDIT_TIMEOUT)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        sys.stderr.write(t("botmove.audit_failed") + "\n")


def move(tell=False):
    """(code, line, done): 0, "" and True when there is no old file; 0, the line and True once it is moved; 0, the
    line and False when copied but the old file still waits (proven()); 1, why not and False. tell: the line also
    goes to #audit (install.sh; the bot posts its own)."""
    code, line, done = _move()
    if line and tell:
        tell_audit(line)
    return code, line, done


def store_differs(env):
    """The core secrets the store has already with a value other than the old file's (names only; read, never
    shown). The old file is then not deleted: it may be the newer one, edited by hand (manager 2026-11-03)."""
    from altention import secret_store
    out = []
    for key, name in SECRETS:
        value = env.get(key, "")
        if not value.strip():
            continue
        try:
            with open(secret_store.path("core", name), "rb") as f:
                there = f.read()
        except FileNotFoundError:
            continue
        except OSError as e:
            raise Stop(t("botmove.why_store", kind=type(e).__name__), t("botmove.todo_again"))
        there = there[:-1] if there.endswith(b"\n") else there
        if digest(there) != digest(value.encode("utf-8")):
            out.append(name)
    return out


def checks(path):
    """(env, here): the old file read and every check that needs no write passed; Stop with why not."""
    env = read_old(path)
    known = set(k for k, _ in SECRETS) | set(k for k, _w, _k in SETTINGS)
    unknown = sorted(k for k in env if k not in known)
    if unknown:
        raise Stop(t("botmove.why_unknown", keys="、".join(unknown)), t("botmove.todo_unknown"))
    try:
        listed = any("dcbot" in (m or {}).get("roles", []) for m in (config.inventory().get("machines") or {}).values())
        # A list that names the bot's machine, and does not know this one by name: never a guess either way (not
        # bot_machine()'s "local" entry taken for this one, for posts: #code-review 2026-11-03). Without such an
        # entry the bot is this machine's whose token is here, as config.bot_machine() says.
        if listed and not config.known_here():
            raise Stop(t("botmove.why_unnamed"), t("botmove.todo_unnamed"))
        here = config.bot_machine()[2]  # only whether plugins.json is written here
    except config.ConfigError:
        raise Stop(t("botmove.why_list"), t("botmove.todo_list"))
    bad = [key for key, _w, kind in SETTINGS if key in env and typed(kind, env[key]) is None]
    if bad:
        raise Stop(t("botmove.why_value", keys="、".join(bad)), t("botmove.todo_value"))
    differs = store_differs(env)
    if differs:
        raise Stop(t("botmove.why_differs", keys="、".join(differs)), t("botmove.todo_differs"), uses_store=True)
    return env, here


def state_path():
    return os.path.expanduser("~/.local/state/altention/botmove.json")


def remember(why=None, todo="", waiting=False):
    """The last move's reason for not moving ({"why", "todo", "waiting"}, plain words, no value), for doctor; none
    once moved. waiting: copied, the old file kept until proven() (nothing for a person to do)."""
    path = state_path()
    try:
        if why is None:
            if os.path.lexists(path):
                os.remove(path)
            return
        from altention import statefile  # whole, and stamped: a bot restarting while tests run is not taken for them
        statefile.write_json(path, {"why": why, "todo": todo, "waiting": waiting})
    except OSError:
        pass  # doctor then says only that the file waits


def pending():
    """For doctor: None when there is no old file, or when it only waits to be deleted (copied, nothing to do);
    else (problem, fix) in plain words: why the last move stopped, as it remembered it, or that none has run yet.
    Whether the old file is there only: never opened (C-109)."""
    if not os.path.lexists(old_path()):
        return None
    try:
        with open(state_path(), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("waiting") is True:
            return None
        if isinstance(d, dict) and isinstance(d.get("why"), str) and isinstance(d.get("todo"), str):
            return t("botmove.doctor", why=d["why"]), d["todo"]
    except (OSError, ValueError):
        pass
    return t("botmove.doctor_waiting"), t("botmove.todo_pending")


VERSION_FILE = "altention/botmove.py"  # a commit with this file reads the new places (dcbot/bot/envfile.py came with it)


def engine_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def reads_new(rev, engine=None):
    """Does the engine's commit `rev` read the new places (it has VERSION_FILE)? False when it cannot be told."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", engine or engine_dir(), "cat-file", "-e", "%s:%s" % (rev, VERSION_FILE)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def proven(here, engine=None):
    """May the old file go? On the bot's machine only when the supervisor's last_good (where a rollback goes) reads
    the new places; elsewhere there is no supervisor to roll back (undo writes it back: restore_old)."""
    if not here:
        return True
    try:
        with open(os.path.join(engine or engine_dir(), "dcbot", "state", "last_good")) as f:
            good = f.read().strip()
    except OSError:
        return False
    return bool(good) and reads_new(good, engine)


def restore_old():
    """Before `altention update undo` goes back to a version that reads the old file alone: the old file written
    back, when it is not there, from what the store and plugins.json hold now (a secret set after the move comes
    along), only the keys that version knows, 600 in a 700 folder, read back and compared. (ok, line): ok False
    with why when it could not be written (undo then does not go back); line "" when nothing was needed."""
    import stat as _stat
    path = old_path()
    if os.path.lexists(path):
        return True, ""
    from altention import secret_store
    lines = []
    for key, name in SECRETS:
        try:
            with open(secret_store.path("core", name), encoding="utf-8") as f:
                value = f.read()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError) as e:
            return False, t("botmove.restore_failed", kind=type(e).__name__)
        value = value[:-1] if value.endswith("\n") else value
        if value.strip():
            lines.append("%s=%s" % (key, value.strip()))
    if not lines:
        return True, ""  # no token here: nothing the old version could read anyway
    try:
        dc = config.setting("dcbot", None)
    except config.ConfigError:
        dc = None
    dc = dc if isinstance(dc, dict) else {}
    for key, where, _kind in SETTINGS:
        node = dc
        for part in where.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, bool):
            lines.append("%s=%s" % (key, "1" if node else "0"))
        elif isinstance(node, (int, float, str)) and str(node).strip() and "\n" not in str(node):
            lines.append("%s=%s" % (key, str(node).strip()))
    data = ("\n".join(lines) + "\n").encode("utf-8")
    folder = os.path.dirname(path)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        with open(path, "rb") as f:
            back = f.read()
        if digest(back) != digest(data) or _stat.S_IMODE(os.stat(path).st_mode) != 0o600:
            os.remove(path)
            return False, t("botmove.restore_failed", kind=t("botmove.why_check"))
    except OSError as e:
        for p in (tmp,):
            try:
                os.remove(p)
            except OSError:
                pass
        return False, t("botmove.restore_failed", kind=type(e).__name__)
    return True, t("botmove.restored")


def _move():
    """(code, line, done): done once there is no old file left (moved, or none to move)."""
    path = old_path()
    if not os.path.lexists(path):
        return 0, "", True
    try:
        env, here = checks(path)
        settings = {where: typed(kind, env[key]) for key, where, kind in SETTINGS if key in env}
        dropped, kept = [], []
        if here:
            kept += ["dcbot." + k for k in write_settings(settings)]
        else:
            dropped = [key for key, _w, _k in SETTINGS if key in env]
        write_secrets([(name, env[key]) for key, name in SECRETS if env.get(key, "").strip()])
        if not proven(here):  # a rollback could still bring back a version that reads the old file alone
            remember(t("botmove.why_waiting"), t("botmove.todo_waiting"), waiting=True)
            line = t("botmove.copied")
            if kept:
                line += t("botmove.kept_keys", items="、".join(kept))
            return 0, line, False
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise Stop(t("botmove.why_delete", kind=type(e).__name__), t("botmove.todo_delete"))
        try:
            os.rmdir(os.path.dirname(path))  # only when empty
        except OSError:
            pass
    except Stop as e:
        remember(str(e), e.todo)
        return 1, t("botmove.kept_store" if e.uses_store else "botmove.kept", why=str(e), todo=e.todo).strip(), False
    except Exception as e:  # its kind only: an error's text may hold a line of the file
        why = t("botmove.why_error", kind=type(e).__name__)
        remember(why, t("botmove.todo_again"))
        return 1, t("botmove.kept", why=why, todo=t("botmove.todo_again")).strip(), False
    remember()
    line = t("botmove.moved") if here else t("botmove.moved_elsewhere", keys="、".join(dropped) or t("botmove.none"))
    if kept:
        line += t("botmove.kept_keys", items="、".join(kept))
    return 0, line, True


def main(argv):
    if argv:
        print(__doc__.split("\n\n")[1])
        return 2
    code, line, _done = move(tell=True)
    if line:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
