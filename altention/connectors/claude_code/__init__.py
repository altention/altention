"""The Claude Code connector (stage 3b, begun 2026-10-12). Its interface is internal for now and may change:
a second tool has not been connected yet (DESIGN 5.1, section 11).

Rules every module here keeps (altention/tests/connectors/claude_code/test_rules.py reads the imports of each file):
- standard library only, Python 3.8: tools/dcremote runs it on other machines with the system Python;
- nothing of the Discord bot is imported (no `bot`, no discord), and nothing here reads or writes DCBOT_* or a path
  of the bot's state: whoever calls passes what is needed;
- no sentence for the owner: a caller gets facts, codes and data, and the bot has the words. The English words Claude
  Code itself draws ("Esc to close") are facts it has to know, not sentences of ours.

What here is the wording or the layout of one version of Claude Code, to be held against a new one (the samples
and recorded runs in altention/tests/connectors/claude_code/ say which versions were looked at: 2.1.288, 2.1.296):
- the screen: screen.py and panels.py (the words Claude Code draws; samples in screens/<version>/);
- the hooks' payloads: hooks.py (hook_payloads/<version>/), and the order of hook and row (turns/<version>/);
- a workflow agent's first rows: transcript.HARNESS and FOLLOWS ("[Workflow harness — user request] …", then
  "[Workflow harness — computed task] … The computed task text follows:"), seen on 2.1.296 only (turns/2.1.296/w3-F).
  With another wording of the task row an agent's task shows as its first row says; whether the agent has been
  told is judged by the rows there are, not by their wording (transcript.asked_yet). Should the first row's own
  beginning change, a workflow's agent is no longer told from a plain one: its task shows as that row says."""
NAME = "claude_code"
