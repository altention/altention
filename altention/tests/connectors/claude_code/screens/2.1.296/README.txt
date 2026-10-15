Real screens of Claude Code 2.1.296, whole, captured 2026-10-11 by #code-review-2 (scratch HOME, a stand-in API on 127.0.0.1,
no client attached to the tmux pane, theme dark, TERM=tmux-256color). Script: #code-review-2's rig (ncrig/samples.sh), not in this repository.

Four sets, the same 22 states in each:
  colour-120x50/   NO_COLOR= FORCE_COLOR=3, pane 120x50 (the size of the bot's own sessions)
  colour-80x24/    the same, pane 80x24
  nocolor-120x50/  NO_COLOR=1
  nocolor-80x24/   NO_COLOR=1

Per state three files:  <state>.txt (tmux capture-pane -p)   <state>.esc.txt (capture-pane -p -e)   <state>.meta.json
(version, pane size, cursor position and whether shown, arguments of claude, how the state was reached).

States, in the order captured:
  trust-dialog           started in a folder never trusted  [claude, no arguments]
  bypass-dialog          first start with --permission-mode bypassPermissions (never accepted)  [claude --permission-mode bypassPermissions]
  idle-fresh-auto        just started with no arguments (2.1.296: auto mode is the default permission mode), nothing typed  [claude, no arguments]
  idle-fresh             just started, nothing typed  [claude --permission-mode default]
  idle-typed             idle, text typed in the box, not sent  [claude --permission-mode default]
  busy                   a turn running (the model has not answered yet), empty box  [claude --permission-mode default]
  busy-queued            a turn running, one message waiting in the queue  [claude --permission-mode default]
  busy-queued-typed      a turn running, one message queued, text typed in the box  [claude --permission-mode default]
  idle-after-turns       idle after two turns  [claude --permission-mode default]
  permission             the model asked to run Bash(touch sample-file); default permission mode  [claude --permission-mode default]
  permission-refused     the permission question refused with Escape  [claude --permission-mode default]
  slash-typed            '/model' typed, the menu of commands open  [claude --permission-mode default]
  model-panel            /model sent: the panel  [claude --permission-mode default]
  btw-answering          /btw sent, the answer not there yet  [claude --permission-mode default]
  btw-answer             /btw answered  [claude --permission-mode default]
  btw-closed             the /btw answer closed with Escape  [claude --permission-mode default]
  btw-answer-second      a second /btw answered (after the first)  [claude --permission-mode default]
  btw-answering-busy     /btw sent while a turn runs, the answer not there yet  [claude --permission-mode default]
  btw-answer-busy        /btw answered while a turn runs  [claude --permission-mode default]
  btw-answer-cleared     x pressed in the answer (clear history)  [claude --permission-mode default]
  idle-bypass            just started with --permission-mode bypassPermissions (as the bot's own sessions), the warning answered Yes  [claude --permission-mode bypassPermissions]
  idle-suggestion        idle after two turns, a prompt suggestion in the box (CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=1)  [claude --permission-mode bypassPermissions]

Notes:
- 2.1.296 starts in auto mode when no --permission-mode is given; the permission question needs --permission-mode default.
- The trust dialog shows the scratch folder's path (long: it wraps).
- The status line of the bot's own sessions is the one in idle-bypass / idle-suggestion (bypass permissions on).
- The model's replies are the stand-in's fixed texts; spinner words and times differ from capture to capture.
- Kept in the repository on 2026-10-11 (the capture lay under /tmp). The only change to what was captured: in the
  path the trust dialog and the permission question show, the account's uid and the name of its home were replaced by
  made-up ones of the same length (claude-1000001, srv-abc-home-someone123), so no row is longer or wraps elsewhere; and the scratch
  folder's "home" in that path reads "base" wherever it stood whole on one row (in four files the row breaks inside the word, and it was left) (the repository's scan for personal paths takes /home/<name> for one).
- Used by the tests of the functions that read the screen (tests/test_screen_samples.py and those that follow).
