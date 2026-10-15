"""Make a replayable recording of one of #code-review-2's runs (hq-out/<name>): the rows of the transcript in the
order and at the times they were written, and the hook events at the times the hooks were called, as one JSON file.
Kept of a row: what tells turns and readings apart (type, promptId, isSidechain, uuid, message role and content with
texts, tool_use ids/names, tool_result ids; subtype of system rows; the attachment's kind). Dropped: everything else
(attachments' bodies are most of the bytes). Paths of the rig are replaced by {transcript} and {cwd}.
usage: convert.py <hq-out/name> <claude code version> <out.json>"""
import glob, json, os, re, sys

src, version, out = sys.argv[1:4]
name = os.path.basename(src.rstrip("/"))
transcripts = [p for p in glob.glob(os.path.join(src, "*.jsonl")) if os.path.basename(p) != "tl.jsonl"]
assert len(transcripts) == 1, transcripts
with open(transcripts[0], "rb") as f:
    data = f.read()
RIG = re.compile(r"/tmp/claude-\d+/[^\s\"']*")


def clean(text):
    return RIG.sub("{rig}", text) if isinstance(text, str) else text


def slim_block(b):
    if not isinstance(b, dict):
        return b
    kind = b.get("type")
    if kind == "text":
        return {"type": "text", "text": clean(b.get("text"))}
    if kind == "tool_use":
        return {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": {"description": "rig"}}
    if kind == "tool_result":
        c = b.get("content")
        if isinstance(c, list):
            c = [{"type": "text", "text": clean(x.get("text", ""))[:200]} for x in c if isinstance(x, dict) and x.get("type") == "text"]
        return dict({"type": "tool_result", "tool_use_id": b.get("tool_use_id"), "content": clean(c)[:200] if isinstance(c, str) else c},
                    **({"is_error": True} if b.get("is_error") else {}))
    return {"type": kind}


def slim(row):
    keep = {k: row[k] for k in ("type", "promptId", "isSidechain", "uuid", "parentUuid", "subtype", "isMeta", "isCompactSummary",
                               "operation", "sessionId") if k in row}
    msg = row.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        m = {"role": msg.get("role"), "content": clean(content) if isinstance(content, str) else [slim_block(b) for b in content or []]}
        for k in ("model", "stop_reason", "id"):
            if msg.get(k) is not None:
                m[k] = msg[k]
        if isinstance(msg.get("usage"), dict):
            m["usage"] = {k: v for k, v in msg["usage"].items() if k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")}
        keep["message"] = m
    if row.get("type") == "attachment" and isinstance(row.get("attachment"), dict):
        a = row["attachment"]
        keep["attachment"] = {k: clean(a[k]) for k in ("type", "commandMode", "prompt") if k in a and isinstance(a[k], (str, int))}
    return keep


steps = []
with open(os.path.join(src, "tl.jsonl")) as f:
    for line in f:
        d = json.loads(line)
        at = d["at_byte"]
        end = data.index(b"\n", at)
        row = json.loads(data[at:end].decode("utf-8"))
        steps.append((d["t"], 0, {"write": slim(row)}))
written = sum(1 for _ in steps)
assert written == data.count(b"\n"), (written, data.count(b"\n"))  # every row of the file is in the timeline
names = sorted(os.listdir(os.path.join(src, "hk", "raw")))
with open(os.path.join(src, "hk", "log.jsonl")) as f:
    log = [json.loads(line) for line in f]
assert len(log) == len(names), (len(log), len(names))
for entry, fname in zip(log, names):
    with open(os.path.join(src, "hk", "raw", fname)) as f:
        ev = json.load(f)
    assert ev.get("hook_event_name") == entry["hook"], (fname, entry["hook"])
    for k in list(ev):
        if k == "transcript_path":
            ev[k] = "{transcript}"
        elif k == "agent_transcript_path":
            ev[k] = "{transcript_dir}/subagents/" + os.path.basename(ev[k])
        elif k == "cwd":
            ev[k] = "{cwd}"
        elif k in ("tool_input", "tool_response"):
            ev[k] = json.loads(clean(json.dumps(ev[k]))) if len(json.dumps(ev[k])) < 600 else {"description": "rig"}
        elif isinstance(ev[k], str):
            ev[k] = clean(ev[k])[:2000]
    steps.append((entry["t"], 1, {"hook": ev, "hook_done": round(entry["t_done"] - entry["t"], 4)}))
marks = []
if os.path.exists(os.path.join(src, "marks.txt")):
    with open(os.path.join(src, "marks.txt")) as f:
        for line in f:
            t, _, text = line.strip().partition(" ")
            if text:
                steps.append((float(t), -1, {"mark": text}))
steps.sort(key=lambda s: (s[0], s[1]))
t0 = steps[0][0]
doc = {"name": name, "claude_code": version, "recorded": "2026-10-12, #code-review-2 (scratch HOME, a stand-in API)",
       "steps": [dict({"t": round(t - t0, 3)}, **what) for t, _, what in steps]}
text = json.dumps(doc, ensure_ascii=False, indent=None, separators=(",", ":")).replace('},{"t":', '},\n{"t":')
assert "/tmp/claude-" not in text and "/mnt/nfs" not in text, "a path of the rig is left"
with open(out, "w", encoding="utf-8") as f:
    f.write(text + "\n")
print(name, version, "rows", written, "hooks", len(names), "bytes", len(text))
