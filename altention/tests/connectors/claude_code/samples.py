"""The real screens kept in screens/<version>/ beside this file (whole captures of Claude Code, see the README there)."""
import json
import os

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screens")
VERSION = "2.1.296"
VERSIONS = ("2.1.296", "2.1.288")  # every version whose screens are kept: the same table holds for each
SETS = ("colour-120x50", "colour-80x24", "nocolor-120x50", "nocolor-80x24")
STATES = ("trust-dialog", "bypass-dialog", "idle-fresh-auto", "idle-fresh", "idle-typed", "busy", "busy-queued",
          "busy-queued-typed", "idle-after-turns", "permission", "permission-refused", "slash-typed", "model-panel",
          "btw-answering", "btw-answer", "btw-closed", "btw-answer-second", "btw-answering-busy", "btw-answer-busy",
          "btw-answer-cleared", "idle-bypass", "idle-suggestion")


def path(state, which="colour-120x50", kind="txt", version=VERSION):
    return os.path.join(HERE, version, which, "%s.%s" % (state, kind))


def screen(state, which="colour-120x50", esc=False, version=VERSION):
    """The screen as tmux capture-pane -p gave it (`esc`: -p -e, with its colours)."""
    with open(path(state, which, "esc.txt" if esc else "txt", version), encoding="utf-8") as f:
        return f.read()


def meta(state, which="colour-120x50", version=VERSION):
    with open(path(state, which, "meta.json", version), encoding="utf-8") as f:
        return json.load(f)


EXTRA = ("A", "B", "C", "D", "E", "L", "W")  # screens/<version>/extra/: more states, see the README there


def extra_states(group, version=VERSION):
    folder = os.path.join(HERE, version, "extra", group)
    return sorted(n[:-len(".meta.json")] for n in os.listdir(folder) if n.endswith(".meta.json"))


def extra(group, state, esc=False, version=VERSION):
    with open(os.path.join(HERE, version, "extra", group, "%s.%s" % (state, "esc.txt" if esc else "txt")), encoding="utf-8") as f:
        return f.read()


def extra_meta(group, state, version=VERSION):
    with open(os.path.join(HERE, version, "extra", group, state + ".meta.json"), encoding="utf-8") as f:
        return json.load(f)


AGENT_SETS = ("colour-120x50", "colour-80x24")  # screens/<version>/agents/: around Claude Code's agents list, see the README there
AGENT_STATES = ("a-idle-before-left", "list-one-row", "list-typed", "list-four-rows", "opened-from-list", "opened-after-turn",
                "list-after-exit", "returned-a-idle", "returned-a-busy", "list-a-working", "returned-a-busy-again", "attach-view",
                "attach-view-other", "attach-view-busy", "busy-before-left", "left-while-busy-waiting", "left-while-busy-moved",
                "left-while-busy-returned")
NARROW_SETS = ("colour-60x30", "colour-70x30", "colour-75x30", "colour-90x30")  # 2.1.296 only, the same states: how
# the status line is cut at other widths (#issues 34, recorded 2026-10-14 by #code-review-2 for 3b 2.7f)


def agents(state, which="colour-120x50", esc=False, version=VERSION):
    with open(os.path.join(HERE, version, "agents", which, "%s.%s" % (state, "esc.txt" if esc else "txt")), encoding="utf-8") as f:
        return f.read()


def agents_meta(state, which="colour-120x50", version=VERSION):
    with open(os.path.join(HERE, version, "agents", which, state + ".meta.json"), encoding="utf-8") as f:
        return json.load(f)
