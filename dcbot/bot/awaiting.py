"""What waits for the owner, in one file of the bot's state: state/waiting-on-owner.json,

    {"at": when it was written, "waiting": [{"channel", "kind", "since"}, …]}

(the list empty when nothing waits). For the board that shows what wants the owner (the status plugin reads it);
the bot itself reads it nowhere.

Kinds: "menu" (a dialog or a panel of Claude Code's shown in the channel with buttons: bot/menus.py), "question"
and "plan" (an AskUserQuestion, a plan to approve: bot/ask.py). A reader takes a kind it does not know as one more
thing that waits: more kinds are to come (a permission dialog).

The file is never the record: it is written from what the bot holds (the menus in memory, the questions in
state/asks/) each time one of those changes, whole, a temporary file renamed over it. Only the running bot writes
it (start(), from main() as soon as the bot's state is read, before Discord is reached: the questions still open,
no menu of before the restart, their buttons are dead), and it writes the empty list as it ends (stop()). The
module is used by tests and tools too, which have no business with the running bot's file: until start() nothing
is written.

For whoever reads it:
 - "at" is when the bot last wrote it, which is when something last changed, not a heartbeat. A bot that was
   killed leaves the file as it was: how fresh it is, is told by "at" together with whether the bot runs
   (state/heartbeat), which is the reader's to look at.
 - It says what the bot still holds open. A dialog answered at the terminal stays listed until the conversation's
   next event that moves on (as its buttons stay in the channel till then): "may have been answered at the
   terminal already" is the careful way to show a menu.

Writing it must never be in the way of the menu or the question itself. That it cannot be written is one line in
the bot's log when it begins, and one when it works again.
"""
import json
import os
import time

from . import config

FILE = "waiting-on-owner.json"
menus = None     # the menus shown (Menus.open: token -> {"channel", "since", …}); bot/menus.py names it here
written = None   # (path, list) as last written: the same again is not written again
on = False       # the running bot's (start()); until then note() writes nothing
failing = False  # the last write did not work: said once when it begins, once when it is over


def listed():
    """What waits for the owner now: every menu shown, every question and plan still open (not the ones that are
    closed and only owed to their machine), oldest first."""
    from . import ask
    out = [{"channel": str(m.get("channel")), "kind": "menu", "since": m.get("since")} for m in list((menus or {}).values())]
    out += [{"channel": str(s.get("channel")), "kind": "plan" if s.get("kind") == "plan" else "question", "since": s.get("ts")}
            for _, s in ask.waiting()]
    return sorted(out, key=lambda w: (w["since"] if isinstance(w["since"], (int, float)) else 0, w["channel"], w["kind"]))


def note():
    """Write the file as things are now. Called by everything that opens or closes a menu or a question (Shown;
    ask.save() and ask.forget()) and when the bot starts and ends. Never raises."""
    global written, failing
    if not on:
        return
    try:
        path = os.path.join(config.STATE, FILE)
        now = listed()
        if written == (path, now) and os.path.exists(path):
            return
        os.makedirs(config.STATE, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "waiting": now}, f)
        os.replace(path + ".tmp", path)
        written = (path, now)
        if failing:
            failing = False
            print("waiting: state/%s is written again" % FILE, flush=True)
    except Exception as e:
        written = None
        if not failing:  # said when it begins, not for every menu and question while it lasts
            failing = True
            print("waiting: could not write state/%s (%s: %s); the menus and questions themselves go on, and this is "
                  "not said again until it is written again" % (FILE, type(e).__name__, e), flush=True)


def start():
    """The bot runs: from here on the file follows what is open, beginning with what is open now (the questions
    kept on disk; no menu of before the restart, their buttons are dead). Whatever the file said before is gone."""
    global on, written
    on, written = True, None
    note()


def stop():
    """The bot ends in the ordinary way: nothing waits on a bot that is not there to take the answer (its buttons
    are dead; a question still on disk is listed again when it starts). The empty list, and nothing written after."""
    global on, written
    if not on:
        return
    try:
        path = os.path.join(config.STATE, FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"at": time.time(), "waiting": []}, f)
        os.replace(path + ".tmp", path)
    except Exception as e:
        print("waiting: could not write state/%s as the bot ends (%s: %s)" % (FILE, type(e).__name__, e), flush=True)
    on, written = False, None


class Shown(dict):
    """The menus shown in channels (Menus.open). Every way one comes or goes writes the file: whoever opens or
    closes a menu, now or in code to come, does it through this dict."""

    def __setitem__(self, token, menu):
        if isinstance(menu, dict):
            menu.setdefault("since", time.time())
        super().__setitem__(token, menu)
        note()

    def __delitem__(self, token):
        super().__delitem__(token)
        note()

    def pop(self, *a):
        try:
            return super().pop(*a)
        finally:
            note()

    def popitem(self):
        try:
            return super().popitem()
        finally:
            note()

    def clear(self):
        super().clear()
        note()

    def update(self, *a, **kw):
        for token, menu in dict(*a, **kw).items():
            self[token] = menu

    def setdefault(self, token, menu=None):
        if token not in self:
            self[token] = menu
        return self[token]
