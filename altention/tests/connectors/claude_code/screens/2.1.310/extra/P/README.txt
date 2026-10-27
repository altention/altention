Real screens of Claude Code 2.1.310, captured 2026-10-27 by #bot-dev (scratch HOME, a stand-in API on 127.0.0.1, no client
attached to the tmux pane, theme dark, TERM=tmux-256color, colour). Script: #bot-dev's rig (scratchpad pc/cd-deny.sh), not in
this repository.

P: the auto-deny countdown of 2.1.302's permission question, in a narrow pane (52x28, the size #activity-dev's pane had on
2026-10-27 when its buttons never went out). The command (Bash `cd sub && rm -f ../shots-zoom/*; timeout 1000 bash
../zoomtry.sh`) is held by the dangerous-removal check; its reason shows in a box of rows beginning with "│". The warning
row wraps onto three rows, after "request": "⚠ Claude Code will automatically deny this request" / "in 1:58, to avoid
blocking progress on an" / "unattended session". Below the options: "Esc to cancel · Tab to amend".

  countdown-colour-52x28-a   the dialog about a second after it came up
  countdown-colour-52x28-b   the same dialog about two seconds later (the countdown row and the spinner differ)
  auto-denied-colour-52x28   the same pane after the countdown reached 0 (input box back; plain text only)

The only change to what was captured: in the path the reason shows, the account's uid and name were replaced by made-up
ones of the same length (claude-1000001, someone123), and the rig's folder "home" in it by "hdir", so no row is
longer or wraps elsewhere.
