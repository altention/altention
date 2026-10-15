What Claude Code does around the end of a turn, measured by #code-review-2 on 2026-10-13 (a real Claude Code 2.1.296
and 2.1.288, a stand-in API): one file a version, a list of cases run one after another in one conversation. Of each
case: every hook event as it came, with when its hook process began (`at`, seconds from the case's first hook) and
for how long it ran (`ran`); every transcript row, with when it was first seen in the file (looked at every 2 ms).
Changed: paths read /w/...; texts over 300 characters are cut to 300 (in an event and in its row alike); of a row
only what a reader of rows looks at is kept (type, promptId, the message's role, content, stop_reason, usage, model,
and the like). tests/test_ends.py plays them by their own clock. Made with a script from the reviewer's raw run
(its events, its transcript and its log of when each row appeared); the facts read off them:
- the reply's row is written 0.085 to 0.11 s after the Stop hook begins, however long the hook runs;
- each block of a reply is a row of its own, all written at once, each with the reply's usage and stop_reason;
- last_assistant_message is the reply's last text block, its ends trimmed;
- a reply with no block, or one empty text block: no last_assistant_message on the Stop, and no reply row to wait for;
- cut by the output limit or refused: Claude Code asks again by itself, no Stop between;
- a failed request (StopFailure) has its row too (isApiErrorMessage), as late;
- a message typed while a turn runs: its UserPromptSubmit comes at once with the running turn's id; the turn's reply
  is written before the message's own row.
