Recorded runs of the real Claude Code, to be played again (../replay.py, ../test_turns.py).

Each <version>/<name>.json is one run in a scratch HOME against a stand-in API (so the model's words are fixed
strings like REPLY-ALPHA.), recorded by #code-review-2 on 2026-10-12 with a hook that logged every event as Claude
Code handed it over and a watcher that logged every row of the transcript as it was written. "steps", in the order
it happened, t in seconds from the first:
  {"t": .., "mark": "..."}                 what the rig did then (typed a message, pressed Escape)
  {"t": .., "hook": {...}, "hook_done": s}  a hook was called with this input (hook_done: how long the hook ran)
  {"t": .., "write": {...}}                 this row was written to the transcript

What the runs show (and the tests hold): a hook is called BEFORE the row it is about is written; a message sent
while a turn runs fires UserPromptSubmit at once with the running turn's prompt_id; a turn interrupted with Escape
ends with no event; a subagent's events carry the prompt_id of whatever turn the main conversation is in then.

  2.1.296/q1-A  idle and queued messages, a text block before a tool call, /compact by hand, a subagent whose end
                makes Claude Code prompt itself 0.06 s after a Stop, an API failure at once, an overloaded API, /exit
  2.1.296/m1-M  a background subagent that outlives its turn; three tool calls in one reply (their rows written
                together, 1.5 s after the first call's hook); Escape before any reply; Escape while a tool runs
  2.1.296/m2-C  automatic compaction (and a PreCompact that nothing followed)
  2.1.288/m3-M, 2.1.288/m4-C  the same two scripts on 2.1.288

  2.1.296/w3-F  a workflow of two agents (one line each); the main turn ends while it runs and Claude Code prompts
                itself when it is done. Not from the rig (a stand-in API cannot make a workflow): a real session
                beside the bot and the real API, recorded by a watcher that polled the bot's spool and the session's
                files every 10 ms; what was said is replaced by fixed words (convert_recording.py). Its steps also
                write the agents' own files: {"file": "subagents/workflows/<run>/agent-<id>.jsonl", "write": row}
                and whole JSON files {"file": …, "put": {…}} (the agents' meta.json; the workflow's own record).

  2.1.296/w4-R  the same on another machine (a test channel's Claude there), recorded on that machine: the events
                are as tools/dchook spooled them, before the relay added anything. Two helper agents of Claude
                Code's own end in between (SubagentStop with an empty agent_type and no start).

convert.py made the other files from the rig's output (hq-out/<name>: tl.jsonl, hk/log.jsonl, hk/raw/, marks.txt and
the transcript). It keeps of a row what tells turns and readings apart (type, promptId, isSidechain, uuids, the
message's role, texts, tool call and result ids, a system row's subtype, an attachment's kind) and drops the rest
(the attachments' bodies were nine tenths of the bytes); the rig's own paths become {transcript}, {transcript_dir}
and {cwd}. So the bytes of a row here are not the bytes Claude Code wrote: positions in a replayed file are the
replay's own. For a new version of Claude Code: record the same scripts, convert, add the folder to replay.VERSIONS.
