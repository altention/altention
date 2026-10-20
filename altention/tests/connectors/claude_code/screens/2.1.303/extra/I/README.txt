Real screens of Claude Code 2.1.303, captured 2026-10-20 by #bot-dev (scratch HOME, a stand-in API on 127.0.0.1, no client
attached to the tmux pane, theme dark, TERM=tmux-256color, claude --permission-mode default). Script: #bot-dev's rig
(scratchpad pc/impcap.sh), not in this repository.

I: the folder's CLAUDE.md imports a file outside it (one line, `@~/outside/room.md`). Before the conversation starts
Claude Code asks "Allow external CLAUDE.md file imports?" with two answers and no numbers (a number key does nothing;
the arrows and Enter pick). Answered once per folder (~/.claude.json, the project's hasClaudeMdExternalIncludesApproved).

  imports-<set>              the dialog as it opens, "No, disable external imports" picked
  imports-yes-picked-<set>   after one Down: "Yes, allow external imports" picked, not answered
  imports-answered-<set>     after Enter on Yes: the conversation's input box

Sets: colour-120x50 and colour-80x24 (FORCE_COLOR=3), nocolor-120x50 and nocolor-80x24 (NO_COLOR=1). Per state three
files, as in the other folders: .txt (capture-pane -p), .esc.txt (capture-pane -p -e), .meta.json.

The only change to what was captured: in the path the dialog shows, the account's uid and name were replaced by made-up
ones of the same length (claude-1000001, someone123), and the scratch folder's "home" before /outside/ reads "base"
(the repository's scan for personal paths takes /home/<name> for one), so no row is longer or wraps elsewhere.
