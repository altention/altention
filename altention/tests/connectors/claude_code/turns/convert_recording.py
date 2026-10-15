"""Make a replayable run (see README.txt) of a recording of a real session taken beside the bot: the output of a
watcher that polled the bot's spool (every hook event as tools/dchook spooled it) and the session's files (every row
of the transcript and of the files under <session>/, with the time it was first seen). That is how the run with a
workflow was taken (w3-F, 2026-10-12: a stand-in API cannot make a workflow): rows of the subagents' own files are
steps with "file" (a path under the session's folder), whole JSON files are {"put": …, "file": …}.

Kept of a row: as convert.py keeps. What was said is replaced by fixed words (the conversation was a real one): the
owner's request, the harness's relay of it to each agent, tool results, and Claude's own sentences other than the
marks the run was asked for (WF-A, WF-B, WORKFLOW done). Dropped from an event: what tools/dchook adds or the bot
alone uses (channel, ts, dc_pane, scratchpad_dir), paths become {transcript}, {transcript_dir} and {cwd}.
usage: convert_recording.py <recording.jsonl> <name> <out.json> [where it was recorded]"""
import json
import os
import re
import sys

src, name, out = sys.argv[1:4]
REQUEST = "use a workflow: two agents, each replies with one line (WF-A, WF-B), no files, no other tools"
KEEP_TEXT = ("WF-A", "WF-B", "WORKFLOW done", "WORKFLOW-2 done")
PATH = re.compile(r"(/mnt|/home|/Users|/tmp)/[^\s\"']*")


def said(text):
    """A sentence of Claude's: the marks stay, anything else becomes one fixed sentence."""
    text = (text or "").strip()
    return text if text in KEEP_TEXT else ("WORKFLOW STARTED." if text else text)


def asked(text):
    """What was said to Claude or to an agent."""
    if text.startswith("[Workflow harness — user request]"):
        return text.split("\n", 1)[0] + "\n  " + REQUEST
    if text.startswith("[Workflow harness — computed task]"):
        return PATH.sub("{path}", text)
    if text.startswith("<task-notification>"):
        return "<task-notification>\n<status>completed</status>\n</task-notification>"
    return REQUEST


def block(b):
    if not isinstance(b, dict):
        return b
    kind = b.get("type")
    if kind == "text":
        return {"type": "text", "text": said(b.get("text"))}
    if kind == "tool_use":
        return {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": {"description": "rec"}}
    if kind == "tool_result":
        return dict({"type": "tool_result", "tool_use_id": b.get("tool_use_id"), "content": "ok"}, **({"is_error": True} if b.get("is_error") else {}))
    return {"type": kind}


def slim(row):
    keep = {k: row[k] for k in ("type", "promptId", "isSidechain", "uuid", "parentUuid", "subtype", "isMeta", "agentId") if k in row}
    msg = row.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        m = {"role": msg.get("role"), "content": asked(content) if isinstance(content, str) else [block(b) for b in content or []]}
        for k in ("model", "stop_reason"):
            if msg.get(k) is not None:
                m[k] = msg[k]
        if isinstance(msg.get("usage"), dict):
            m["usage"] = {k: v for k, v in msg["usage"].items() if k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")}
        keep["message"] = m
    if row.get("type") == "attachment" and isinstance(row.get("attachment"), dict):
        keep["attachment"] = {"type": row["attachment"].get("type")}
    return keep


def event(ev):
    ev = {k: v for k, v in ev.items() if k not in ("channel", "ts", "dc_pane", "scratchpad_dir")}
    for k in list(ev):
        if k == "transcript_path":
            ev[k] = "{transcript}"
        elif k == "agent_transcript_path":
            ev[k] = "{transcript_dir}/" + ev[k].split(session + "/", 1)[1]
        elif k == "cwd":
            ev[k] = "{cwd}"
        elif k == "prompt":
            ev[k] = asked(ev[k])
        elif k == "last_assistant_message":
            ev[k] = said(ev[k])
        elif k in ("tool_input", "tool_response", "background_tasks", "message"):
            ev[k] = [] if isinstance(ev[k], list) else ({"description": "rec"} if isinstance(ev[k], dict) else "rec")
        elif k == "started" and isinstance(ev[k], dict):
            ev[k] = {x: ev[k][x] for x in ("runId", "workflowName", "summary", "taskId") if x in ev[k]}
        elif k == "hint":  # the bot's one-line summary of a tool call: a workflow's name stays, a command's is the owner's
            ev[k] = str(ev[k])[:80] if ev.get("tool_name") == "Workflow" else "rec"
    return ev


steps, version, session, t0 = [], None, None, None
with open(src, encoding="utf-8") as f:
    recs = [json.loads(line) for line in f if line.startswith("{")]  # (the watcher's own last line is no record)
for r in recs:
    if "hook" in r:
        session = session or r["hook"].get("session_id")
for r in recs:
    t0 = t0 if t0 is not None else r["t"]
    t = round(r["t"] - t0, 3)
    if "hook" in r:
        steps.append({"t": t, "hook": event(r["hook"])})
    elif "row" in r:
        rel = r["file"].split(session, 1)[1].lstrip("/") if session in r["file"] else r["file"]
        main = rel in ("", ".jsonl") or r["file"].endswith(session + ".jsonl")
        try:
            d = json.loads(r["row"])
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        version = version or d.get("version")
        if rel.endswith(".jsonl") or main:
            step = {"t": t, "write": slim(d) if d.get("type") in ("user", "assistant", "system", "attachment") or main else
                    {k: d[k] for k in ("type", "agentId", "label", "phase") if k in d}}
        else:  # a whole JSON file
            step = {"t": t, "put": {k: d[k] for k in ("agentType", "description", "workflowPhase", "spawnDepth", "runId", "taskId") if k in d}}
        if not main:
            step["file"] = rel
        steps.append(step)
where = sys.argv[4] if len(sys.argv) > 4 else "beside the bot"  # or "on another machine": where the watcher ran
doc = {"name": name, "claude_code": version, "recorded": "2026-10-12, %s, a real session and the real API (the words replaced)" % where,
       "steps": steps}
text = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).replace('},{"t":', '},\n{"t":')
for bad in ("/mnt/", "/home/", "/Users/", "/tmp/claude"):
    assert bad not in text, bad
with open(out, "w", encoding="utf-8") as f:
    f.write(text + "\n")
print(name, version, "steps", len(steps), "hooks", sum(1 for s in steps if "hook" in s), "bytes", len(text))
