Real screens of Claude Code 2.1.302, captured 2026-10-18 by #bot-dev (scratch HOME, a stand-in API on 127.0.0.1, no client
attached to the tmux pane, theme dark, TERM=tmux-256color). Script: #bot-dev's rig (scratchpad pc/cap.sh), not in this repository.

P: a permission question with an automatic denial. In auto mode (2.1.302's default; bypass mode too), a call the
dangerous-removal check holds (here the model's Bash `rm -rf ~`) is asked about with a deadline of two minutes: a warning
row above "Do you want to proceed?" counts down every second ("⚠ Claude Code will automatically deny this request in 1:58,
to avoid blocking progress on an unattended session"). At 80 columns the row wraps onto two. At 0 the dialog goes, the
call is denied and the turn goes on.

  countdown-<set>-a        the dialog about a second after it came up
  countdown-<set>-b        the same dialog about two seconds later (only the countdown row differs)
  auto-denied-nocolor-80x24  the same pane after the countdown reached 0 (input box back)

Sets: colour-120x50 and colour-80x24 (FORCE_COLOR=3), nocolor-120x50 and nocolor-80x24 (NO_COLOR=1). Per state three
files, as in the other folders: .txt (capture-pane -p), .esc.txt (capture-pane -p -e), .meta.json.

The only change to what was captured: in the path the dangerous-removal reason shows, the account's uid and name were
replaced by made-up ones of the same length (claude-1000001, someone123), so no row is longer or wraps elsewhere.
