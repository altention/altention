"""Claude Code's health on the bot's machine (C-162, design: instance docs/refactor/c162-design.md).

Three layers, each the same Claude Code conversation (one session id), the bot going down them only when Claude
Code itself is the trouble:
  1  tmux: the interactive Claude Code in a pane (the default; buttons, menus, /btw);
  2  print: `claude -p --output-format stream-json` (claude_bridge.run_turn; tool progress, no buttons);
  3  text: `claude -p --output-format text` (claude_bridge.run_text; the reply only).
The layer the machine's channels are held to is `level` (1 when nothing is written). A channel's own `mode` is
never changed: main.Bot.mode() takes the lower of the two, so going back up is one value.

Only channels whose Claude runs on this machine and that the bot opened go down: not another machine's (print does
not run there yet: second step, design section 8), not a pane the owner attached here (it is the owner's own
Claude Code, which a print turn would run beside, on the same conversation).

Which Claude Code file the bot runs is binary(): a pinned one (section 3, a later step) or config.CLAUDE. Every
place the bot runs claude asks it (claude_bridge, catalog, tmux_bridge.argv for this machine).

The file (state/cc_health.json) is keyed by machine, then by Claude Code version, so the step for other machines
uses the same shape:
  {"machines": {"<machine>": {"broken": {"tmux"|"json": {"version": "<the version that broke it>", "since": <time>}},
                              "versions": {"<version>": {counts, probe}}, "pin": {...}}}}
A file that cannot be read is no level (1) and no pin: the bot runs as it always did.
"""
import json
import re
import shutil
import os
import time

from . import config

MODE_OF = {1: "tmux", 2: "print", 3: "text"}


def path():
    return os.environ.get("DCBOT_CC_HEALTH") or os.path.join(config.STATE, "cc_health.json")  # (tests: tmuxguard)


def machine():
    """The name this machine's entry is kept under (the machine list's; the host name when there is none)."""
    try:
        from . import remote
        return remote.this_machine() or "here"
    except Exception:
        return "here"


def load():
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data):
    tmp = "%s.tmp.%d" % (path(), os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path())


def entry(data=None, name=None):
    """This machine's entry (a dict; empty when there is none or it is not one)."""
    data = load() if data is None else data
    got = ((data.get("machines") or {}) if isinstance(data.get("machines"), dict) else {}).get(name or machine())
    return got if isinstance(got, dict) else {}


KINDS = ("tmux", "json")  # the ways a channel's turns run that can break apart (manager 11-07: each kept on its own)


def broken(data=None):
    """Which ways of running Claude Code are broken on this machine: {"tmux": {"version", "since"}, "json": …}. A
    tmux channel looks at "tmux" only; a print channel (its own choice, or a tmux one stepped down) at "json" only:
    one update may break one way and not the other (the stream-json format changed, the screen did not)."""
    got = entry(data).get("broken")
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if k in KINDS and isinstance(v, dict)}


def broken_version(kind=None, data=None):
    """The Claude Code version that broke `kind` (any, when None)."""
    got = broken(data)
    for k in ([kind] if kind else KINDS):
        if k in got:
            return got[k].get("version")
    return None


def set_broken(kind, version, now=None):
    data = load()
    mine = _mine(data)
    if not isinstance(mine.get("broken"), dict):
        mine["broken"] = {}
    mine["broken"][kind] = {"version": version, "since": now or time.time()}
    save(data)


def clear_broken(kind=None):
    data = load()
    mine = _mine(data)
    if isinstance(mine.get("broken"), dict):
        for k in ([kind] if kind else list(mine["broken"])):
            mine["broken"].pop(k, None)
        if not mine["broken"]:
            mine.pop("broken")
    save(data)


def level(data=None):
    """How a tmux channel here runs, as a layer: 1 (tmux), 2 (print: tmux broken), 3 (text: stream-json broken too)."""
    got = broken(data)
    return 1 if "tmux" not in got else 3 if "json" in got else 2


def set_level(value, version=None, now=None):
    """level() written as broken ways (1: none; 2: tmux; 3: tmux and json)."""
    clear_broken()
    if value >= 2:
        set_broken("tmux", version, now)
    if value >= 3:
        set_broken("json", version, now)


def applies(st):
    """Does the machine's level hold this channel? Only a conversation on this machine that the bot opened: its own
    tmux session, or a window it opened in the owner's tmux here (home without a host). Not another machine's, not
    an attached pane (the owner's own), not a terminal or files channel."""
    if st.get("kind") in ("terminal", "files") or st.get("plain"):
        return False
    home = st.get("home")
    if home:
        return not home.get("host")
    return not st.get("pane")


def effective(own, st, data=None):
    """The way a channel runs: its own (`own`: "tmux" or "print"), or the next one still sound: tmux broken → print,
    stream-json broken → text. A channel the machine's state does not hold (applies()) keeps its own."""
    if not applies(st):
        return own
    got = broken(data)
    way = own
    if way == "tmux" and "tmux" in got:
        way = "print"
    if way == "print" and "json" in got:
        way = "text"
    return way


def binary(data=None):
    """The Claude Code file the bot runs on this machine: the pinned one when there is one that is still there (a
    later step writes it), else config.CLAUDE (CLAUDE_BIN, or ~/.local/bin/claude)."""
    pin = entry(data).get("pin")
    if isinstance(pin, dict) and isinstance(pin.get("path"), str) and os.path.isfile(pin["path"]):
        return pin["path"]
    return config.CLAUDE


# ------------------------------------------------------------------ what counts as broken (design section 2.1)
SUSPECT_AFTER = 3        # failures of one version (a channel's own run of them reset by its success)
RESUME_CHANNELS = 2      # channels whose saved conversation would not come up, on one version
START_AFTER = 2         # print turns in a row whose claude exited non-zero (not killed, not the login or limit)
JSON_AFTER = 3           # print turns in a row with no result event though claude exited 0
GRACE_SECS = 10 * 60     # after the bot starts: many conversations reopen at once; failures only noted
MEM_FLOOR = 2 * 1024 ** 3
VERSION = re.compile(r"^\d+\.\d+\.\d+([.-][\w.]+)?$")

# Claude Code's own words for a trouble that is not its version: the usage limit (C-99), the login (C-100), the
# network or the API. Looked for only where an error is written (TAIL_ROWS: the last rows of a screen that ended, of a
# screen with no box, of the probe's; stderr), never in the conversation above, and as Claude Code words them, numbers
# with their context: a "529 tokens" or a version 2.1.401 on screen, or /login in what was said, is none of this
# (#code-review-2 on e30eba7: matched loosely, the bot would never step down).
NOT_THE_VERSION = re.compile(
    r"usage limit reached|limit resets|rate limit|(?:please )?run /login|invalid api key|oauth token (?:has )?expired|"
    r"authentication(?:_error| failed| error)|not logged in|"
    r"\b(?:ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|ETIMEDOUT)\b|getaddrinfo|connection error|unable to connect to api|"
    r"overloaded_error|API Error:\s*\(?(?:401|403|429|5\d\d)\b|\bstatus(?: code)?:? (?:401|403|429|5\d\d)\b",
    re.I)
TAIL_ROWS = 6


def tail(text, rows=TAIL_ROWS):
    """The last `rows` rows of `text` that hold anything."""
    return "\n".join([r for r in (text or "").split("\n") if r.strip()][-rows:])


def version_of(path):
    """The Claude Code version a file is, by its name once links are followed (versions/<version>, or a kept copy
    claude-keep/<version>); None when the name is no version."""
    try:
        name = os.path.basename(os.path.realpath(path))
    except (OSError, TypeError, ValueError):
        return None
    return name if VERSION.match(name) else None


def busy_machine():
    """Why the machine is too busy for a failure to say anything of Claude Code (MemAvailable under 2 GB, or the
    1-minute load over twice the CPUs); None when it is not, or it cannot be told."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    if kb * 1024 < MEM_FLOOR:
                        return "可用記憶體 %.1f GB" % (kb / 1024.0 ** 2)
        load = os.getloadavg()[0]
        if load > 2 * (os.cpu_count() or 1):
            return "load %.1f" % load
    except (OSError, ValueError, IndexError):
        return None
    return None


def not_the_version(text="", signal=""):
    """Why a failure is none of the version's: Claude Code's words above in the last rows of its screen or stderr
    (tail()), a SIGKILL (the kernel's OOM killer, or someone), or a machine too busy. None when nothing says so."""
    if str(signal) == "9":
        return "被訊號 9 結束（記憶體不足？）"
    found = NOT_THE_VERSION.search(tail(text))
    if found:
        return "畫面寫著「%s」" % found.group(0)
    return busy_machine()


def _versions(mine):
    got = mine.get("versions")
    if not isinstance(got, dict):
        got = mine["versions"] = {}
    return got


def _mine(data):
    machines = data.get("machines")
    if not isinstance(machines, dict):
        machines = data["machines"] = {}
    got = machines.get(machine())
    if not isinstance(got, dict):
        got = machines[machine()] = {}
    return got


def _record(version):
    data = load()
    rec = _versions(_mine(data)).setdefault(version, {})
    return data, rec


def note_ok(version, channel, kind="tmux"):
    """A conversation of `version` came up (tmux) or a print turn gave its result (json): that channel's run of
    failures ends, and the version has worked once."""
    if not version:
        return
    data, rec = _record(version)
    changed = not rec.get("worked")
    rec["worked"] = True
    for k in (kind, "start") if kind == "json" else (kind,):  # a print turn that worked ends its channel's run of
        runs = rec.get(k)                                       # non-zero exits too (#code-review on 0c0b58d)
        if isinstance(runs, dict) and runs.pop(str(channel), None):
            changed = True
    if changed:
        save(data)


def note_fail(kind, version, channel, why="", now=None):
    """A failure of `version` in `channel`, of `kind`: "tmux" (a window did not come up, its claude ended at once,
    or its prompt box is not one the bot knows), "resume" (the saved conversation did not come up, a new one did),
    "json" (a print turn with no result event). What it leads to: "suspect" (time for the probe), "json" (layer 2
    itself is broken), or None (counted, nothing yet). Failures that are none of the version's are not given here
    (not_the_version()); during GRACE_SECS after the bot started the caller notes them as "held"."""
    if not version:
        return None
    data, rec = _record(version)
    runs = rec.get(kind)
    if not isinstance(runs, dict):
        runs = rec[kind] = {}
    runs[str(channel)] = (runs.get(str(channel)) or 0) + 1
    rec["last"] = {"kind": kind, "channel": str(channel), "why": str(why)[:300], "at": now or time.time()}
    save(data)
    if kind == "json":
        return "json" if sum(runs.values()) >= JSON_AFTER else None
    if kind == "start":  # claude -p exits non-zero (section 3.2): twice
        return "start" if sum(runs.values()) >= START_AFTER else None
    if kind == "resume":
        return "suspect" if len(runs) >= RESUME_CHANNELS else None
    if not rec.get("worked"):  # a version that has not worked once yet: the first failure is enough (section 2.1)
        return "suspect"
    return "suspect" if sum(runs.values()) >= SUSPECT_AFTER else None


def forget_failures(version):
    """The probe came up clean: what was counted for `version` says nothing of it."""
    data, rec = _record(version)
    for kind in ("tmux", "resume", "json", "start"):
        rec.pop(kind, None)
    save(data)


def json_alone(data=None):
    """Only stream-json broken (the screen is fine): words that name the screen would say the wrong thing."""
    got = broken(data)
    return "json" in got and "tmux" not in got


def probed_lately(version, secs=30 * 60, now=None, key="probed"):
    rec = _versions(entry()).get(version) if isinstance(entry().get("versions"), dict) else None
    at = (rec or {}).get(key)
    return isinstance(at, (int, float)) and (now or time.time()) - at < secs


def mark_probed(version, result, now=None, key="probed"):
    data, rec = _record(version)
    rec[key] = now or time.time()
    rec[key.replace("probed", "probe")] = result
    save(data)


def note_probe_session(name):
    """The probe's tmux session while it runs (None once it is closed): a bot that starts and finds one written
    closes that one (only the one written down)."""
    data = load()
    mine = _mine(data)
    if name:
        mine["probe_session"] = name
    else:
        mine.pop("probe_session", None)
    save(data)


# ------------------------------------------------------------------ the last good version and the pin (section 3)
KEEP_FLOOR = 1024 ** 3  # free disk under this: no copy kept (#audit says so)


def keep_dir():
    return os.path.join(config.STATE, "claude-keep")


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def keep_copy(version, source):
    """The file part of keep(), for a thread: `source` copied to claude-keep/<version> (temp file, sha256 of the
    source, the copy and the source again all alike, then renamed). (path, size, sha256) of the source, or why not
    (a str: the disk, a copy that differs, an error). Writes nothing of cc_health.json."""
    try:
        free = shutil.disk_usage(config.STATE).free
    except OSError as e:
        return "讀不到磁碟空間（%s）" % e
    if free < KEEP_FLOOR:
        return "磁碟剩不到 1 GB"
    os.makedirs(keep_dir(), exist_ok=True)
    final = os.path.join(keep_dir(), version)
    tmp = "%s.tmp.%d" % (final, os.getpid())
    try:
        real = os.path.realpath(source)
        size, digest = os.path.getsize(real), sha256(real)
        shutil.copyfile(real, tmp)
        os.chmod(tmp, 0o755)
        if os.path.getsize(tmp) != size or sha256(tmp) != digest or sha256(real) != digest:
            os.remove(tmp)
            return "複製出來的跟原本的不一樣"
        os.replace(tmp, final)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return "複製失敗（%s）" % e
    return final, size, digest


def keep_record(version, final, size, digest):
    """The state part of keep(), on the event loop (the loop writes cc_health.json too: #code-review-2 on 7780fb3):
    `version` becomes cc_last_good, and the copy it replaces goes (not one a pin runs)."""
    data = load()
    mine = _mine(data)
    old = (mine.get("last_good") or {}).get("path") if isinstance(mine.get("last_good"), dict) else None
    mine["last_good"] = {"version": version, "path": final, "size": size, "sha256": digest, "since": time.time()}
    save(data)
    pin = mine.get("pin") if isinstance(mine.get("pin"), dict) else {}
    if old and old != final and os.path.dirname(old) == keep_dir() and pin.get("path") != old:
        try:
            os.remove(old)
        except OSError:
            pass


def keep(version, source):
    """`version` passed the check by hand (!cctest): its file copied to claude-keep/<version> (one copy only: the old
    one removed once the new one is verified), and it becomes cc_last_good (keep_copy() then keep_record()). The size
    and sha256 kept are the source's, both sides compared, so a source removed by Claude Code's cleanup halfway is
    caught (#code-review-2). Returns None when kept, else why not."""
    got = keep_copy(version, source)
    if isinstance(got, str):
        return got
    keep_record(version, *got)
    return None


def last_good(data=None, check=False):
    """cc_last_good when its kept file is there and its size is what was kept ({"version", "path", …}); None
    otherwise. `check`: its sha256 compared too (daily, and before it is pinned)."""
    got = entry(data).get("last_good")
    if not isinstance(got, dict) or not isinstance(got.get("path"), str):
        return None
    try:
        if os.path.getsize(got["path"]) != got.get("size"):
            return None
        if check and sha256(got["path"]) != got.get("sha256"):
            return None
    except OSError:
        return None
    return got


def pin(data=None):
    got = entry(data).get("pin")
    return got if isinstance(got, dict) and isinstance(got.get("path"), str) else None


def set_pin(good, broke):
    """Run `good` (last_good()) instead of Claude Code `broke` (the version that could not be used)."""
    data = load()
    _mine(data)["pin"] = {"version": good["version"], "path": good["path"], "from": broke, "since": time.time()}
    save(data)


def clear_pin():
    data = load()
    _mine(data).pop("pin", None)
    save(data)


def pinned_env():
    """What a Claude Code started by the bot gets while a pin holds: no self-update (an old version would install
    the new one and move the link; section 3.1)."""
    return {"DISABLE_AUTOUPDATER": "1"} if pin() else {}


def pin_days():
    """dcbot.cc_pin_days (7): how long a pin holds before the owner is asked."""
    try:
        from . import remote
        got = remote.alt().setting("dcbot.cc_pin_days")
    except Exception:
        got = None
    return got if isinstance(got, int) and not isinstance(got, bool) and got > 0 else 7


def pin_reminded(at, since=None):
    data = load()
    got = _mine(data).get("pin")
    if isinstance(got, dict):
        if at is None:
            got.pop("reminded", None)
            got.pop("card", None)
        else:
            got["reminded"] = at
        if since is not None:
            got["since"] = since
        save(data)


def pin_card(channel_id, message_id):
    """Where the reminder with the two buttons was posted (C-187: another channel's Claude may answer it, and the
    buttons are taken off when it does)."""
    if not (isinstance(channel_id, int) and isinstance(message_id, int)):
        return
    data = load()
    got = _mine(data).get("pin")
    if isinstance(got, dict):
        got["card"] = [channel_id, message_id]
        save(data)


def pin_asking(data=None):
    """The pin, while its reminder waits on an answer (reminded, the card known); else None."""
    got = pin(data)
    card = got.get("card") if got else None
    if not got or not got.get("reminded") or not (isinstance(card, list) and len(card) == 2
                                                    and all(isinstance(i, int) for i in card)):
        return None
    return got


def installed():
    """The Claude Code installed for the bot (config.CLAUDE: CLAUDE_BIN or ~/.local/bin/claude), pinned or not: what
    !cctest checks and what a pin waits to see change."""
    return config.CLAUDE


def note_drilled(version, ok, tries):
    """The last new version drilled (section 5.2): looked at again only when it failed once."""
    data = load()
    _mine(data)["drilled"] = {"version": version, "ok": ok, "tries": tries, "at": time.time()}
    save(data)
