"""Recorded runs of the real Claude Code played again (turns/<version>/<name>.json, see the README there): the
rows of the transcript appear in the order and at the moments they were written, the hook events come at the
moments their hooks were called. What handles the events is the caller's; when it gets to them is the point:
`timing` is "live" (each event handled when its hook is called: the rows it is about are mostly not written yet),
"late" (each handled just before the next hook is called: its rows are there, the next event's are not) or
"piled" (the first event when its hook is called, so the file is known; every other one after everything was
written: events that waited while the bot was away)."""
import json
import os

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "turns")
VERSIONS = ("2.1.296", "2.1.288")
TIMINGS = ("live", "late", "piled")  # of one who handles whatever has come; played with `awaits` they are of one
# who leaves an event that waits for its row (hooks.awaits) and looks again after every step of the run


def names(version):
    return sorted(n[:-len(".json")] for n in os.listdir(os.path.join(HERE, version)) if n.endswith(".json"))


def load(version, name):
    with open(os.path.join(HERE, version, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def row_text(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"


def play(run, path, handle, timing="live", cwd="/w", awaits=None):
    """Write the run's rows to `path` and hand its events to handle(event), in the given timing. Returns the events
    in the order they were handed over (each as handle left it).

    `awaits(event, now)`: asked before an event is handed over; True leaves it, and every event behind it, for the
    next look (after the next step of the run, at that step's time; each event carries the time its hook was
    called as "ts", as tools/dchook stamps it). At the end of the run everything left is handed over."""
    def event(step):
        ev = json.loads(json.dumps(step["hook"]).replace("{transcript_dir}", path[:-len(".jsonl")])
                        .replace("{transcript}", path).replace("{cwd}", cwd))
        return ev
    waiting, done = [], []
    folder = path[:-len(".jsonl")]  # the session's own folder: its subagents' files are steps with "file"

    def put(step):
        where = os.path.join(folder, step["file"]) if step.get("file") else path
        os.makedirs(os.path.dirname(where), exist_ok=True)
        if "put" in step:
            with open(where, "w", encoding="utf-8") as f:
                json.dump(step["put"], f)
        else:
            with open(where, "a", encoding="utf-8") as f:
                f.write(row_text(step["write"]))
    state = {"at": 0, "t": 0.0}

    def advance(secs):
        """Let the run go on for `secs` while an event is being handled (the handler sleeps: tools/dcremote waiting for
        a subagent's files): what is written in that time is written; hooks called in it wait their turn."""
        until = state["t"] + secs
        steps = run["steps"]
        while state["at"] < len(steps) and steps[state["at"]]["t"] <= until:
            step = steps[state["at"]]
            state["at"] += 1
            if "write" in step or "put" in step:
                put(step)
            elif "hook" in step:
                waiting.append(dict(event(step), ts=step["t"]))
        state["t"] = until
    play.advance = advance

    def flush(now=None):
        while waiting:
            if awaits is not None and now is not None and awaits(waiting[0], now):
                return
            ev = waiting.pop(0)
            handle(ev)
            done.append(ev)
    while state["at"] < len(run["steps"]):
        step = run["steps"][state["at"]]
        state["at"] += 1
        state["t"] = max(state["t"], step["t"])
        if "write" in step or "put" in step:
            put(step)
            if awaits is not None and timing == "live":
                flush(step["t"])  # another look: the row an event waited for may be this one
        elif "hook" in step:
            if timing == "late":
                flush(step["t"])  # the one before: its rows are written by now, this one's are not
            waiting.append(dict(event(step), ts=step["t"]))
            if timing == "live" or not done:
                flush(step["t"])
        elif awaits is not None and timing == "live":
            flush(step["t"])
    flush()
    return done
