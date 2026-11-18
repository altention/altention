A real Claude Code turn (2.1.319, `claude -p "say hi"` against cc_drill's fake API in a scratch HOME, 2026-11-08):
`real-turn.jsonl` is its transcript (rows of the API's request and the environment attachments left out, paths
replaced by /scratch), `real-turn-stop.json` what its Stop hook was handed. C-163 c4: the footer's turn id comes from
the hook's prompt_id, the replay compares it with the transcript's promptId; this pins that they are one value.
