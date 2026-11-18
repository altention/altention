#!/usr/bin/env python3
"""A standby for the bot (C-163 d3a, design: instance docs/refactor/c163-design.md sections 2, 8, 10).

  altention standby status [--json]          the machines that could be a standby, and which one is
  altention standby setup MACHINE auto       MACHINE takes over when the bot's machine stops: it gets the role
                                             dcbot-standby, a copy of the bot's token (by ssh, on stdin only), and
                                             apply
  altention standby setup MACHINE notify     MACHINE only tells: the role, a Discord webhook in the notification
                                             channel stored there (one test message by it), and apply

#setup asks it on a new installation with two machines or more (ops/setup.py: standby, standby.mode), and runs it
when the owner says 設備援 later. The token and the webhook address are read and sent in this one process, through
secret_store's places: never on a command line (not in argv: `ps` does not show them), never printed or logged; #audit
says only that it was done. Exit status: 0 done, 1 refused or failed (said why), 2 wrong usage.
Standard library only, Python 3.8.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import config, secret_store  # noqa: E402

ROLE = "dcbot-standby"
READY = True  # the manager's switch (11-08): False until the drill (design section 7) passed; the drill passed and the
# manager turned it on (11-11), in a commit of its own. Never an instance setting: nobody turns it off or on by mistake.
# Off again (False): standby setup refuses, #setup does not ask, doctor does not offer; a standby set up before stays.
TEST_VAR = "ALTENTION_STANDBY_TEST"  # the drill's rig turns it on for itself: this variable at 1 and a HOME that is
# not this user's own (a scratch one): a real installation never has both


def ready():
    """Can a standby be set up here: READY, or the drill's rig (TEST_VAR=1 in a scratch HOME)."""
    return READY or drill_rig()


def drill_rig():
    """The drill's rig and nothing else: TEST_VAR=1 and a HOME that is not this user's own. What only the drill may
    do asks this, never ready() (READY turned on one day must not open them: DCBOT_DRILL_SENDER, manager 11-08)."""
    if os.environ.get(TEST_VAR) != "1":
        return False
    try:
        import pwd
        real = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError):
        return False
    return os.path.realpath(os.path.expanduser("~")) != os.path.realpath(real)
API = "https://discord.com/api/v10"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


class Failed(Exception):
    """A step that did not happen: its words are for the owner."""


def candidates():
    """The machines of the list that could be the standby: not the bot's, reached by ssh. [(name, is one now)]."""
    bot = config.bot_machine()[0]
    out = []
    for name, m in (config.inventory().get("machines") or {}).items():
        if name != bot and config.ssh_aliases(m):
            out.append((name, ROLE in ((m or {}).get("roles") or [])))
    return out


IDS = ("guild-id", "owner-id")  # the core's secrets besides the token that the bot reads (bot/envfile.SECRETS)


def read_secret(plugin_name, name):
    """This machine's stored secret's value (secret_store's place), None when there is none. Kept in this process."""
    try:
        with open(secret_store.path(plugin_name, name), encoding="utf-8") as f:
            value = f.read()
    except (OSError, secret_store.Refused):
        return None
    value = value[:-1] if value.endswith("\n") else value
    return value or None


def secret_command(machine, plugin_name, name):
    """The command run on `machine` to store a secret given on its stdin (`altention secret set … --stdin`): only the
    plugin's and the secret's names are in it, never the value."""
    m = (config.inventory().get("machines") or {}).get(machine) or {}
    return "%s/bin/altention secret set %s %s --stdin --via setup" % (config.engine_on(m), plugin_name, name)


def run_there(machine, command, value):
    """`command` on `machine` by ssh (each alias in turn), `value` on its stdin. (ok, what it said)."""
    m = (config.inventory().get("machines") or {}).get(machine) or {}
    said = ""
    for alias in config.ssh_aliases(m):
        try:
            r = subprocess.run(SSH + [alias, command], input=value, capture_output=True, universal_newlines=True,
                               timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            said = str(e)
            continue
        if r.returncode == 0:
            return True, r.stdout.strip()
        said = (r.stderr or r.stdout).strip()[-300:]
        if r.returncode != 255:  # (reached, and the command itself said no: another alias would say the same)
            break
    return False, said


def send_secret(machine, plugin_name, name, value):
    ok, said = run_there(machine, secret_command(machine, plugin_name, name), value)
    if not ok:
        raise Failed("存不到 %s 上（%s）" % (machine, said or "連不上"))


def instance_lock(inst, wait=60):
    """The instance repository's git lock (<git dir>/altention-git.lock, flock: the sync's own), held while the
    machine list is changed and committed. An open file (close it to let go), or Failed after `wait` seconds."""
    import fcntl
    import time
    r = subprocess.run(["git", "-C", inst, "rev-parse", "--absolute-git-dir"], capture_output=True, universal_newlines=True,
                       timeout=60)
    if r.returncode != 0:
        raise Failed("實例不是 git repo")
    f = open(os.path.join(r.stdout.strip(), "altention-git.lock"), "a")
    until = time.time() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.time() > until:
                f.close()
                raise Failed("實例正在同步，%d 秒內拿不到它的鎖；過一下再試" % wait)
            time.sleep(1)


def add_role(machine):
    """The role dcbot-standby on `machine` in the instance's machines.json, committed (under the sync's lock) and
    pushed (the other machines' apply read it). True when it changed. A commit that fails puts the file back."""
    inst = config.instance_dir()
    path = os.path.join(inst, "machines.json")
    git = ["git", "-C", inst]
    lock = instance_lock(inst)
    try:
        with open(path, encoding="utf-8") as f:
            before = f.read()
        d = json.loads(before)
        m = (d.get("machines") or {}).get(machine)
        if m is None:
            raise Failed("機器清單裡沒有 %s" % machine)
        roles = m.setdefault("roles", [])
        if ROLE in roles:
            return False
        roles.append(ROLE)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(path + ".tmp", path)
        subprocess.run(git + ["add", "machines.json"], capture_output=True, timeout=60)
        who = [] if subprocess.run(git + ["config", "user.email"], capture_output=True, timeout=60).stdout.strip() else \
            ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]
        r = subprocess.run(git[:1] + who + git[1:] + ["commit", "-q", "-m", "standby: %s takes the role %s" % (machine, ROLE)],
                           capture_output=True, universal_newlines=True, timeout=60)
        if r.returncode != 0:
            with open(path, "w", encoding="utf-8") as f:  # (put back: a role said failed must not be added after all)
                f.write(before)
            subprocess.run(git + ["reset", "-q", "--", "machines.json"], capture_output=True, timeout=60)
            raise Failed("實例 commit 失敗：%s" % (r.stderr or r.stdout).strip()[:200])
    finally:
        lock.close()
    if subprocess.run(git + ["remote"], capture_output=True, universal_newlines=True, timeout=60).stdout.strip():
        subprocess.run(git + ["push", "-q"], capture_output=True, timeout=120)  # (else the sync pushes it)
    return True


def discord(method, path, body=None, token=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bot " + token, "Content-Type": "application/json",
                                          "User-Agent": "DiscordBot (altention, 1.0)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
        return json.loads(data) if data else None


WEBHOOK_NAME = "altention standby"


def make_webhook(token):
    """The webhook in the notification channel (core.notify_channel) by WEBHOOK_NAME: the one there already when
    there is one (set up again: no second one left behind), else made by the bot. Its address. Failed when the bot
    may not (Manage Webhooks) or the channel is not there."""
    guild = read_secret("core", "guild-id")
    name = config.setting("core.notify_channel", "notifications")
    try:
        found = [c for c in discord("GET", "/guilds/%s/channels" % guild, token=token) or [] if c.get("name") == name]
        if not found:
            raise Failed("找不到通知頻道 #%s" % name)
        hooks = discord("GET", "/channels/%s/webhooks" % found[0]["id"], token=token) or []
        hook = next((h for h in hooks if h.get("name") == WEBHOOK_NAME and h.get("token")), None) or \
            discord("POST", "/channels/%s/webhooks" % found[0]["id"], {"name": WEBHOOK_NAME}, token=token)
    except urllib.error.HTTPError as e:
        raise Failed("機器人建不了 webhook（Discord 回 %s；要「管理 Webhook」權限）" % e.code)
    return "https://discord.com/api/webhooks/%s/%s" % (hook["id"], hook["token"])


VIEW, SEND, HISTORY = 1 << 10, 1 << 11, 1 << 16


def make_lease_channel(token):
    """#bot-lease (dcbot.channels.lease, else bot-lease), hidden (design 1.1: nobody but the bot sees or writes it),
    in the system category when there is one: made by the bot when it is not there. True when made."""
    guild = read_secret("core", "guild-id")
    name, system = bot_channel_names()
    try:
        chans = discord("GET", "/guilds/%s/channels" % guild, token=token) or []
        if any(c.get("name") == name and c.get("type") == 0 for c in chans):
            return False
        me = discord("GET", "/users/@me", token=token)["id"]
        body = {"name": name, "type": 0, "topic": "機器人自己用來確定由哪台負責的頻道，不用理它。",
                "permission_overwrites": [{"id": str(guild), "type": 0, "deny": str(VIEW)},
                                          {"id": str(me), "type": 1, "allow": str(VIEW | SEND | HISTORY)}]}
        cat = next((c for c in chans if c.get("type") == 4 and c.get("name") == system), None)
        if cat:
            body["parent_id"] = cat["id"]
        discord("POST", "/guilds/%s/channels" % guild, body, token=token)
    except urllib.error.HTTPError as e:
        raise Failed("機器人建不了 #%s（Discord 回 %s；要「管理頻道」權限）" % (name, e.code))
    return True


def bot_channel_names():
    """(#bot-lease's name, the system category's) as the bot itself reads them (dcbot/bot/config.py channel_name: the
    instance's dcbot.channels.*, else its env file, else the default): never two answers (#code-review-2 on 3a4e6b1)."""
    dcbot = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dcbot")
    if dcbot not in sys.path:
        sys.path.insert(0, dcbot)
    from bot import config as bot_config
    return bot_config.LEASE_CHANNEL, bot_config.SYSTEM_CATEGORY


def restart_bot():
    """The bot on this machine asked to restart (its restart-requested mark, as !restart writes it): its lease starts
    with the standby on the list."""
    state = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dcbot", "state")
    with open(os.path.join(state, "restart-requested"), "w") as f:
        json.dump({"why": "standby"}, f)


def test_webhook(url, primary, machine):
    from altention import text
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json", "User-Agent": "altention"},
                                 data=json.dumps({"content": text.t("standby.webhook_test", primary=primary, standby=machine),
                                                  "allowed_mentions": {"parse": []}}).encode())
    with urllib.request.urlopen(req, timeout=20):
        pass


def apply(machine):
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops",
                                                     "apply.py"), machine], capture_output=True, universal_newlines=True,
                       timeout=1800)
    # 2: done, with something left for a person there (a git user not set, ...): the engine is up to date all the same,
    # so not a reason to stop (the drill's item 23, manager 11-10); its words then say so (TODO_MARK)
    last = (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]
    return r.returncode in (0, 2), ([TODO_MARK] if r.returncode == 2 else last)


TODO_MARK = "todo"  # apply()'s `last` when it ended with something left for a person (exit 2)


def setup(machine, mode, out=None, steps=None):
    """setup MACHINE auto|notify (the module's words). `steps`: the functions to use (tests). Raises Failed. In this
    order (manager on 2956b55): that machine's engine brought up to date (apply: an older one refuses --via setup);
    the secret (the token, or the webhook) handed over; only once it is there, the role; apply again; #bot-lease made
    or found; the bot here restarted so its lease starts. A secret not handed over adds no role."""
    from altention import text
    out = out or sys.stdout
    steps = steps or {}
    if not ready():
        raise Failed(text.t("standby.not_ready"))
    if machine not in dict(candidates()):
        raise Failed(text.t("standby.not_candidate", machine=machine))
    primary = config.bot_machine()[0]
    token = read_secret("core", "discord-token")
    if not token:
        raise Failed(text.t("standby.no_token"))
    ids = dict((n, read_secret("core", n)) for n in IDS)  # (the bot's other two settings: the standby's bot and its
    for n, v in ids.items():  # watch need them as much as the token, notify only too: go-live 11-11, manager)
        if not v:
            raise Failed(text.t("standby.no_id", name=text.t("standby.id_name." + n)))
    ok, last = (steps.get("apply") or apply)(machine)
    if not ok:
        raise Failed(text.t("standby.not_updated", machine=machine, last=last[0]))
    if mode == "auto":
        (steps.get("send_secret") or send_secret)(machine, "core", "discord-token", token)
        out.write(text.t("standby.token_sent", machine=machine) + "\n")
        ping = read_secret("core", "healthchecks-url")
        if ping:  # (the outside notice: whichever machine is the bot pings it, so a takeover is no false "down")
            (steps.get("send_secret") or send_secret)(machine, "core", "healthchecks-url", ping)
            out.write(text.t("standby.health_sent", machine=machine) + "\n")
        audit = text.t("standby.audit_auto", machine=machine, primary=primary)
    else:
        url = (steps.get("make_webhook") or make_webhook)(token)
        (steps.get("send_secret") or send_secret)(machine, "core", "standby-webhook", url)
        (steps.get("test_webhook") or test_webhook)(url, primary, machine)
        out.write(text.t("standby.webhook_set", machine=machine) + "\n")
        audit = text.t("standby.audit_notify", machine=machine, primary=primary)
    for n, v in ids.items():  # (before the role: a standby without them could not read #bot-lease at all)
        (steps.get("send_secret") or send_secret)(machine, "core", n, v)
    out.write(text.t("standby.ids_sent", machine=machine) + "\n")
    added = (steps.get("add_role") or add_role)(machine)
    out.write(text.t("standby.role" if added is not False else "standby.role_had", machine=machine) + "\n")
    ok, last = (steps.get("apply") or apply)(machine)
    said = "standby.applied_todo" if ok and last[:1] == [TODO_MARK] else "standby.applied" if ok else "standby.apply_failed"
    out.write(text.t(said, machine=machine, last=last[0]) + "\n")
    if (steps.get("make_lease_channel") or make_lease_channel)(token):
        out.write(text.t("standby.lease_made") + "\n")
    (steps.get("restart_bot") or restart_bot)()
    secret_store.tell_audit(audit)
    out.write(text.t("standby.restarting", machine=machine) + "\n")
    return 0 if ok else 1


def main(argv):
    from altention import text
    if argv[:1] == ["status"]:
        if not ready():
            print(text.t("standby.not_ready"))
            return 1
        rows = candidates()
        if "--json" in argv:
            print(json.dumps([{"machine": n, "standby": s} for n, s in rows], ensure_ascii=False))
        else:
            for n, s in rows:
                print("%s%s" % (n, "（備援）" if s else ""))
        return 0
    if argv[:1] == ["setup"] and len(argv) == 3 and argv[2] in ("auto", "notify"):
        try:
            return setup(argv[1], argv[2])
        except Failed as e:
            print(text.t("standby.failed", why=e))
            return 1
    print(__doc__.split("\n\n")[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
