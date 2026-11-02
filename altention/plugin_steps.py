"""What a plugin still needs on this machine before it is set up (C-77 G1), worked out afresh each time.

Three levels, from what its plugin.json declares (nothing says the level itself):
1. only setup[]: install.sh runs it, nobody is asked;
2. needs[] with "install": system tools the person installs, asked first (a command starting with `sudo ` is theirs
   to run, in their own terminal);
3. guide[]: steps the person takes outside (an account, a developer page), one at a time, each checked.

A check, in needs[] and in guide[], is a command; its exit status: 0 done; 3 (CANNOT) this machine cannot tell (an
engine too old, a core command missing, a step that does not apply here): the item is asked instead (its "ask",
else a plain question), the answer kept like that of a step whose check is null; anything else not yet, said with
the last lines it printed. Past CHECK_TIMEOUT, or not able to start, is not yet too. A check that gave 3 is run again
the next time: 0 then counts, the kept answer only stands while it still gives 3.

Nothing is kept but those answers (ANSWERS, at call time): a plan picks up where the person stopped because every
check is run again. plan() runs commands the plugin wrote (its checks) and nothing else; it installs nothing.

- plan(name, folder, d, settings): {plugin, level, os, setup, needs, guide, next, left, offers, failing}; each item
  has a "state": ok, todo, ask (to be asked), declined (an optional one the person said no to), unchecked (no time
  left); a step with a secret has "stored" (this machine has a value for it, by its length only). "left" counts what #setup still goes through, the asked ones too; "failing" names the steps a check just
  said are not done, the only ones install and doctor speak of: a step nobody can check is no fact to warn about
  (a plugin set up before its guide[] existed has no answers kept; #code-review of bb05de1).
- said(name, key, answer): keep "yes" or "no" for a step (its id) or a need ("needs:<name>").
- lines(p): the plan in words, for #setup's Claude to follow as it is (ops/setup.py plugin-steps).

Standard library only, Python 3.8.
"""
import json
import os
import platform
import time

from . import plugin, plugin_update, secret_store
from .text import t

CANNOT = 3           # a check's exit status: this machine cannot tell
CHECK_TIMEOUT = 30   # one check
PLAN_LIMIT = 120     # all of one plan's checks: the rest are "unchecked" past it
DOCTOR_LIMIT = 60    # each plugin's plan in a whole doctor, which makes one for every plugin (#code-review of bb05de1)
DONE = ("ok", "declined")


def answers_path(name):
    return os.path.join(os.path.expanduser("~/.local/state/altention/setup/guide"), "%s.json" % name)


def answers(name):
    """{key: {"answer": "yes" | "no", "at": seconds}}; {} when there are none or the file cannot be read."""
    try:
        with open(answers_path(name), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def said(name, key, answer):
    d = answers(name)
    d[key] = {"answer": answer, "at": int(time.time())}
    path = answers_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def os_name():
    """One of plugin.OS_NAMES for this machine, or "" when it is none of them."""
    if platform.system() == "Darwin":
        return "macos"
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            info = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return ""
    words = ("%s %s" % (info.get("ID", ""), info.get("ID_LIKE", ""))).replace('"', "").split()
    for name, like in (("debian", ("debian", "ubuntu")), ("fedora", ("fedora", "rhel")), ("arch", ("arch",))):
        if any(w in like for w in words):
            return name
    return ""


def run_check(cmd, folder, settings, deadline):
    """("ok" | "cannot" | "todo" | "unchecked", the reason in its last lines)."""
    left = deadline - time.time()
    if left < 1:
        return "unchecked", t("steps.unchecked")
    try:
        rc, out, killed = plugin_update._run(plugin.expand(cmd, folder, settings), folder, min(CHECK_TIMEOUT, left))
    except plugin.PluginError as e:
        return "todo", t("steps.cannot_run", error=e)
    if killed:
        return "todo", t("steps.timeout", seconds=min(CHECK_TIMEOUT, int(left)))
    tail = "\n".join([l for l in out.strip().splitlines() if l.strip()][-3:])[-300:]
    return {0: "ok", CANNOT: "cannot"}.get(rc, "todo"), tail


def _expanded(cmd, folder, settings):
    """A step's "run" with its {…} filled in; "" when it has none or names a setting not set (said by its check)."""
    try:
        return plugin.expand(cmd, folder, settings) if cmd else ""
    except plugin.PluginError:
        return ""


def _asked(key, optional, kept):
    a = (kept.get(key) or {}).get("answer")
    if a == "yes":
        return "ok"
    return "declined" if a == "no" and optional else "ask"


def plan(name, folder, d, settings=None, deadline=None):
    deadline = deadline if deadline is not None else time.time() + PLAN_LIMIT
    kept, here = answers(name), os_name()
    needs = []
    for n in d.get("needs", []):
        st, why = run_check(n["check"], folder, settings, deadline)
        if st == "cannot":
            st = _asked("needs:" + n["name"], n.get("optional", False), kept)
        cmd = (n.get("install") or {}).get(here)
        needs.append({"name": n["name"], "state": st, "reason": why if st in ("todo", "unchecked") else "",
                      "why": n.get("why", ""), "size": n.get("size", ""), "optional": n.get("optional", False),
                      "for": n.get("for", []), "install": cmd, "sudo": bool(cmd and cmd.startswith("sudo ")),
                      "hint": n.get("hint", ""), "ask": n.get("ask") or t("steps.ask_need", name=n["name"])})
    steps = []
    for g in d.get("guide", []):
        if g.get("check") is None:
            st, why = "cannot", ""
        else:
            st, why = run_check(g["check"], folder, settings, deadline)
        if st == "cannot":
            st = _asked(g["id"], g.get("optional", False), kept)
        steps.append({"id": g["id"], "title": g["title"], "state": st, "reason": why if st in ("todo", "unchecked") else "",
                      "why": g.get("why", ""), "url": g.get("url", ""), "do": g.get("do", []),
                      "run": _expanded(g.get("run"), folder, settings),
                      "secret": g.get("secret"), "stored": bool(g.get("secret") and secret_store.length(name, g["secret"]["name"])),
                      "optional": g.get("optional", False),
                      "ask": g.get("ask") or t("steps.ask_step", title=g["title"]),
                      "after": g.get("after") if g.get("after") is not None
                      else [s["id"] for s in steps if not s["optional"]]})
    state = {s["id"]: s["state"] for s in steps}
    nxt = next((s["id"] for s in steps if s["state"] not in DONE and all(state.get(a) in DONE for a in s["after"])), None)
    left = [x for x in needs + steps if x["state"] not in DONE and not x["optional"]]
    failing = [x for x in steps if x["state"] == "todo" and not x["optional"]]
    offers = [x for x in needs + steps if x["state"] not in DONE and x["optional"]]
    level = 3 if steps else 2 if any(n.get("install") for n in d.get("needs", [])) else 1
    try:
        keep_gates(name, d, steps)
    except OSError:  # a plan is still a plan; the gates stay as they were
        pass
    return {"plugin": name, "level": level, "os": here, "setup": [s["name"] for s in d.get("setup", []) if not s.get("lazy")],
            "lazy": [s["name"] for s in d.get("setup", []) if s.get("lazy")],  # left for first use (#display-dev)
            "needs": needs, "guide": steps, "next": nxt, "left": len(left), "offers": len(offers),
            "failing": [x["id"] for x in failing]}


# --- gates (C-77 G5): a step's "gate" holds back setup items and schedules until that step is done ---------------
# Read without running any check (apply, the scheduler and install's setup items read GATES, each plan writes it).
# Only a step whose check said "not done" holds (a fact, as for install's and doctor's words); an item is held only
# until it was let go once: a step failing again later never takes away what runs already (support-2 of G5: no
# removal on a guess). No file, or one that cannot be read: nothing held.

def gate_path(name):
    return os.path.join(os.path.expanduser("~/.local/state/altention/setup/gate"), "%s.json" % name)


def _gate_state(name):
    try:
        with open(gate_path(name), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def keep_gates(name, d, steps):
    """After a plan: the steps failing now (a step not checked this time keeps what it was), and every gated item
    whose step is not failing let go for good."""
    before = _gate_state(name)
    was = set(before.get("failing") or [])
    failing = sorted(s["id"] for s in steps if s["state"] == "todo" or (s["state"] == "unchecked" and s["id"] in was))
    released = set(before.get("released") or [])
    for g in d.get("guide", []):
        if g["id"] not in failing and next((s for s in steps if s["id"] == g["id"]), {}).get("state") != "unchecked":
            released |= set(g.get("gate") or [])
    if not d.get("guide") and not before:
        return
    path = gate_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"failing": failing, "released": sorted(released), "at": int(time.time())}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def gated(name, d):
    """{setup item or schedule name: the title of the step it waits for}, from the last plan's GATES file."""
    st = _gate_state(name)
    failing, released = set(st.get("failing") or []), set(st.get("released") or [])
    out = {}
    for g in d.get("guide", []):
        if g["id"] in failing:
            for item in g.get("gate") or []:
                if item not in released:
                    out.setdefault(item, g["title"])
    return out


MARK = {"ok": "✅", "todo": "⬜", "ask": "❓", "declined": "➖", "unchecked": "…"}


def _need_words(n):
    if n["state"] not in ("todo", "unchecked"):
        return {"ok": t("steps.need_ok"), "declined": t("steps.need_declined")}.get(n["state"]) or t("steps.need_ask", ask=n["ask"])
    how = t("steps.need_how", cmd=n["install"]) + (t("steps.need_sudo") if n["sudo"] else "") if n["install"] \
        else t("steps.need_hint", hint=n["hint"]) if n["hint"] else t("steps.need_no_how")
    return "；".join(x for x in (t("steps.need_missing") if n["state"] == "todo" else n["reason"], n["why"], n["size"],
                                 t("steps.need_optional", what="、".join(n["for"])) if n["optional"] else "", how) if x)


def _step_words(s):
    if s["state"] == "todo":
        return t("steps.step_todo_why", why=s["reason"]) if s["reason"] else t("steps.step_todo")
    return {"ok": t("steps.step_ok"), "declined": t("steps.step_declined"),
            "ask": t("steps.step_ask", ask=s["ask"])}.get(s["state"], s["reason"])


def _secret_words(plugin_name, s):
    """For a step with a secret not done: whether this machine has it, and the terminal command (C-77 G3)."""
    if not s.get("secret") or s["state"] in DONE:
        return ""
    return t("steps.secret_stored" if s.get("stored") else "steps.secret_missing", plugin=plugin_name, name=s["secret"]["name"])


def lines(p):
    out = [t("steps.head", plugin=p["plugin"], level=p["level"], words=t("steps.level%d" % p["level"])),
           t("steps.setup", items="、".join(p["setup"]) or t("steps.none"))]
    if p.get("lazy"):
        out.append(t("steps.setup_lazy", items="、".join(p["lazy"])))
    if p["needs"]:
        out.append(t("steps.needs"))
    out += ["  %s %s：%s" % (MARK[n["state"]], n["name"], _need_words(n)) for n in p["needs"]]
    if p["guide"]:
        out.append(t("steps.guide"))
    out += ["  %d. %s %s（%s）：%s%s%s" % (i, MARK[s["state"]], s["title"], s["id"], _step_words(s),
                                          _secret_words(p["plugin"], s), t("steps.next_mark") if s["id"] == p["next"] else "")
            for i, s in enumerate(p["guide"], 1)]
    step = next((s for s in p["guide"] if s["id"] == p["next"]), None)
    if p["left"]:
        out.append(t("steps.left_next", n=p["left"], title=step["title"], id=step["id"]) if step else t("steps.left", n=p["left"]))
    else:
        out.append(t("steps.done_offers", n=p["offers"]) if p["offers"] else t("steps.done"))
    return out
