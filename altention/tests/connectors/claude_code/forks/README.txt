Recorded runs of the real Claude Code in which a conversation is moved to a background process (the left key on an
empty input box: Claude Code's agents list), to be played again (../test_forks.py). Stage 3b step 2.4d.

Each <version>/<name>.json is one run in a scratch HOME against a stand-in API, recorded by #code-review-2 (its bgm
rig: a hook that logged every event as Claude Code handed it over with the time it was called, a watcher that logged
every row of both transcripts as it was written). "old" is the conversation's id in its pane, "new" the id it goes on
under. "steps", in the order it happened, t in seconds since the epoch (the rows' own "timestamp" fields are the same
clock, which is what the reading goes by):
  {"t": .., "hook": {...}}                  a hook was called with this input (transcript_path "{old}" or "{new}")
  {"t": .., "write": {...}, "into": "old"}  this row was written to that transcript ("old" or "new")
Events of the run's other background processes (the spare one, conversations typed into the list) are left out.
Rows that are not something said (attachments, snapshots, modes) are cut down to type, uuid, timestamp, and a few
bulky fields of the others are dropped (the converter lists them); the rig's folder reads /w. Nothing else is changed.

What the runs show (and the tests hold):
- the new transcript does not exist when the new process's SessionStart (source "fork") is called. Moved while idle,
  it is written 0.1 to 0.2 s after the first UserPromptSubmit that follows; moved in mid-turn, 0.13 s after the
  SessionStart;
- it begins with a copy of the old conversation: the same rows (uuid, timestamp, turnPosition as they were), each
  with the new sessionId; the copied message rows bear the promptId of the turn that is current when the file is
  written (moved while idle: the first turn after the move) or none (moved in mid-turn); of a turn moved while
  it ran, what it had said and its tool call are not copied;
- the Stop of a turn moved while it ran has no prompt_id.

  idle-1   one turn, then the left key on an idle conversation, Escape (back in it), a turn with words and a tool, a turn
  idle-3   three turns before the move (the first with words and a tool), then the same
  busy-1   one turn, a turn with words and a long tool, the left key while the tool runs; the turn ends; a turn
  busy-3   two turns (the first with words and a tool), then the same
  resumed-plain     a conversation of three turns whose rows give no turn number (a 2.1.296 file with turnPosition taken off
                    every row, standing for one begun before 2.1.284: no older claude was at hand; the rows' version field
                    is as it was), resumed: a turn with words and a tool, the left key while idle, Escape, two turns. Nothing
                    Claude Code writes from then on gives a turn number either, in the old file or the new
  resumed-numbered  the same from the file as it was (numbers kept: the new rows go on counting)
(-1: 2026-10-13; -3: 2026-10-14, after a run with several turns before the move showed them posted again;
resumed-*: 2026-10-14, after #code-review found that rows without turn numbers were not told from the turn's own.)
