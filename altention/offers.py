"""Asking once, in fixed words, whether to put on an optional plugin from the official list (C-64, C-77 G4).

A plugin.json's "offer": {"cost": "...", "when": "install" | "asked"} (plugin.OFFER_WHEN):
- install: among #setup's questions of a new installation;
- asked: only when something calls `setup.py offer <name>` (a condition first true, such as the secretary's first
  event for a calendar plugin).
Never because a plugin is new on the list (owner 10-21, C-81: #update says it once, nobody is asked): the people
asked are the ones doing the thing it helps with. The question is the plugin's "pitch" and its cost (offer.cost,
else made from what it needs: tools and their sizes, steps elsewhere), with three answers: 「好，開」 (yes: #setup goes
on with plugin-add), 「先不要」 (no: never offered again), 「之後再說」 (later: offered again the next time the same
moment comes: setup.py offer again for asked, the reconcile after the next update for install, the person having
asked for that; MAX_LATER laters count as no).

The answers are the person's, kept in the instance's plugins.json: "offers": {name: {"answer", "at", "times"}}
(times: the laters so far). Someone asking for the plugin themselves (「裝 <name>」) is never held back by them.
Only plugins of the official list that the instance does not have yet are offered. Nothing here writes a file:
record() changes the settings it is given (ops/setup.py writes and commits them). Standard library only, Python 3.8.
"""
import time

from . import catalog
from .text import t

MAX_LATER = 3
ANSWERS = ("yes", "no", "later")


def candidates(listed=None, settings=None):
    """{name: plugin.json} of the official list's plugins that declare an offer and that the instance does not name
    in its plugins (on or off: those are decided already)."""
    listed = (catalog.load() or {}).get("plugins") if listed is None else listed
    have = (settings or {}).get("plugins") if isinstance((settings or {}).get("plugins"), dict) else {}
    out = {}
    for name, e in sorted((listed or {}).items()):
        d = e.get("plugin") if isinstance(e, dict) else None
        if isinstance(d, dict) and isinstance(d.get("offer"), dict) and name not in have:
            out[name] = d
    return out


def answered(name, settings):
    rec = ((settings or {}).get("offers") or {}).get(name) if isinstance((settings or {}).get("offers"), dict) else None
    return rec if isinstance(rec, dict) else None


def open_for(name, settings):
    """Whether `name` may be offered: never answered, or answered later fewer than MAX_LATER times."""
    rec = answered(name, settings)
    return rec is None or (rec.get("answer") == "later" and int(rec.get("times") or 0) < MAX_LATER)


def due(moments, listed=None, settings=None, later_only=False):
    """[name] of the candidates whose offer.when is one of `moments` and that may be offered now; later_only: only
    those the person answered 「之後再說」 (the reconcile asks nothing new)."""
    return [n for n, d in candidates(listed, settings).items()
            if d["offer"].get("when", "install") in moments and open_for(n, settings)
            and (not later_only or (answered(n, settings) or {}).get("answer") == "later")]


COST_MOST = 200  # characters of the cost shown (catalog.WORDS's way: words from a plugin.json made plain first)


def cost(d):
    """offer.cost, else what plugin.json says it will need here: its tools (with sizes) and its steps elsewhere;
    made plain as the list's other words are (catalog.plain: #code-review of efd6ade)."""
    return catalog.plain(_cost(d), COST_MOST)


def _cost(d):
    if (d.get("offer") or {}).get("cost"):
        return d["offer"]["cost"]
    tools = ["%s%s" % (n["name"], "（%s）" % n["size"] if n.get("size") else "") for n in d.get("needs", []) if n.get("install")]
    steps = [g for g in d.get("guide", []) if not g.get("optional")]
    parts = ([t("offer.cost_tools", tools="、".join(tools))] if tools else []) + \
            ([t("offer.cost_steps", n=len(steps))] if steps else [])
    return "；".join(parts) or t("offer.cost_none")


def question(name, d):
    """The question as #setup asks it (setup.py questions --json's shape)."""
    return {"key": "offer.%s" % name, "values": list(ANSWERS), "header": t("offer.header"), "multiSelect": False,
            "question": t("offer.question", title=d.get("title") or name, pitch=d.get("pitch") or d.get("description", ""),
                          cost=cost(d)),
            "options": [{"label": t("offer.yes"), "description": t("offer.yes_why")},
                        {"label": t("offer.no"), "description": t("offer.no_why", plugin=name)},
                        {"label": t("offer.later"), "description": t("offer.later_why", most=MAX_LATER)}]}


def record(settings, name, answer):
    """Keep the person's answer in `settings` (the instance's plugins.json, changed in place); the answer kept, which
    is "no" for the MAX_LATER-th later."""
    offers = settings.setdefault("offers", {})
    if not isinstance(offers, dict):
        offers = settings["offers"] = {}
    rec = offers.get(name) if isinstance(offers.get(name), dict) else {}
    times = int(rec.get("times") or 0) + (1 if answer == "later" else 0)
    kept = "no" if answer == "later" and times >= MAX_LATER else answer
    offers[name] = {"answer": kept, "at": int(time.time()), "times": times}
    return kept
