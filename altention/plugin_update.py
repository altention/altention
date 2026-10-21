"""External plugins on this machine (C-61): each one the instance declares (plugins.json "plugins.<name>": "source"
and "version", or "local": true) is put in <root>/plugins/<name> at that version, its setup[] items are run once per
version, and the system tools it needs are looked for. install.sh's step core.plugins-external (ops/install.py) uses
this; E3's update card will too (follow()).

- ensure(name, entry): clone (beside it first, moved in once whole) or check out the declared version. A folder there
  whose origin is not the declared source, that is not a git repo, has changes not committed, or a file of the
  person's (not tracked, or ignored) with the name of one the new version adds (C-66's check) is never touched, only
  said. A local plugin is only looked for. What git said goes to the log, not to the person.
- run_setup(name, folder, d, version): the setup[] items with a command, in order; one "lazy" is left for the plugin
  to ask for when it is first used, one run before for the same version and the same command is not run again
  (state/plugins/<name>.json, written after each item), one "optional" that fails is said and is not a failure; an
  item runs SETUP_TIMEOUT at most, and no longer than what is left before `deadline` (its whole process group
  killed then); one cut by the deadline counts as timing out when it had half of SETUP_TIMEOUT or more, or was the
  plugin's first item run this round (manager 2026-10-20, #code-review-2 of 0f90704: with STEP_LIMIT ==
  SETUP_TIMEOUT a stuck first item was always "cut", never given up on), else it is not written down and runs next
  time; with less than LAST_START left none starts (the step never outlasts the card's time for install). Nothing but what plugin.json
  writes is run (decision 5: no install.sh found by itself).
- needs_missing(folder, d): the "needs" whose check does not pass here, with their hint; nothing is installed.

Nothing happens at import and HOME is read at call time. Standard library only, Python 3.8.
"""
import json
import os
import shutil
import signal
import subprocess
import time

from . import config, plugin

GIT_TIMEOUT = 600     # a clone or fetch of a plugin
SETUP_TIMEOUT = 900   # one setup item (manager 2026-10-20: 15 minutes)
STEP_LIMIT = 900      # install's whole external-plugin step: an item gets what is left of it, at most SETUP_TIMEOUT
LAST_START = 60       # less than this left: no new item ("later")
NEED_TIMEOUT = 30     # one needs check


def folder_of(name, root=None):
    return os.path.join(root or config.root(), plugin.EXTERNAL, name)


def state_path(name):
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "plugins", "%s.json" % name)


def _git(folder, *args, timeout=GIT_TIMEOUT):
    try:
        return subprocess.run(["git", "-C", folder] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 124, "", str(e))


def _said(r):
    return ((r.stderr or "").strip().splitlines() or (r.stdout or "").strip().splitlines() or ["沒有說明"])[-1][:200]


def declared(settings=None):
    """[(name, entry)]: the external plugins the instance declares, in name order ({} entries and drafts left out).
    [] when the settings cannot be read."""
    if settings is None:
        try:
            settings = config.settings()
        except Exception:
            return []
    block = settings.get("plugins") if isinstance(settings, dict) else None
    if not isinstance(block, dict):
        return []
    return [(n, e) for n, e in sorted(block.items()) if isinstance(e, dict) and plugin.source_of(e) == (True, None)
            and plugin.check_name(n)]


def wanted_here(name, entry, machine):
    """Whether this machine should have the plugin's folder: not turned off here (disabled_on), and "on" (when it
    says one) names it. A plugin placed on one machine by its plugin.json alone is cloned everywhere it is not
    turned off: where it runs is only known from that plugin.json."""
    if machine in plugin._names(entry.get("disabled_on")):
        return False
    on = plugin._names(entry.get("on"))
    return not on or machine in on


def head(folder):
    r = _git(folder, "rev-parse", "-q", "--verify", "HEAD^{commit}", timeout=30)
    return r.stdout.strip() if r.returncode == 0 else ""


def in_the_way(folder, now, want):
    """The files not tracked here, or ignored, with the name of one `want` adds over `now` (update.in_the_way's check,
    C-66): a checkout refuses the first and overwrites the second without a word."""
    def names(*args):
        r = _git(folder, *args, timeout=60)
        return set(l for l in (r.stdout or "").splitlines() if l.strip())
    added = names("diff", "--name-only", "--diff-filter=A", now, want)
    loose = names("ls-files", "--others", "--exclude-standard") | names("ls-files", "--others", "--ignored", "--exclude-standard")
    return sorted(added & loose)


def ensure(name, entry, root=None, log=None):
    """(status, words, version): status "made" (cloned), "moved" (checked out another version), "same", "local",
    "away" (local, no folder here), or "kept" (there but not ours to change: said why) / "failed"; version is the
    commit the folder is at afterwards ("" when unknown). `log(text)` gets what git said when it failed."""
    def told(r):
        if log:
            log("%s: git %s" % (name, " ".join((r.stderr or r.stdout or "").split())[:500]))
    folder = folder_of(name, root)
    if entry.get("local"):
        if not os.path.isdir(folder):
            return "away", "實例寫 local，這台沒有 %s" % folder, ""
        return "local", "本地插件 %s" % folder, head(folder) if os.path.isdir(os.path.join(folder, ".git")) else ""
    source, version = entry["source"].strip(), entry["version"].strip()
    if not os.path.lexists(folder):
        os.makedirs(os.path.dirname(folder), exist_ok=True)
        tmp = "%s.cloning-%d" % (folder, os.getpid())
        shutil.rmtree(tmp, ignore_errors=True)
        r = _git(os.path.dirname(folder), "clone", "-q", "--no-checkout", "--", source, tmp)
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return "failed", "clone %s 失敗：%s" % (source, _said(r)), ""
        want = _git(tmp, "rev-parse", "-q", "--verify", "%s^{commit}" % version, timeout=30).stdout.strip()
        r = _git(tmp, "checkout", "-q", "--detach", want) if want else None
        if r is None or r.returncode != 0:
            if r is not None:
                told(r)
            shutil.rmtree(tmp, ignore_errors=True)
            return "failed", "%s 裡找不到 %s" % (source, version) if r is None else "%s 換到 %s 失敗（git 的原文在 log）" % (
                source, version), ""
        os.rename(tmp, folder)
        return "made", "clone 了 %s，在 %s" % (source, version), head(folder)
    if not os.path.isdir(os.path.join(folder, ".git")):
        return "kept", "%s 已經有東西，但不是 git repo：沒有動（不要的話移走，例如 mv %s %s.mine）" % (folder, folder, folder), ""
    origin = _git(folder, "remote", "get-url", "origin", timeout=30).stdout.strip()
    if origin != source:
        return "kept", "%s 的 origin 是 %s，不是實例寫的 %s：沒有動" % (folder, origin or "（沒有）", source), head(folder)
    edits = _git(folder, "status", "--porcelain", "--untracked-files=no", timeout=60)
    if edits.returncode != 0 or edits.stdout.strip():
        return "kept", "%s 有還沒 commit 的修改：沒有換版本，修改都還在" % folder, head(folder)
    want = _git(folder, "rev-parse", "-q", "--verify", "%s^{commit}" % version, timeout=30).stdout.strip()
    if not want:
        _git(folder, "fetch", "-q", "--tags", "origin")
        want = _git(folder, "rev-parse", "-q", "--verify", "%s^{commit}" % version, timeout=30).stdout.strip()
    if not want:
        return "failed", "%s 裡找不到 %s（fetch 過了）" % (source, version), head(folder)
    now = head(folder)
    if now == want:
        return "same", "在 %s" % version, now
    loose = in_the_way(folder, now, want)
    if loose:
        return "kept", "%s 裡有你自己的檔（沒追蹤或被忽略）跟 %s 要加的檔同名：%s；沒有換版本，檔都還在（改名或移走再重跑）" % (
            folder, version, "、".join(loose[:10])), now
    r = _git(folder, "checkout", "-q", "--detach", want)
    if r.returncode != 0:
        told(r)
        return "failed", "換到 %s 失敗（git 的原文在 log）" % version, head(folder)
    return "moved", "從 %s 換到 %s" % (now[:12] or "（不明）", version), want


def _read_state(name):
    try:
        with open(state_path(name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(name, d):
    path = state_path(name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def _run(cmd, folder, timeout):
    """(exit status, output, killed) of `cmd` in bash, in a process group of its own (not a session: whoever kills
    install's session, update_go.run past its limit, stops it too): past `timeout` the whole group is killed (a
    download it started too) and killed is True; an item ending 124 by itself is not. Interrupted (Ctrl-C on
    install, which the group's own processes do not get) the group goes too, then the interrupt goes on.
    setpgrp in preexec_fn: process_group= is 3.11 on."""
    try:
        p = subprocess.Popen(["bash", "-c", cmd], cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, errors="replace", preexec_fn=os.setpgrp)
    except OSError as e:
        return 127, str(e), False
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or "", False
    except subprocess.TimeoutExpired:
        _kill(p)
        try:  # all it printed, from the start (the exception's part would be said twice)
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # one that left the group (setsid) still holds the pipe: not waited for
            p.stdout.close()
            out = ""
        return 124, (out or "") + "\n（%d 秒沒有結束，已中止）" % timeout, True
    except BaseException:  # KeyboardInterrupt, SystemExit: no setup item left running behind
        _kill(p)
        raise


GIVE_UP = 2  # an item failing (or timing out) this many times in a row, same version and command: not run by itself


def manual(name, item):
    """The command that runs one setup item by hand (and writes it down: a stuck one is taken up again)."""
    return "python3 %s setup %s %s" % (os.path.join(os.path.realpath(plugin.ENGINE), "ops", "plugins.py"), name, item)


def run_setup(name, folder, d, version, settings=None, log=None, deadline=None, only=None):
    """[(item name, status, words)] for the setup[] items of plugin `name` at `version` (a commit, "" when not
    known: then an item is run again only when its command changed). status: "ran", "same" (run before for this
    version and command), "lazy", "record" (run is null: a note only), "later" (not started, or cut, by `deadline`),
    "stuck" (failed GIVE_UP times in a row for this version and command: not run again by itself; manual() runs it),
    "blocked" (an item before it that is not optional did not go through: the steps are written in order and may
    need each other), "failed", "optional-failed". `log(text)` receives each command and its output. Each item's
    result is written as soon as it ends: a run killed midway takes up only the items not done. `only`: that one
    item, run whatever its record says (by hand, a lazy one too)."""
    out, state = [], _read_state(name)
    done = state.get("setup") if isinstance(state.get("setup"), dict) else {}
    held = None  # the item that stops the rest
    first = True  # no item run yet this round
    for item in d.get("setup", []):
        n = item.get("name")
        if only is not None and n != only:
            continue
        if item.get("run") is None:
            out.append((n, "record", item.get("today") or ""))
            continue
        if item.get("lazy") and only is None:
            out.append((n, "lazy", "用到時再裝"))
            continue
        if held is not None:
            out.append((n, "blocked", "前一項 %s 沒過，沒跑" % held))
            continue
        bad = "optional-failed" if item.get("optional") else "failed"
        try:
            cmd = plugin.expand(item["run"], folder, settings)
        except plugin.PluginError as e:
            out.append((n, bad, str(e)))
            held = None if item.get("optional") else n
            continue
        before = done.get(n) if isinstance(done.get(n), dict) else {}
        same = before.get("run") == cmd and before.get("version") == version
        if only is None and same and before.get("ok"):
            out.append((n, "same", "%s 跑過了" % (version[:12] or "這個指令")))
            continue
        if only is None and same and not before.get("ok") and int(before.get("fails") or 0) >= GIVE_UP:
            out.append((n, "stuck", "失敗 %d 次，不再自動重跑；手動跑：%s" % (int(before["fails"]), manual(name, n))))
            held = None if item.get("optional") else n
            continue
        limit = SETUP_TIMEOUT
        if deadline is not None:
            left = int(deadline - time.time())
            if left < LAST_START:
                out.append((n, "later", "下次再裝"))
                continue
            limit = min(SETUP_TIMEOUT, left)
        if log:
            log("== %s/setup %s: %s" % (name, n, cmd))
        rc, text, killed = _run(cmd, folder, limit)
        was_first, first = first, False
        if killed and limit < SETUP_TIMEOUT // 2 and not was_first:  # cut short by the step's time: not its fault
            if log:
                log(text)
                log("== %s/setup %s: 這一步的時間到了，下次再跑" % (name, n))
            out.append((n, "later", "時間到了，下次再裝"))
            continue
        if log:
            log(text)
            log("== %s/setup %s: exit %s" % (name, n, rc))
        ok = rc == 0
        fails = 0 if ok else (int(before.get("fails") or 0) + 1 if same and not before.get("ok") else 1)
        done[n] = {"run": cmd, "version": version, "ok": ok, "fails": fails, "at": int(time.time())}
        state["setup"] = done
        _write_state(name, state)
        if ok:
            out.append((n, "ran", "跑好了"))
            continue
        tail = [x.strip() for x in text.splitlines() if x.strip()][-2:]
        words = "結束碼 %s：%s" % (rc, " / ".join(x[:120] for x in tail) or "沒有輸出")
        if fails >= GIVE_UP:
            words += "；失敗 %d 次了，之後不再自動重跑，手動跑：%s" % (fails, manual(name, n))
        out.append((n, bad, words))
        held = None if item.get("optional") else n
    return out


def stuck(name, folder, d, version, settings=None):
    """[(item, words)] of `name`'s setup items given up on (GIVE_UP failures in a row) for `version` and their
    command as it is now, for doctor; a new version or command, or a run that went through, clears it."""
    done = _read_state(name).get("setup")
    out = []
    for item in d.get("setup", []) if isinstance(done, dict) else []:
        n, before = item.get("name"), done.get(item.get("name"))
        if item.get("run") is None or not isinstance(before, dict) or before.get("ok"):
            continue
        try:
            cmd = plugin.expand(item["run"], folder, settings)
        except plugin.PluginError:
            continue
        if before.get("run") == cmd and before.get("version") == version and int(before.get("fails") or 0) >= GIVE_UP:
            out.append((n, "%s 的安裝步驟 %s 失敗 %d 次，不再自動重跑；手動跑：%s" % (name, n, int(before["fails"]), manual(name, n))))
    return out


def needs_missing(folder, d, settings=None):
    """[(need name, hint)] of plugin.json "needs" whose check fails here (exit status not 0, or it cannot be run).
    Only looked for, never installed (no sudo)."""
    missing = []
    for need in d.get("needs", []):
        try:
            rc, _, _ = _run(plugin.expand(need["check"], folder, settings), folder, NEED_TIMEOUT)
            ok = rc == 0
        except plugin.PluginError:
            ok = False
        if not ok:
            missing.append((need["name"], need.get("hint") or ""))
    return missing
