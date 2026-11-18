"""The standby's copy of the bot's state (C-163 step 3b, design: instance docs/refactor/c163-design.md 3.1, 3.2, 3.4).

The machine that is the bot pushes, by ssh, the state files a takeover needs to every standby
(<engine>/dcbot/state-standby/ there, written whole: a temporary folder renamed in place), every PUSH_SECS, and
within CHANGED_SECS of a change to channels.json. With them goes meta.json: when, from which machine, its engine
version (git HEAD) and its venv's requirements, so that the standby checks before it takes over that it would read
these files the same way (section 3.4). Each push is noted in state/standby-sync.json (per standby: when, done or
why not) for doctor.

The bundle is a gzip tar, base64 text on stdin (remote.run carries text); `dcremote state-in` unpacks it, taking
only plain files under their own names (nothing absolute, nothing with "..").
"""
import base64
import io
import json
import os
import subprocess
import tarfile
import time

SYNCED = ("channels.json", "seen.json", "calls-system.json", "waiting-on-owner.json",
          "ownerless-sessions.json", "archive-left-said.json", "botmove-said", "cc_health.json",
          "ledger.json", "permissions.json")  # (ledger.json: who posted each message of the bot's, and what kind: C-172a P1a;
# permissions.json: what it last saw of its own permissions, the #update card shown once: C-191)
# (not routes.json: each machine's own, read by its own hook; taken in, the primary's copy took the place of this
# machine's and an attached pane here went silent after a takeover: drill item 12 on the mac, 11-10. The routes of the
# old bot's panes are bound again where they are: Bot.rebind_far())
SYNCED_DIRS = ("sources",)  # (and the questions waiting for the owner: bundle()'s `extra`, from ask.bundled(); taken
# in by the bot through ask.take_from(), never by take_in(): every write of theirs goes through ask.save())
PUSH_SECS = 5 * 60  # a change to what matters to a takeover: pushed, but not more often than this (manager 11-08:
# channels.json is megabytes, and busy and the like change every turn)
FULL_SECS = 6 * 3600  # everything, changed or not: nothing missed for long
CHANGED_SECS = 30  # how often push_rounds() looks
MATTERS = ("session_id", "home", "pane", "tmux", "permission", "model", "cwd", "files", "mode", "kind", "took_from",
           "archived", "paused", "talk_held")  # the fields of a channel a takeover acts on (manager 11-08); not busy, turns, history…
# (C-184: "paused", so the machine that is the bot when its time comes resumes it, posts kept included)
PROTOCOL = 2  # the lease and takeover protocol: lease.py's messages, dcremote's fencing, this module's bundle. Raised by
# one when any of them changes so that another version would read it wrong; a standby takes over only on the same
# (3.4 as the manager ruled 11-08: being able to take over matters more than the same engine commit)
STALE_SECS = 3600  # older state is taken over all the same, and the takeover says from when (section 3.4)


def engine_version(root):
    """The engine's git HEAD (the dcbot folder's parent); "" when git does not say."""
    try:
        r = subprocess.run(["git", "-C", os.path.dirname(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                           timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def release_name(root):
    """The release (tag vX.Y.Z) the engine's HEAD is, or ""."""
    try:
        r = subprocess.run(["git", "-C", os.path.dirname(root), "describe", "--tags", "--exact-match", "--match", "v*", "HEAD"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def version_said(commit, release):
    """How an engine version is said to the owner: its release, else 7 characters of the commit."""
    return release or (commit or "")[:7]


def requirements(root):
    try:
        with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


SETTINGS = ("BRIDGE", "MODEL", "TMUX_SOCKET", "NOTIFY_CHANNEL", "AUDIT_CHANNEL")  # the bot's settings that are no
# secret (section 3.2): pushed with the state for doctor to compare (163d); never the token


def meta(root, machine, now=None):
    settings = settings_now()
    return {"at": now or time.time(), "from": machine, "engine": engine_version(root), "requirements": requirements(root),
            "settings": settings, "protocol": PROTOCOL, "release": release_name(root), "token_hash": token_hash(),
            "health_hash": token_hash("healthchecks-url")}


def token_hash(name="discord-token"):
    """The first 16 hex of the sha256 of this machine's core secret `name` (the bot token; healthchecks-url too:
    secret_store.fingerprint, the value never read out), None without one: doctor on the standby compares it with
    the primary's pushed one (design section 8; the healthchecks address: #code-review on 5e2a4e0, manager)."""
    try:
        from altention import secret_store
        got = secret_store.fingerprint("core", name)
    except Exception:
        return None
    return (got or {}).get("hash")


def settings_now():
    """The bot's settings that are no secret, here (SETTINGS)."""
    try:
        from . import envfile
        got = envfile.load(secrets=False)
        return {k: got[k] for k in SETTINGS if k in got}
    except Exception:
        return {}


def readiness(standby_dir, root, now=None):
    """Design section 8, what the pushed state says on a standby: {"pushed": its age in seconds or None (nothing
    pushed), "protocol", "requirements", "token_same" (this machine's bot token the same as the primary's: True,
    False, or None when either is not known), "settings": the names of the bot's settings that differ}."""
    try:
        with open(os.path.join(standby_dir, "meta.json")) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {"pushed": None}
    mine = token_hash()
    theirs = got.get("token_hash")
    health_here, health_there = token_hash("healthchecks-url"), got.get("health_hash")
    pushed_settings = got.get("settings") or {}
    here = settings_now()
    return {"pushed": max(0, int((now or time.time()) - (got.get("at") or 0))),
            "protocol": got.get("protocol") == PROTOCOL,
            "requirements": got.get("requirements", "") == requirements(root),
            "token_same": None if not (mine and theirs) else mine == theirs,
            "health_same": None if not (health_here or health_there) else health_here == health_there,  # (none on
            # either: the outside notice not used; one on one side only: it differs)
            "settings": sorted(k for k in set(pushed_settings) | set(here) if pushed_settings.get(k) != here.get(k))}


def bot_panes_socket(server):
    """The socket path of this machine's bot tmux server `server` (as tmux makes it: $TMUX_TMPDIR or /tmp)."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", "tmux-%d" % os.getuid(), server)


def bot_panes(server):
    """The panes of this machine's bot tmux server `server`: {"socket", "panes": [{"session", "id", "pid", "socket"}]}
    (dcremote bot-panes for the bot that takes over; the failback for the windows it leaves on this machine)."""
    try:
        r = subprocess.run(["tmux", "-L", server, "list-panes", "-a", "-F",
                            "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{socket_path}"], capture_output=True, text=True,
                           timeout=20)
        out = r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    rows = [l.split("\t") for l in out.splitlines() if l.count("\t") == 3]
    sock = bot_panes_socket(server)
    return {"socket": sock, "panes": [{"session": a, "id": b, "pid": int(c) if c.isdigit() else None, "socket": d}
                                      for a, b, c, d in rows]}


def unplaced(st):
    """A channel whose conversation or files are on the bot's own machine (no machine named): a conversation channel
    (with a conversation or not yet) or a files channel; not archived, not marked by a takeover."""
    from . import kinds
    if not isinstance(st, dict) or st.get("took_from") or st.get("archived"):
        return False
    kind = kinds.channel_kind(st)
    if kind == kinds.FILES:
        return isinstance(st.get("files"), dict) and not st["files"].get("host")
    talks = kind == kinds.CONVERSATION or (kind == kinds.UNSET and st.get("cwd") and st.get("kind") in (None, kinds.CONVERSATION))
    return bool(talks) and not (st.get("home") or {}).get("host") and not (st.get("pane") or {}).get("host")


def failback_channels(channels, me, primary, found):
    """Section 5, step 2: the channels as the primary is to have them when it is the bot again (a copy; `me` the
    standby, `found` bot_panes() here). Where each conversation is now decides:
    - the primary's own, taken over (took_from naming it): back to its own machine (the host marks gone; a session
      of its bot server is again its `tmux`; print mode again); those never reached: as they were;
    - this machine's own (took_from.local): by ssh again (the host back);
    - opened here during the takeover (no machine named): stay here, named (home.host = this machine: its windows
      and session files are on this disk): never copied, never marked stale (manager, #code-review must 2)."""
    import copy
    out = copy.deepcopy(channels or {})
    for cid, st in out.items():
        if not isinstance(st, dict):
            continue
        t = st.get("took_from")
        if isinstance(t, dict) and t.get("machine") == primary and not t.get("local"):
            st.pop("took_from")
            if t.get("pending"):
                continue
            for k in ("home", "pane", "files"):
                if t.get(k) and isinstance(st.get(k), dict):
                    st[k] = {f: v for f, v in st[k].items() if f != "host"}
            if "tmux" in t:
                st["tmux"] = t["tmux"] or (st.get("home") or {}).get("session")
                st.pop("home", None)
                st.pop("pane", None)
            if t.get("mode"):
                st["mode"] = t["mode"]
        elif isinstance(t, dict) and t.get("local"):
            st.pop("took_from")
            for k in t["local"]:
                if isinstance(st.get(k), dict):
                    st[k] = dict(st[k], host=me)
        elif unplaced(st):
            from . import kinds
            if kinds.channel_kind(st) == kinds.FILES:
                st["files"] = dict(st["files"], host=me)
                continue
            if st.get("home") or st.get("pane"):  # a window in the owner's tmux here, or a pane of theirs
                for k in ("home", "pane"):
                    if st.get(k):
                        st[k] = dict(st[k], host=me)
            elif st.get("tmux") or st.get("session_id"):  # the bot's own tmux server here; or a print-mode
                name = st.pop("tmux", None) or "dc-%s" % str(cid)[-4:]  # conversation with no window yet
                pane = next((p for p in found.get("panes") or [] if p.get("session") == name), None)
                st["home"] = {"host": me, "socket": (pane or {}).get("socket") or found.get("socket"), "session": name,
                              "window": ""}
                if pane:
                    st["pane"] = {"host": me, "socket": pane["socket"], "id": pane["id"], "pid": pane.get("pid")}
            else:
                continue  # no conversation yet: the primary opens it there when there is one
            if st.get("mode") == "print":  # (its print turns would run there, without the session file)
                st["mode"] = "tmux"
    return out


def what_matters(channels, asks=()):
    """A hash of what a takeover needs of the channels (MATTERS of each) and of the questions waiting (`asks`: their
    ids)."""
    rows = {cid: {k: st.get(k) for k in MATTERS if k in st} for cid, st in (channels or {}).items() if isinstance(st, dict)}
    return hash(json.dumps([rows, sorted(asks)], sort_keys=True, default=str))


PLACED = ("session_id", "pane", "home", "tmux")  # where a channel's conversation is: a change pushed at once


def what_placed(channels):
    """A hash of where each channel's conversation is (PLACED): an attach, a new conversation, a window opened."""
    rows = {cid: {k: st.get(k) for k in PLACED if k in st} for cid, st in (channels or {}).items() if isinstance(st, dict)}
    return hash(json.dumps(rows, sort_keys=True, default=str))


def due(since, changed, placed=False):
    """Push now? `since`: seconds since the last push. `placed`: where a conversation is changed (what_placed()):
    not held back PUSH_SECS (C-163 drill problem A, manager 11-09 (c): a pane attached again, the bot down within
    five minutes, the standby had the old pane)."""
    return since >= FULL_SECS or (changed and (placed or since >= PUSH_SECS))


def bundle(state, info, extra=(), dirs=(), channels_text=None):
    """The files of SYNCED and SYNCED_DIRS in `state` that are there, `extra` ((name in the bundle, path): the
    questions waiting, ask.bundled()), the folders `dirs` (there even when empty: "no questions" is not "nothing
    known"; #code-review on 43bfe53) and meta.json (`info`): base64 of a gzip tar. `channels_text`: channels.json
    as the bot has it in memory, in place of the file (C-175: the file is written off the event loop and may be a
    moment behind; #code-review on 0b43435)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in dirs:
            folder = tarfile.TarInfo(name)
            folder.type, folder.mode, folder.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
            tar.addfile(folder)
        for name, path in extra:
            if os.path.isfile(path):
                tar.add(path, arcname=name)
        for name in SYNCED:
            path = os.path.join(state, name)
            if name == "channels.json" and channels_text is not None:
                data = channels_text.encode("utf-8")
                member = tarfile.TarInfo(name)
                member.size, member.mtime, member.mode = len(data), int(time.time()), 0o644
                tar.addfile(member, io.BytesIO(data))
            elif os.path.isfile(path):
                tar.add(path, arcname=name)
        for name in SYNCED_DIRS:
            folder = os.path.join(state, name)
            if os.path.isdir(folder):
                for dirpath, _, files in os.walk(folder):
                    for f in files:
                        full = os.path.join(dirpath, f)
                        tar.add(full, arcname=os.path.relpath(full, state))
        data = json.dumps(info).encode()
        member = tarfile.TarInfo("meta.json")
        member.size, member.mtime = len(data), int(time.time())
        tar.addfile(member, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


BASE = "takeover-base.json"  # in the standby's state: channels.json as it took it in at its takeover (keep_base())


def keep_base(standby_dir, state):
    """At a takeover, beside take_in(): the channels as pushed, kept as they were (failback_marks() compares)."""
    import shutil
    src, dst = os.path.join(standby_dir, "channels.json"), os.path.join(state, BASE)
    if os.path.isfile(src):
        shutil.copyfile(src, dst + ".tmp")
        os.replace(dst + ".tmp", dst)
    elif os.path.exists(dst):
        os.remove(dst)


def failback_marks(channels, state):
    """At a failback, on the standby: {"untouched": [...], "gone": [...]} for the push's meta. `channels`: as the
    primary is to have them (failback_channels()). Untouched: what a takeover acts on (MATTERS) the same as when
    it took them in (BASE): the standby did not change it, so the primary's own copy, maybe newer than its last
    push, stays (C-163 drill problem A, manager 11-09 (b)). Gone: taken in and not there now (removed meanwhile).
    {} when there is no BASE to compare with (everything taken as pushed, as before)."""
    try:
        with open(os.path.join(state, BASE)) as f:
            base = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(base, dict):
        return {}

    def key(st):
        return json.dumps({k: st.get(k) for k in MATTERS if k in st} if isinstance(st, dict) else st, sort_keys=True, default=str)
    return {"untouched": sorted(c for c, st in (channels or {}).items() if c in base and key(base[c]) == key(st)),
            "gone": sorted(c for c in base if c not in (channels or {}))}


def failback_merge(pushed, own, marks):
    """On the primary, taking a failback in: the channels pushed, but the primary's own copy (`own`, its
    channels.json before) for each one the standby left untouched (failback_marks(); also when the primary has none
    of it now: removed after its last push), and none of those the standby removed. `own` not a dict with channels
    in it (unreadable, empty): as pushed."""
    if not isinstance(own, dict) or not own or not isinstance(pushed, dict) or not marks:
        return pushed
    untouched, gone = set(marks.get("untouched") or ()), set(marks.get("gone") or ())
    out = {c: st for c, st in pushed.items() if c not in untouched}
    out.update({c: st for c, st in own.items() if c in untouched or (c not in pushed and c not in gone)})
    return out


def failback_bundle(state, channels, info, extra=(), dirs=()):
    """The failback's push (section 5, step 1): the SYNCED files of `state`, with `channels` in place of its
    channels.json, and meta.json (`info`, with "failback": True)."""
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="failback.")
    try:
        for name in SYNCED:
            if name != "channels.json" and os.path.isfile(os.path.join(state, name)):
                shutil.copyfile(os.path.join(state, name), os.path.join(tmp, name))
        for name in SYNCED_DIRS:
            if os.path.isdir(os.path.join(state, name)):
                shutil.copytree(os.path.join(state, name), os.path.join(tmp, name))
        with open(os.path.join(tmp, "channels.json"), "w") as f:
            json.dump(channels, f)
        return bundle(tmp, dict(info, failback=True), extra, dirs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def failback_waiting(standby_dir, taken_at):
    """On the primary, before it starts: a failback push not taken in yet (its meta's time, newer than `taken_at`,
    the last one taken in); None else."""
    try:
        with open(os.path.join(standby_dir, "meta.json")) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    at = got.get("at") if got.get("failback") else None
    return at if isinstance(at, (int, float)) and at > (taken_at or 0) else None


def recovered_channels(standby_dir, me, epoch, own_channels):
    """On the primary becoming the bot again with no failback push (the standby took over and then went down
    itself; #code-review on 8778f8d, manager): the channels the standby pushed while it was the bot (its meta's
    epoch bigger than `epoch`, this machine's own before; newer than `own_channels`, the path of this machine's
    channels.json), turned as a failback would (failback_channels: what was opened there stays there, named).
    (channels, meta) or None."""
    try:
        with open(os.path.join(standby_dir, "meta.json")) as f:
            got = json.load(f)
        with open(os.path.join(standby_dir, "channels.json")) as f:
            pushed = json.load(f)
    except (OSError, ValueError):
        return None
    if got.get("failback") or not got.get("from") or got.get("from") == me:
        return None
    if not isinstance(got.get("epoch"), int) or got["epoch"] <= (epoch or 0):
        return None
    try:
        mine = os.stat(own_channels).st_mtime
    except OSError:
        mine = 0
    if not isinstance(got.get("at"), (int, float)) or got["at"] <= mine:
        return None
    found = {"socket": got.get("bot_socket"), "panes": []}  # (that machine is down: its windows are not known)
    return failback_channels(pushed, got["from"], me, found), got


def unpack(text, target):
    """`dcremote state-in`: the bundle in `text` into `target` (state-standby/), whole: unpacked into a temporary
    folder beside it, then put in place, with a .gitignore of its own ("*": git leaves it alone). Only plain files, under
    relative names without "..". The names written."""
    raw = base64.b64decode(text.encode("ascii"))
    tmp = "%s.tmp.%d" % (target, os.getpid())
    os.makedirs(tmp)
    written = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = os.path.normpath(m.name)
            if os.path.isabs(name) or name.startswith("..") or "/../" in name:
                continue
            if m.isdir():
                os.makedirs(os.path.join(tmp, name), exist_ok=True)
                continue
            if not m.isfile():
                continue
            path = os.path.join(tmp, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with tar.extractfile(m) as src, open(path, "wb") as dst:
                dst.write(src.read())
            written.append(name)
    with open(os.path.join(tmp, ".gitignore"), "w") as f:  # (its own, not dcbot/.gitignore: a change there makes
        f.write("*\n")  # the supervisor's rollback only partial; v0.6.0 B9)
    old = "%s.old.%d" % (target, os.getpid())
    if os.path.isdir(target):
        os.rename(target, old)
    os.rename(tmp, target)
    if os.path.isdir(old):
        import shutil
        shutil.rmtree(old, ignore_errors=True)
    return written


MERGED = ("cc_health.json",)  # by machine: the others' entries as the bot had them, this machine's own kept (section
# 3.1: "its own entry it works out itself"; #code-review on 9d2b7bd: a pin to an older Claude Code must survive)


def merge_machines(pushed, own, me):
    """cc_health.json at a takeover: the pushed file with this machine's own entry (if it has one) kept."""
    out = dict(pushed) if isinstance(pushed, dict) else {}
    machines = dict(out.get("machines") or {}) if isinstance(out.get("machines"), dict) else {}
    mine = ((own or {}).get("machines") or {}).get(me) if isinstance((own or {}).get("machines"), dict) else None
    if mine is not None:
        machines[me] = mine
    else:
        machines.pop(me, None)  # (what the bot knew of this machine is not this machine's own word)
    out["machines"] = machines
    return out


def take_in(standby_dir, state, me=None):
    """At a takeover, on the standby: what was pushed goes into its own state (its own heartbeat, pid and the like
    stay its own: only the SYNCED files and folders are put in; MERGED ones keep this machine's entry). The names."""
    import shutil
    taken = []
    for name in SYNCED:
        src = os.path.join(standby_dir, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(state, name)
        if name in MERGED:
            def read(p):
                try:
                    with open(p) as f:
                        return json.load(f)
                except (OSError, ValueError):
                    return {}
            with open(dst + ".tmp", "w") as f:
                json.dump(merge_machines(read(src), read(dst), me), f)
        else:
            shutil.copyfile(src, dst + ".tmp")
        os.replace(dst + ".tmp", dst)
        taken.append(name)
    for name in SYNCED_DIRS:
        src = os.path.join(standby_dir, name)
        if os.path.isdir(src):
            dst = os.path.join(state, name)
            shutil.rmtree(dst + ".tmp", ignore_errors=True)
            shutil.copytree(src, dst + ".tmp")
            shutil.rmtree(dst, ignore_errors=True)
            os.rename(dst + ".tmp", dst)
            taken.append(name)
    return taken


def checks(standby_dir, root, now=None):
    """Section 3.4, before a takeover: (blocking reason's key in messages.LEASE or None, the state's time when older
    than STALE_SECS (taken over all the same, said), {"theirs", "mine"} engine versions when they differ (said, not
    blocking: manager 11-08)). Blocking: no state pushed yet, another protocol (PROTOCOL), other requirements."""
    try:
        with open(os.path.join(standby_dir, "meta.json")) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return "why_no_state", None, None
    if got.get("protocol") != PROTOCOL:
        return "why_protocol", None, None
    if got.get("requirements", "") != requirements(root):
        return "why_requirements", None, None
    age = (now or time.time()) - (got.get("at") or 0)
    note = time.strftime("%m-%d %H:%M", time.localtime(got.get("at") or 0)) if age > STALE_SECS else None
    mine = engine_version(root)
    engines = {"theirs": version_said(got["engine"], got.get("release")), "mine": version_said(mine, release_name(root))} \
        if got.get("engine") and mine and got["engine"] != mine else None
    return None, note, engines
