# Changelog

Every release has a `## vX.Y.Z — date` heading, newest first. The release tool publishes the text under a version's
heading as that version's notes.

Each version has two sections:

- **For people**: what changed, for you to read.
- **For agents**: what an agent updating an installation to this version needs to know, in plain words (settings to
  move, a step to run, something to check afterwards), or `None`. A planned `/update` command (not in this version
  yet) will read it, tell you what it says before doing anything, and never carry it out on its own.

## v0.1.0 — 2026-10-15

### For people

First public release.

- Discord bot for Claude Code: one long-lived conversation per channel, running in tmux on your own machine; attach
  a conversation you started yourself with `/attach`; questions, plans and permission prompts as buttons; subagents and
  workflows as threads; terminal channels and a file browser; channels that post to and talk with each other
  (`altention dc post|read|notify`); the bot changes and redeploys itself, with automatic rollback.
- Install with one script and a guided `/install` skill; `doctor` checks every machine and says how to fix each
  problem.
- Bundled plugins, each optional: chatlog (conversation logs), progress (project progress), usage, status, planner
  (reminders), council (a decision skill).
- Your settings and data live in a private instance repository, apart from the engine; see `examples/instance/`.
- Security: the bot obeys one owner; the token stays in a mode-600 file that Claude Code is kept from reading; the
  engine does not update itself unless you set `core.update`. See `SECURITY.md`.
- Tested with Claude Code 2.1.288 and 2.1.296 (2.1.299: screens checked, in daily use). Several machines over SSH
  work but are marked advanced: not yet verified from a clean install. See the README's known limitations and roadmap.

### For agents

None.
