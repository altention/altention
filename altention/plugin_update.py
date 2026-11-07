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
import re
import os
import shutil
import signal
import subprocess
import time

from . import config, plugin, text

GIT_TIMEOUT = 600     # a clone or fetch of a plugin
SETUP_TIMEOUT = 900   # one setup item (manager 2026-10-20: 15 minutes)
STEP_LIMIT = 900      # install's whole external-plugin step: an item gets what is left of it, at most SETUP_TIMEOUT
LAST_START = 60       # less than this left: no new item ("later")
NEED_TIMEOUT = 30     # one needs check


def folder_of(name, root=None):
    return os.path.join(root or config.root(), plugin.EXTERNAL, name)


def state_path(name):
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "plugins", "%s.json" % name)


SLOW_LIMIT = 60  # seconds a clone or fetch may go without progress (manager 2026-10-20: not 600 on a silent source)
_SSH = []  # this process's GIT_SSH_COMMAND for a source over ssh, worked out once


def _git_env():
    """git's environment for a plugin's source: no prompts; over http(s), given up after SLOW_LIMIT seconds with (almost)
    no progress; over ssh, a connect timeout and keep-alives, unless the person set their own ssh command."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_HTTP_LOW_SPEED_LIMIT", "1000")
    env.setdefault("GIT_HTTP_LOW_SPEED_TIME", str(SLOW_LIMIT))
    if not _SSH:
        try:
            own = subprocess.run(["git", "config", "--get", "core.sshCommand"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            own = ""
        _SSH.append("" if own or env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH") else
                    "ssh -o ConnectTimeout=%d -o ServerAliveInterval=15 -o ServerAliveCountMax=%d" % (SLOW_LIMIT, SLOW_LIMIT // 15))
    if _SSH[0]:
        env["GIT_SSH_COMMAND"] = _SSH[0]
    return env


def _git(folder, *args, timeout=GIT_TIMEOUT):
    """git in a process group of its own: past `timeout` the whole group goes (a clone's remote helper too, still
    connected to a source that does not answer: #code-review-2 of c0b20b7), and the words are plain ones."""
    try:
        p = subprocess.Popen(["git", "-C", folder] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, errors="replace",
                             env=_git_env(), preexec_fn=os.setpgrp)
    except OSError as e:
        return subprocess.CompletedProcess(args, 127, "", "git 跑不起來（%s）" % e)
    try:
        out, err = p.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, p.returncode, out, err)
    except subprocess.TimeoutExpired:
        _kill(p)
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return subprocess.CompletedProcess(args, 124, "", "%d 秒都沒有回應，已中止" % timeout)
    except BaseException:
        _kill(p)
        raise


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


def _left(deadline, limit):
    """`limit`, or what is left before `deadline` when that is less (None: no deadline)."""
    return limit if deadline is None else min(limit, int(deadline - time.time()))


def unreachable(source, name=None):
    """The words for a source that did not answer; with `name`, when it has been so since and when it is tried next."""
    words = "連不上它的來源（%s），這輪先跳過" % source
    st = _reach().get(name) if name else None
    if isinstance(st, dict) and st.get("since"):
        words += "（從 %s 起連不上，下次 %s 再試）" % (_hm(st["since"]), _hm(st.get("next") or 0))
    return words


# Backing off from a source that does not answer (manager 2026-10-20): the update round then skips it, longer each
# time it fails again (BACKOFF_FIRST, doubled, at most BACKOFF_MAX), until it answers. Only the rounds back off
# (backoff=True from update.py's plugins round); what a person runs tries anyway, and its result counts too.
BACKOFF_FIRST = 20 * 60  # the next round (every 15 minutes) skips it
BACKOFF_MAX = 6 * 3600


def _hm(t):
    return time.strftime("%m-%d %H:%M", time.localtime(t))


def _reach_path():
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "plugin-src", "unreachable.json")


def _reach():
    try:
        with open(_reach_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _reach_put(d):
    path = _reach_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _reached(name, ok):
    """Write down whether `name`'s source answered: ok clears it, a failure counts and sets when to try next."""
    d = _reach()
    if ok:
        if name in d:
            d.pop(name)
            _reach_put(d)
        return
    st = d.get(name) if isinstance(d.get(name), dict) else {}
    now = time.time()
    fails = int(st.get("fails") or 0) + 1
    d[name] = {"since": st.get("since") or now, "fails": fails,
               "next": now + min(BACKOFF_MAX, BACKOFF_FIRST * 2 ** (fails - 1))}
    _reach_put(d)


def held_off(name):
    """Whether the rounds skip `name`'s source for now (it failed, and its next try is not due)."""
    st = _reach().get(name)
    return isinstance(st, dict) and time.time() < float(st.get("next") or 0)


def unreachable_now():
    """[(name, since, fails, next)] of the sources that did not answer last time, for doctor."""
    return sorted((n, st.get("since"), st.get("fails"), st.get("next")) for n, st in _reach().items() if isinstance(st, dict))


NETWORK = re.compile(r"Could not resolve|Connection (timed out|refused|reset)|timed out|too slow|unable to access|"
                     r"Network is unreachable|No route to host|沒有回應", re.I)  # not "Could not read from remote": a
# repository that is not there says it too


HTTP_ANSWERED = re.compile(r"returned error: 4\d\d")  # the server answered (401, 403, 404…): not the network


def _network(r):
    """Whether a failed git run failed for the network (no answer, too slow, cannot connect), not for what it found
    (a repository not there, a login refused: those are said in git's words and not backed off)."""
    said = (r.stderr or "") + (r.stdout or "")
    return r.returncode == 124 or (bool(NETWORK.search(said)) and not HTTP_ANSWERED.search(said))


def ensure(name, entry, root=None, log=None, deadline=None, backoff=False):
    """(status, words, version): status "made" (cloned), "moved" (checked out another version), "same", "local",
    "away" (local, no folder here), or "kept" (there but not ours to change: said why) / "failed"; version is the
    commit the folder is at afterwards ("" when unknown). `log(text)` gets what git said when it failed. `deadline`:
    a clone or fetch gets what is left of the update round at most; a source that does not answer within it is
    said in plain words (unreachable()), what git said goes to the log only. `backoff` (the rounds): a source held
    off is not tried now."""
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
        limit = _left(deadline, GIT_TIMEOUT)
        if limit <= 0 or (backoff and held_off(name)):
            return "failed", unreachable(source, name), ""
        r = _git(os.path.dirname(folder), "clone", "-q", "--no-checkout", "--", source, tmp, timeout=limit)
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            if _network(r):
                told(r)
                _reached(name, False)
                return "failed", unreachable(source, name), ""
            return "failed", "clone %s 失敗：%s" % (source, _said(r)), ""
        _reached(name, True)
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
        limit = _left(deadline, GIT_TIMEOUT)
        held = limit <= 0 or (backoff and held_off(name))
        r = None if held else _git(folder, "fetch", "-q", "--tags", "origin", timeout=limit)
        if r is None or _network(r):
            if r is not None:
                told(r)
                _reached(name, False)
            return "failed", unreachable(source, name), head(folder)
        if r.returncode == 0:  # a failure that is not the network is no "unreachable" (#code-review of dc6a4ba)
            _reached(name, True)
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


def _gated_words(step):
    return text.t("plugin.gated", step=step)  # (run_setup has a local called text)


def run_setup(name, folder, d, version, settings=None, log=None, deadline=None, only=None):
    """[(item name, status, words)] for the setup[] items of plugin `name` at `version` (a commit, "" when not
    known: then an item is run again only when its command changed). status: "ran", "same" (run before for this
    version and command), "lazy", "record" (run is null: a note only), "later" (not started, or cut, by `deadline`),
    "stuck" (failed GIVE_UP times in a row for this version and command: not run again by itself; manual() runs it),
    "blocked" (an item before it that is not optional did not go through: the steps are written in order and may
    need each other), "gated" (it waits for a guide[] step not done yet, C-77 G5: not run, nothing written; the
    rest go on), "failed", "optional-failed". `log(text)` receives each command and its output. Each item's
    result is written as soon as it ends: a run killed midway takes up only the items not done. `only`: that one
    item, run whatever its record says (by hand, a lazy one too)."""
    out, state = [], _read_state(name)
    done = state.get("setup") if isinstance(state.get("setup"), dict) else {}
    held = None  # the item that stops the rest
    first = True  # no item run yet this round
    from . import plugin_steps  # it imports this module
    gates = plugin_steps.gated(name, d) if only is None else {}  # run by hand: the person asked for that one
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
        if n in gates:  # C-77 G5: waits for a guide[] step; nothing written, so it runs once the gate opens
            out.append((n, "gated", _gated_words(gates[n])))
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


# --- E3: following the declared versions, for altention update (update.py, update_go.py: support-3's card) ---------
# Agreed with support-3 (manager 2026-10-20): the card, the checks' order, go, undo and `update plugin` are
# update.py's and update_go.py's; what a plugin is, where its releases are and what changes with one is here.

KINDS = ("unreachable", "broken", "origin", "edits", "engine", "interface", "test", "perms", "clean")  # most pressing first
TEST_TIMEOUT = 600
FETCH_TIMEOUT = 60


def _tag_key(tag):
    m = re.match(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$", tag or "")
    return tuple(int(x) for x in m.groups()) if m else None


def mirror_of(name):
    """A bare copy of a plugin's source on a machine without its folder (the bot's machine, a plugin placed
    elsewhere): ~/.local/state, at call time."""
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "plugin-src", "%s.git" % name)


def enabled(settings=None):
    """[{name, source, version, update, machines}] of the plugins the instance declares with a source (local ones
    do not update): update is the plugin's own "update" (notify, release) or the instance's core.update; machines,
    the machine list's names it is meant for ("on", else all but disabled_on)."""
    if settings is None:
        try:
            settings = config.settings()
        except Exception:
            return []
    try:
        names = sorted((config.inventory().get("machines") or {}).keys())
    except Exception:
        names = []
    mode = ((settings.get("core") or {}).get("update") if isinstance(settings.get("core"), dict) else None) or "notify"
    out = []
    for name, entry in declared(settings):
        if entry.get("local"):
            continue
        on = plugin._names(entry.get("on"))
        off = set(plugin._names(entry.get("disabled_on")))
        out.append({"name": name, "source": entry["source"].strip(), "version": entry["version"].strip(),
                    "update": entry.get("update") if entry.get("update") in ("notify", "release") else
                    ("release" if mode == "release" else "notify"),
                    "machines": [m for m in (on or names) if m not in off]})
    return out


def _repo_of(p, root=None):
    """(folder to run git in, it is the plugin's own folder): the folder here when it is ours (a git repo whose origin
    is the source), else the mirror."""
    folder = folder_of(p["name"], root)
    if os.path.isdir(os.path.join(folder, ".git")) and \
            _git(folder, "remote", "get-url", "origin", timeout=30).stdout.strip() == p["source"]:
        return folder, True
    return mirror_of(p["name"]), False


def newer(p, fetch=True, root=None, deadline=None, backoff=False):
    """The vX.Y.Z tags on p's source newer than its version, newest first; None when they cannot be read (the source
    not reachable within FETCH_TIMEOUT, or what is left before `deadline`: the card says 「這次查不到」). `backoff`
    (the rounds): a source that did not answer is skipped until its next try is due; each try's result is kept."""
    repo, own = _repo_of(p, root)
    if fetch:
        limit = _left(deadline, FETCH_TIMEOUT)
        if limit <= 0 or (backoff and held_off(p["name"])):
            return None
        if not own and not os.path.isdir(repo):
            os.makedirs(os.path.dirname(repo), mode=0o700, exist_ok=True)
            if _git(os.path.dirname(repo), "init", "-q", "--bare", repo, timeout=30).returncode != 0:
                return None
        r = _git(repo, "fetch", "-q", "--tags", "--", p["source"], "+refs/tags/*:refs/tags/*", timeout=limit)
        if r.returncode != 0:
            if _network(r):
                _reached(p["name"], False)
            return None
        _reached(p["name"], True)
    elif not os.path.isdir(repo):
        return None
    now = _tag_key(p["version"])
    tags = [t for t in _git(repo, "tag", "-l", "v*", timeout=30).stdout.split() if _tag_key(t)]
    return sorted([t for t in tags if now is None or _tag_key(t) > now], key=_tag_key, reverse=True)


def _show(repo, tag, path):
    r = _git(repo, "show", "%s:%s" % (tag, path), timeout=30)
    return r.stdout if r.returncode == 0 else None


def notes(p, tags, lang=None, root=None):
    """[(tag, [lines], cut)] for the card: each release's points from that release's own CHANGELOG.md (update.py's
    release_notes: the installation's language first), 3 at most each; a release without them says so (lines [])."""
    from . import update
    repo, _ = _repo_of(p, root)
    out = []
    for tag in tags:
        text = _show(repo, tag, "CHANGELOG.md")
        got = update.release_notes(None, tag, text=text, most=3, lang=lang) if text else None
        out.append((tag, got[0] if got else [], bool(got and got[1])))
    return out


def _load_at(repo, tag):
    """plugin.json at `tag`, checked; (dict, None) or (None, why)."""
    return _load_at_newer(repo, tag)[:2]


def _load_at_newer(repo, tag):
    """_load_at's (dict or None, why) and whether it is written for a newer engine (plugin.NewerEngine)."""
    found = _show(repo, tag, "plugin.json")
    if found is None:
        return None, "%s 沒有 plugin.json" % tag, False
    try:
        return plugin.check(json.loads(found), "%s:plugin.json" % tag), None, False
    except plugin.NewerEngine as e:
        return None, str(e), True
    except (ValueError, plugin.PluginError) as e:
        return None, str(e), False


def _when_words(w):
    if not isinstance(w, dict):
        return str(w)
    if "every" in w:
        return "每 %s" % w["every"]
    if "at" in w:
        days = w.get("days", "daily")
        return "%s %s" % ("每天" if days in ("daily", None) else "每週 " + ",".join(days), w["at"])
    if "once" in w:
        return "%s 一次" % w["once"]
    return json.dumps(w, ensure_ascii=False)


def added(old, new):
    """What the new plugin.json does on this computer that the old one did not, in plain words (decision 4: such a
    version waits for a yes): hooks, a rewrite of replies (both see the conversations: watchers() asks them again on
    their own, manager 2026-10-21), skills every Claude reads, schedules, setup commands, data (or data made private /
    not), channels."""
    old = old or {}
    out = []
    r, o = new.get("rewrite"), old.get("rewrite")
    if isinstance(r, dict) and (not isinstance(o, dict) or o.get("command") != r.get("command")):
        out.append("%s改寫回覆：%s（每一則 Claude 的回覆貼出前都先交給它，它會看到你的對話內容）" % (
            "新的" if not isinstance(o, dict) else "改了的", r.get("command")))
    olds = set(s.get("name") for s in old.get("skills", []))
    for s in new.get("skills", []):
        if s.get("name") not in olds:
            out.append("所有 Claude 都會讀到它的 skill：%s" % s.get("name"))
    olds = set((h.get("event"), h.get("matcher"), h.get("command")) for h in old.get("hooks", []))
    for h in new.get("hooks", []):
        if (h.get("event"), h.get("matcher"), h.get("command")) not in olds:
            out.append("新的 hook：%s%s（它會看到你所有 Claude 對話的內容）" % (h.get("event"), "（%s）" % h["matcher"] if h.get("matcher") else ""))
    olds = dict((s.get("name"), s) for s in old.get("schedules", []))
    for s in new.get("schedules", []):
        o = olds.get(s.get("name"))
        if o is None or o.get("do") != s.get("do"):
            out.append("%s %s：%s" % ("新的排程" if o is None else "改了要做的事的排程", s.get("name"), _when_words(s.get("when"))))
    olds = dict((s.get("name"), s.get("run")) for s in old.get("setup", []))
    for s in new.get("setup", []):
        if s.get("run") is not None and olds.get(s.get("name")) != s.get("run"):
            out.append("%s %s：%s" % ("新的安裝指令" if s.get("name") not in olds else "改了的安裝指令", s.get("name"), s.get("run")))
    olds = dict((d.get("name"), d) for d in old.get("data", []))
    for d in new.get("data", []):
        o = olds.get(d.get("name"))
        if o is None:
            out.append("新的資料 %s：%s%s" % (d.get("name"), d.get("path"), "（private）" if d.get("private") else ""))
        elif bool(o.get("private")) != bool(d.get("private")):
            out.append("資料 %s 改成%s" % (d.get("name"), "private" if d.get("private") else "不是 private"))
    olds = set(c.get("name") for c in old.get("channels", []))
    for c in new.get("channels", []):
        if c.get("name") not in olds:
            out.append("新的頻道 #%s" % c.get("name"))
    out += asks_more(old, new)
    return out


def asks_more(old, new):
    """C-77 G5: what the new plugin.json asks more of the person (by name, as added() does): a tool to install, new
    or with other install commands (the whole install object compared, every system's); a step elsewhere; a step the
    plugin does itself whose command changed; a key to hand in."""
    out = []
    olds = dict((n.get("name"), n) for n in old.get("needs", []))
    for n in new.get("needs", []):
        if n.get("install") and (n.get("name") not in olds or olds[n.get("name")].get("install") != n.get("install")):
            out.append(text.t("plugin_update.more_tool", name=n.get("name"), size="（%s）" % n["size"] if n.get("size") else ""))
    olds = dict((g.get("id"), g) for g in old.get("guide", []))
    for g in new.get("guide", []):
        if g.get("id") not in olds:
            out.append(text.t("plugin_update.more_step", title=g.get("title")))
        elif g.get("run") and olds[g.get("id")].get("run") != g.get("run"):
            out.append(text.t("plugin_update.more_run", title=g.get("title"), run=g.get("run")))
    olds = set(s.get("name") for s in old.get("secrets", []))
    for s in new.get("secrets", []):
        if s.get("name") not in olds:
            out.append(text.t("plugin_update.more_secret", name=s.get("label") or s.get("name")))
    return out


def watchers(old, new):
    """[words] of what the new plugin.json adds that sees the conversations (its hooks, a rewrite of replies, C-71b):
    asked about once more on its own (decision 4), never taken with the other 「多了」 lines alone."""
    old = old or {}
    olds = set((h.get("event"), h.get("matcher"), h.get("command")) for h in old.get("hooks", []))
    out = ["hook %s%s" % (h.get("event"), "（%s）" % h["matcher"] if h.get("matcher") else "") for h in new.get("hooks", [])
           if (h.get("event"), h.get("matcher"), h.get("command")) not in olds]
    r, o = new.get("rewrite"), old.get("rewrite")
    if isinstance(r, dict) and (not isinstance(o, dict) or o.get("command") != r.get("command")):
        out.append("改寫回覆")
    return out


def _engine_ok(spec, engine_version):
    """Whether `engine_version` (vX.Y.Z) meets plugin.json "engine" (">=X.Y.Z"); True when either is not given."""
    if not spec or not engine_version:
        return True
    m = re.match(r"^>=\s*v?([0-9]+)\.([0-9]+)\.([0-9]+)$", spec.strip())
    have = _tag_key(engine_version)
    return True if not m or have is None else have >= tuple(int(x) for x in m.groups())


def check(p, tag, engine_version=None, root=None):
    """{kind, kinds, why, added}: whether plugin p can move to `tag` here, the most pressing kind first (KINDS):
    unreachable (the tag is not there), broken (its plugin.json not usable), origin (the folder here is not ours:
    another origin, or no git), edits (changes not committed in it, or a file of the person's where the new version
    adds one), engine (it wants a newer engine than `engine_version`, the one this round ends with), interface (its
    "uses" asks for what this engine's altention/interfaces.json does not offer: C-94), test (its
    "test" fails in a copy aside), perms (it does more on this computer: `added`, plain words), clean."""
    repo, own = _repo_of(p, root)
    folder = folder_of(p["name"], root)
    found, why = [], {}
    if _git(repo, "rev-parse", "-q", "--verify", "%s^{commit}" % tag, timeout=30).returncode != 0:
        return {"kind": "unreachable", "kinds": ["unreachable"], "why": "拿不到 %s 的 %s" % (p["name"], tag), "added": []}
    new, bad, newer = _load_at_newer(repo, tag)
    if newer:  # written for a newer engine: the card's "update the engine" kind, not unreadable
        return {"kind": "engine", "kinds": ["engine"], "why": text.t("plugin_update.newer_engine", plugin=p["name"], tag=tag),
                "added": []}
    if new is None:
        return {"kind": "broken", "kinds": ["broken"], "why": "%s 的 %s 讀不懂：%s" % (p["name"], tag, bad), "added": []}
    if os.path.lexists(folder) and not own:
        found.append("origin")
        why["origin"] = "%s 不是從 %s 來的（origin 不同，或不是 git）：不動它" % (folder, p["source"])
    if own:
        edits = _git(folder, "status", "--porcelain", "--untracked-files=no", timeout=60)
        loose = in_the_way(folder, "HEAD", tag)
        if edits.returncode != 0 or edits.stdout.strip() or loose:
            found.append("edits")
            why["edits"] = "%s 裡有你自己的修改%s" % (folder, "，或跟 %s 要加的檔同名的檔（%s）" % (tag, "、".join(loose[:5])) if loose else "")
    if not _engine_ok(new.get("engine"), engine_version):
        found.append("engine")
        why["engine"] = "%s 的 %s 要引擎 %s，這一輪換完是 %s" % (p["name"], tag, new.get("engine"), engine_version)
    if new.get("uses"):
        try:
            problem = plugin.uses_problem(new, plugin.offered_here())
        except plugin.PluginError as e:
            problem = text.t("plugin.interfaces_unreadable", error=e)
        if problem:
            found.append("interface")
            why["interface"] = text.t("plugin_update.interface", plugin=p["name"], tag=tag, problem=problem)
    if new.get("test") and "origin" not in found:
        ok, said = _test_at(repo, tag, new["test"])
        if not ok:
            found.append("test")
            why["test"] = "%s 的 %s 測試沒過：%s" % (p["name"], tag, said)
    old, _ = _load_at(repo, p["version"])
    more = added(old, new)
    if more:
        found.append("perms")
        why["perms"] = "%s 的 %s 會在這台多做：%s" % (p["name"], tag, "；".join(more))
    kinds = [k for k in KINDS if k in found] or ["clean"]
    return {"kind": kinds[0], "kinds": kinds, "why": why.get(kinds[0], ""), "added": more}


def _test_at(repo, tag, test):
    """(ok, words): plugin.json "test" run in a copy of `tag` beside everything (a temporary folder, removed)."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="plugin-test-")
    try:
        arch = subprocess.run(["git", "-C", repo, "archive", tag], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
        if arch.returncode != 0:
            return False, "拿不出 %s 的檔案" % tag
        if subprocess.run(["tar", "-x", "-C", tmp], input=arch.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            return False, "解不開 %s 的檔案" % tag
        try:
            cmd = plugin.expand(test, tmp)
        except plugin.PluginError as e:
            return False, str(e)
        rc, text, _ = _run(cmd, tmp, TEST_TIMEOUT)
        tail = [x.strip() for x in text.splitlines() if x.strip()][-2:]
        return rc == 0, "結束碼 %s：%s" % (rc, " / ".join(x[:120] for x in tail) or "沒有輸出")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _requires(names, root=None):
    """{name: [plugins it requires, among `names`]}, from each one's plugin.json here (the mirror's when not here)."""
    out = {}
    for n in names:
        d = None
        try:
            with open(os.path.join(folder_of(n, root), "plugin.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            pass
        reqs = [x.get("plugin") for x in (d or {}).get("requires", []) if isinstance(x, dict)]
        out[n] = [r for r in reqs if r in names]
    return out


def order(names, root=None):
    """`names` in an order where each comes after the ones it requires (by name otherwise; a loop: by name)."""
    need, done, out = _requires(names, root), set(), []
    left = sorted(names)
    while left:
        ready = [n for n in left if all(r in done for r in need.get(n, []))] or left[:1]
        n = ready[0]
        out.append(n)
        done.add(n)
        left.remove(n)
    return out


def blocked_by(failed, names, root=None):
    """The ones of `names` that need (however far down) one in `failed`, so do not move this time."""
    need, out, grew = _requires(names, root), set(), True
    gone = set(failed)
    while grew:
        grew = False
        for n in names:
            if n not in gone and any(r in gone for r in need.get(n, [])):
                gone.add(n)
                out.add(n)
                grew = True
    return sorted(out)


def switch(p, tag, settings=None, log=None, root=None, deadline=None, backoff=False):
    """(ok, why): plugin p's folder here moved to `tag` (ensure: the same checks as install), its setup[] for the new
    version run (none started past `deadline`: the update round's own limit, support-3), its skills linked. The
    instance is not changed (set_versions). On a failure the folder goes back to the commit it was at; setup items
    that already ran cannot be undone and why says which. Items left for later are no failure: why names them, and
    the next round (or install) takes them up."""
    folder = folder_of(p["name"], root)
    before = head(folder) if os.path.isdir(os.path.join(folder, ".git")) else ""
    st, words, version = ensure(p["name"], dict(p, version=tag), root=root, log=log, deadline=deadline, backoff=backoff)
    if st in ("failed", "kept", "away"):
        return False, words
    try:
        with open(os.path.join(folder, "plugin.json"), encoding="utf-8") as f:
            d = plugin.check(json.load(f), os.path.join(folder, "plugin.json"))
    except (OSError, ValueError, plugin.PluginError) as e:
        d, bad = None, str(e)
    if d is None:
        ran, failed = [], ["plugin.json：%s" % bad]
    else:
        if any(g.get("gate") for g in d.get("guide", [])):  # C-77 G5 (support-2): the gates of this version, here,
            from . import plugin_steps  # before its setup runs (a machine that only follows has made no plan yet)
            limit = time.time() + plugin_steps.PLAN_LIMIT
            try:
                plugin_steps.plan(p["name"], folder, d, settings, min(limit, deadline) if deadline else limit)
            except Exception as e:  # support-2 of 6ede371: no plan is no gate (as an unreadable gate file); the
                if log:  # folder is at the new version already, so its setup goes on rather than stopping here
                    log("== %s: 算不出計畫（%s: %s），這次照沒有 gate 跑" % (p["name"], type(e).__name__, e))
        ran = run_setup(p["name"], folder, d, version, settings, log, deadline)
        failed = ["%s（%s）" % (i, t) for i, s, t in ran if s in ("failed", "stuck")]
    if failed:
        if before:
            _git(folder, "checkout", "-q", "--detach", before)
        done = [i for i, s, _ in ran if s == "ran"]
        return False, "%s 換到 %s 沒成功：%s；已經放回原來的版本%s" % (
            p["name"], tag, "；".join(failed), "（安裝步驟 %s 已經跑過，退不回來）" % "、".join(done) if done else "")
    plugin.link_skills(None, add_only=True)
    later = [i for i, s, _ in ran if s == "later"] if d is not None else []
    gated = [(i, w) for i, s, w in ran if s == "gated"] if d is not None else []  # C-77 G5: said, as later is
    return True, words + ("；%s 下次再裝" % "、".join(later) if later else "") + (
        "；%s" % "；".join("%s %s" % (i, w) for i, w in gated) if gated else "")


def follow(settings=None, log=None, root=None, deadline=None, names=None, backoff=False):
    """[(name, ok, why)]: every plugin declared with a source and wanted on this machine whose folder is not at the
    instance's version moved to it (switch); one not cloned yet, cloned (ensure); one already there is not listed.
    Each machine's update round runs this after its engine step (U5's other machines follow the instance this way).
    `names`: only these (undo, update plugin); `deadline`: passed on to each switch."""
    if settings is None:
        try:
            settings = config.settings()
        except Exception:
            return []
    machine = plugin.this_machine([])
    out = []
    for name, entry in declared(settings):
        if entry.get("local") or not wanted_here(name, entry, machine) or (names is not None and name not in names):
            continue
        folder = folder_of(name, root)
        want = _git(folder, "rev-parse", "-q", "--verify", "%s^{commit}" % entry["version"], timeout=30).stdout.strip() \
            if os.path.isdir(os.path.join(folder, ".git")) else ""
        if want and head(folder) == want:
            continue
        p = {"name": name, "source": entry["source"].strip(), "version": entry["version"].strip()}
        ok, why = switch(p, p["version"], settings, log, root, deadline, backoff)
        out.append((name, ok, why))
    return out


def declare(name, source, version):
    """The instance's plugins.<name> written as {"source", "version"} and committed (C-81 K2: a plugin from the
    official list), under the instance's own git lock, as set_versions; refused when the instance already says
    anything of `name`. (ok, why)."""
    from . import sync
    inst = config.instance_dir()
    if not inst:
        return False, "這台沒有實例"
    repo = sync.Repo(inst)
    path = os.path.join(inst, "plugins.json")
    try:
        with sync.locked(repo):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return False, "實例的 plugins.json 不是物件"
            block = d.setdefault("plugins", {})
            if not isinstance(block, dict):
                return False, "實例的 plugins.json 的 plugins 不是物件"
            if name in block:
                return False, "實例已經寫了插件 %s（%s）：不改它" % (name, json.dumps(block[name], ensure_ascii=False)[:120])
            block[name] = {"source": source, "version": version}
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
            os.replace(path + ".tmp", path)
            repo.git("add", "--", "plugins.json", timeout=60)
            who = [] if repo.git("config", "user.email", timeout=20).stdout.strip() else \
                ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]  # as setup.py's commit()
            r = repo.git(*(who + ["commit", "-q", "-m", "plugins: add %s %s" % (name, version), "--", "plugins.json"]),
                         timeout=60)
            if r.returncode != 0:
                return False, "實例 commit 失敗：%s" % ((r.stderr or r.stdout).strip().splitlines() or [""])[-1][:200]
            return True, ""
    except (OSError, ValueError, sync.Busy) as e:
        return False, "實例改不了：%s" % e


def set_versions(versions, message=None):
    """The instance's plugins.<name>.version set to each of `versions` ({name: tag}) and committed, under the
    instance's own git lock only (never the engine's: update.py lets go of it first). (ok, why)."""
    from . import sync
    inst = config.instance_dir()
    if not inst:
        return False, "這台沒有實例"
    repo = sync.Repo(inst)
    path = os.path.join(inst, "plugins.json")
    try:
        with sync.locked(repo):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            block = d.get("plugins") if isinstance(d, dict) else None
            if not isinstance(block, dict):
                return False, "實例的 plugins.json 沒有 plugins 這一項"
            for name, tag in sorted(versions.items()):
                if not isinstance(block.get(name), dict) or "source" not in block[name]:
                    return False, "實例沒有用 source 寫的插件 %s" % name
                block[name]["version"] = tag
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
            os.replace(path + ".tmp", path)
            repo.git("add", "--", "plugins.json", timeout=60)
            if repo.git("diff", "--cached", "--quiet", "--", "plugins.json", timeout=60).returncode == 0:
                return True, ""
            who = [] if repo.git("config", "user.email", timeout=20).stdout.strip() else \
                ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]  # as setup.py's commit()
            r = repo.git(*(who + ["commit", "-q", "-m", message or "plugins: %s" % "、".join(
                "%s %s" % (n, t) for n, t in sorted(versions.items())), "--", "plugins.json"]), timeout=60)
            if r.returncode != 0:
                return False, "實例 commit 失敗：%s" % ((r.stderr or r.stdout).strip().splitlines() or [""])[-1][:200]
            return True, ""
    except (OSError, ValueError, sync.Busy) as e:
        return False, "實例改不了：%s" % e
