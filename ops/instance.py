#!/usr/bin/env python3
"""The instance: the owner's private settings and data, in its own repo (DESIGN.md 4.1, stage 2.3a).

  python3 ops/instance.py init            a new instance on this machine (<root>/instance), local only until it is
                                          given a remote (install.sh runs it when there is none)
  python3 ops/instance.py seed REPO_URL   once, on one machine: fill the empty instance repo from what the engine
                                          holds today (memory/, the machine list, ssh_config) and push it
  python3 ops/instance.py join REPO_URL   on every machine (apply.py runs it): clone the instance, point
                                          ~/.config/altention/instance at it, link the root folder; the next sync
                                          (altention/sync.py, chatlog's cron) then moves this machine's links over
  python3 ops/instance.py leave           undo `join` on this machine (the way back): memory written in the
                                          instance is merged into the engine's memory/ first, then the memory link
                                          and the SSH Include point at the engine again and the pointer is removed
  python3 ops/instance.py pull [--take-refused]  update this machine's instance now, its settings checked first
                                          like the sync's (altention/sync.py update_instance); --take-refused
                                          (apply.py runs it) also takes an upstream that check holds back
  python3 ops/instance.py status          where this machine stands (for doctor and people)

Both are safe to run again. Nothing is deleted: a real memory folder that the link replaces is renamed, not
removed (altention/sync.py). Standard library, Python 3.8.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)
from altention import config  # noqa: E402


def system_timezone(fallback="UTC"):
    """This machine's IANA time zone ($TZ, else what /etc/localtime points at, else /etc/timezone), or fallback when
    none of them says."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz and not tz.startswith("/"):
        return tz
    try:
        path = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in path:
            return path.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        with open("/etc/timezone") as f:
            name = f.readline().strip()
        if "/" in name:
            return name
    except OSError:
        pass
    return fallback


def plugins_skeleton():
    """A new instance's plugins.json: this machine's time zone; zh-TW, the only language the texts are in yet."""
    return {"format": 1, "core": {"language": "zh-TW", "timezone": system_timezone()},
            "sync": {"interval": "15m", "dirs": {}, "owns": ["knowledge"]}}


GITATTRIBUTES = "# appended to from several machines: merged, never a conflict\nmemory/MEMORY.md merge=union\n"
README = """# my-altention

這個使用者的 Altention 實例：私人設定和資料（記憶、機器清單、SSH 別名、插件設定）。
引擎（程式）在另一個 repo；引擎靠 `~/.config/altention/instance` 找到這裡。
由 `ops/instance.py` 建立，`altention/sync.py` 每 15 分鐘同步。
"""


KNOWLEDGE = """# 知識庫

整理好、長期有效的說明（這套系統怎麼運作、使用者的偏好和決定、踩過的坑）。引擎的 `knowledge/` 是指到這裡的連結，
同步會把它提交並推到其他機器。
"""


def git(path, *args, timeout=300):
    return subprocess.run(["git", "-C", path] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout)


def place():
    return os.path.join(config.root(), "instance")


def settle(dest):
    """A clone whose HEAD names no branch (the remote's default is not set, or it was empty when cloned): fetch
    and take main, so a clone made before the seed, or one cut short, is not stuck "empty" for ever."""
    if git(dest, "rev-parse", "-q", "--verify", "HEAD").returncode == 0:
        return
    git(dest, "fetch", "-q", "origin")
    if git(dest, "rev-parse", "-q", "--verify", "origin/main").returncode == 0:
        git(dest, "checkout", "-q", "-B", "main", "--track", "origin/main")


def clone(url, dest=None, need_content=False):
    """Cloned into <dest>.cloning and moved into place only when complete: a clone cut short (sleep, a dropped
    SSH) leaves nothing at the place that counts as this machine's instance."""
    dest = dest or place()
    if os.path.isdir(os.path.join(dest, ".git")):
        settle(dest)
        return dest
    if os.path.exists(dest) and os.listdir(dest):
        sys.exit("instance：%s 已存在而且不是 git repo，沒有動它" % dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    work = dest + ".cloning"
    shutil.rmtree(work, ignore_errors=True)  # an earlier attempt cut short: start again
    r = subprocess.run(["git", "clone", "-q", url, work], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=600)
    if r.returncode != 0:
        shutil.rmtree(work, ignore_errors=True)
        sys.exit("instance：clone %s 失敗：%s" % (url, r.stderr.strip()[:300]))
    settle(work)
    if need_content and git(work, "rev-parse", "-q", "--verify", "HEAD").returncode != 0:
        shutil.rmtree(work, ignore_errors=True)  # nothing at the place that counts as an instance
        sys.exit("instance：%s 還是空的，先在一台機器跑 `ops/instance.py seed`" % url)
    if os.path.isdir(dest):
        os.rmdir(dest)  # an empty folder only (checked above)
    os.rename(work, dest)
    return dest


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)  # a reader never sees half a file


def write_new(path, text):
    """Write a file only if it is not there yet (never overwrite what the owner has)."""
    if not os.path.exists(path):
        write_atomic(path, text)
        return True
    return False


def seed(url):
    """Filled in a folder of its own and moved into place only once pushed: the instance's place counts as "this
    machine has an instance" the moment it exists, and a sync must not walk into it half made."""
    final = place()
    if os.path.isdir(os.path.join(final, ".git")):
        print("instance：%s 已經有了，不重新填（要補檔案請直接改實例）" % final)
        return 0
    dest = final + ".seeding"
    if os.path.exists(dest):
        shutil.rmtree(dest)  # what an earlier, interrupted seed left: never pushed, made again from the engine
    clone(url, dest)
    if git(dest, "rev-parse", "-q", "--verify", "HEAD").returncode == 0:
        shutil.rmtree(dest)
        sys.exit("instance：%s 已經有內容了，沒有重新填；在這台改用 `ops/instance.py join`" % url)
    git(dest, "checkout", "-q", "-b", "main")
    shutil.copytree(os.path.join(ENGINE, "memory"), os.path.join(dest, "memory"))
    for src, name in (("ops/machines.json", "machines.json"), ("ops/ssh_config", "ssh_config")):
        if os.path.exists(os.path.join(ENGINE, src)):
            shutil.copy2(os.path.join(ENGINE, src), os.path.join(dest, name))
    machines = config.read_json(os.path.join(dest, "machines.json"))
    if isinstance(machines, dict) and "format" not in machines:
        machines = dict([("format", 1)] + list(machines.items()))
        write_atomic(os.path.join(dest, "machines.json"), json.dumps(machines, ensure_ascii=False, indent=2) + "\n")
    plugins = plugins_skeleton()
    write_new(os.path.join(dest, "plugins.json"), json.dumps(plugins, ensure_ascii=False, indent=2) + "\n")
    write_new(os.path.join(dest, ".gitattributes"), GITATTRIBUTES)
    write_new(os.path.join(dest, "README.md"), README)
    write_new(os.path.join(dest, ".gitignore"), "__pycache__/\n*.tmp\n")
    git(dest, "add", "-A")
    r = git(dest, "commit", "-q", "-m", "instance: seeded from the engine (memory, machine list, ssh aliases)")
    if r.returncode != 0:
        sys.exit("instance：commit 失敗：%s" % r.stderr.strip()[:300])
    r = git(dest, "push", "-q", "-u", "origin", "main")
    if r.returncode != 0:
        sys.exit("instance：push 失敗：%s（%s 留著，重跑 seed 會重新做）" % (r.stderr.strip()[:300], dest))
    os.rename(dest, final)  # only now is it this machine's instance
    print("instance：已建立並推上 %s；這台接著跑 `ops/instance.py join` 指向它" % url)
    return 0


def init():
    """A local instance for a machine that has none (3a.9, install.sh): memory/, the settings skeleton, a git repo
    with no remote, and the pointer. Made in a folder of its own and moved into place only when whole (an install
    cut short and run again starts that folder over). 0 made or already there; 1 refused (said why)."""
    final = place()
    if os.path.isdir(os.path.join(final, ".git")):
        print("instance：%s 已經有了" % final)
        return 0
    if os.path.lexists(final):  # something else is there: the owner's, never taken over
        print("instance：%s 已經有東西，但不是實例；沒有動它" % final)
        return 1
    dest = final + ".initializing"
    if os.path.lexists(dest):
        shutil.rmtree(dest)  # what an earlier, interrupted init left: never used, made again
    os.makedirs(os.path.join(dest, "memory"))
    write_new(os.path.join(dest, "memory", "MEMORY.md"), "")
    os.makedirs(os.path.join(dest, "knowledge"))  # the engine's knowledge/ links here (sync.owns)
    write_new(os.path.join(dest, "knowledge", "README.md"), KNOWLEDGE)
    write_new(os.path.join(dest, "plugins.json"), json.dumps(plugins_skeleton(), ensure_ascii=False, indent=2) + "\n")
    write_new(os.path.join(dest, ".gitattributes"), GITATTRIBUTES)
    write_new(os.path.join(dest, "README.md"), README)
    write_new(os.path.join(dest, ".gitignore"), "__pycache__/\n*.tmp\n")
    for args in (["init", "-q"], ["checkout", "-q", "-b", "main"], ["add", "-A"]):
        r = git(dest, *args)
        if r.returncode != 0:
            print("instance：git %s 失敗：%s" % (args[0], r.stderr.strip()[:200]))
            return 1
    who = [] if git(dest, "config", "user.email").stdout.strip() else ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]
    r = git(dest, *(who + ["commit", "-q", "-m", "instance: made on this machine (no remote yet)"]))
    if r.returncode != 0:
        print("instance：commit 失敗：%s" % r.stderr.strip()[:200])
        return 1
    os.rename(dest, final)  # only now is it this machine's instance
    os.makedirs(config.config_dir(), exist_ok=True)
    pointer = os.path.join(config.config_dir(), "instance")
    write_atomic(pointer, final + "\n")
    print("instance：在 %s 建了本機實例（沒有遠端；要在多台共用時再設定遠端）" % final)
    return 0


def join(url, rejoin=False):
    pointer = os.path.join(config.config_dir(), "instance")
    left = os.path.join(config.config_dir(), "left")
    current = open(pointer).read().strip() if os.path.exists(pointer) else ""
    if (os.path.exists(left) or current == "-") and not rejoin:  # left on purpose: apply must not quietly undo that
        print("instance：這台之前用 leave 離開了實例，沒有自動接回；要接回請跑 `ops/instance.py join --rejoin %s`" % url)
        return 0
    if os.path.exists(left):
        os.remove(left)
    dest = clone(url, need_content=True)
    if git(dest, "rev-parse", "-q", "--verify", "HEAD").returncode != 0:
        sys.exit("instance：%s 還是空的，先在一台機器跑 `ops/instance.py seed`" % url)
    os.makedirs(config.config_dir(), exist_ok=True)
    if current in ("", "-"):  # none yet, or rejoining: point at it (a pointer to somewhere else is kept)
        with open(pointer, "w") as f:
            f.write(dest + "\n")
    links = {"engine": ENGINE}
    for rel, target in links.items():
        link = os.path.join(config.root(), rel)
        if not os.path.lexists(link):
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(target, link)
    print("instance：這台已接上 %s；下一次同步（核心的 cron_sync，最多 15 分鐘）會把記憶和 SSH 的連結改過去" % dest)
    return 0


def leave():
    from altention import sync
    try:
        inst = config.instance_dir()
    except config.ConfigError:
        inst = None
    engine_mem = os.path.join(ENGINE, "memory")
    if inst and os.path.isdir(os.path.join(inst, "memory")) and os.path.isdir(engine_mem) and not os.path.islink(engine_mem):
        twice = sync.merge_into(os.path.join(inst, "memory"), engine_mem, config.this_machine(), print)
        if twice:
            print("instance：兩邊內容不同的記憶檔另存了：%s" % "、".join(twice))
    for link in sync.memory_links():
        if os.path.islink(link):
            os.remove(link)
            os.symlink(engine_mem, link)
            print("instance：記憶資料夾改回指向引擎：%s" % link)
    cfg = os.path.realpath(os.path.expanduser("~/.ssh/config"))
    if inst and os.path.isfile(cfg):
        with open(cfg) as f:
            text = f.read()
        line = "Include %s" % os.path.join(inst, "ssh_config")
        if line in text:
            tmp = cfg + ".tmp"
            with open(tmp, "w") as f:
                f.write(text.replace(line, "Include %s" % os.path.join(ENGINE, "ops", "ssh_config")))
            os.chmod(tmp, 0o600)
            os.replace(tmp, cfg)
            print("instance：~/.ssh/config 改回引用引擎的 ssh_config")
    # Gone in a way an older engine understands too (a rollback of the engine may follow): no pointer, and the
    # folder moved out of the place an engine looks by default. A separate mark keeps apply from joining again.
    os.makedirs(config.config_dir(), exist_ok=True)
    pointer = os.path.join(config.config_dir(), "instance")
    if os.path.exists(pointer):
        os.remove(pointer)
    kept = None
    if inst and os.path.realpath(inst) == os.path.realpath(place()):
        kept = "%s.left-%d" % (place(), time.time())
        os.rename(place(), kept)
    with open(os.path.join(config.config_dir(), "left"), "w") as f:
        f.write("left the instance at %s; `ops/instance.py join --rejoin URL` to join again\n" % time.strftime("%F %T"))
    print("instance：這台已離開實例（實例資料夾移到 %s，沒有刪）" % (kept or inst or "?"))
    return 0


def pull(take):
    """This machine's instance up to its upstream now, under its lock, the way the sync does it (update_instance:
    the upstream's settings checked first); take: also an upstream that check holds back, the way out a person takes
    when the check is wrong (apply.py). What waited here is pushed by the next sync round."""
    from altention import sync
    try:
        inst = config.instance_dir()
    except config.ConfigError as e:
        print("instance：%s" % e)
        return 1
    if not inst or not os.path.isdir(os.path.join(inst, ".git")):
        print("instance：這台沒有實例的 git repo，不用更新")
        return 0
    repo = sync.Repo(inst)
    try:
        with sync.locked(repo):
            code, note, held = sync.update_instance(repo, take=take)
    except sync.Busy as e:
        print("instance：%s（同步還在跑），這次沒有更新實例" % e)
        return 1
    if code:
        print("instance：更新實例失敗：%s" % note)
        return 1
    if held:
        print("instance：%s；要越過檢查用 --take-refused" % held)
        return 1
    if note:  # took an upstream the settings check held back: said, so a run of apply for something else knows
        print("instance：%s" % note)
    print("instance：實例在 %s" % git(inst, "rev-parse", "--short", "HEAD").stdout.strip())
    return 0


def status():
    try:
        inst = config.instance_dir()
        src = config.load("machines.json")[1]
    except config.ConfigError as e:
        print("設定讀不出來：%s" % e)
        return 1
    print("實例：%s" % (inst or "（還沒有）"))
    print("機器清單來源：%s" % src)
    from altention import sync
    st = config.read_json(sync.status_path()) or {}
    print("連結：%s" % json.dumps(st.get("links") or {}, ensure_ascii=False))
    return 0


def main(argv):
    if argv[:1] != ["status"] and argv:  # every other command writes this machine's instance, pointer or links
        from altention import engine_id
        why = engine_id.refusal(ENGINE, "instance.py %s" % argv[0])
        if why:
            print(why)
            return 1
    rejoin = "--rejoin" in argv
    argv = [a for a in argv if a != "--rejoin"]
    if argv[:1] == ["init"]:
        return init()
    if len(argv) >= 2 and argv[0] == "seed":
        return seed(argv[1])
    if len(argv) >= 2 and argv[0] == "join":
        rc = join(argv[1], rejoin)
        if not rc:
            print("ALT-STEP-OK instance-join")  # ops/apply.py's remote step checks for it (not just the exit status)
        return rc
    if argv[:1] == ["pull"]:
        rc = pull("--take-refused" in argv)
        if not rc:
            print("ALT-STEP-OK instance-pull")  # ops/apply.py's remote step checks for it
        return rc
    if argv[:1] == ["status"]:
        return status()
    if argv[:1] == ["leave"]:
        return leave()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
