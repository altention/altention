Real screens of Claude Code 2.1.316's /btw answer box, captured 2026-11-03, colour, theme dark. Two ways of drawing it on
the same version:

inline-*: one installation (a test account's own bot session, engine v0.5.0, tmux 3.2a, TERM=screen, the real model)
draws it with no row of "▔": the question sent stands in the conversation as the owner's message ("❯ /btw …"), then
an empty row, the questions asked so far at an indent of 2, an empty row, the answer at 4, an empty row, and the hint at
2 as the last of the screen. The bot (v0.5.0) did not see it as the box: "/btw" answers were never posted back and
the box was left open. Captured by #discord-control on that machine (tmux -L dcbot capture-pane -p, and -p -e):
  inline-answering   the bot's second /btw, the answer on its way ("✻ Answering…"; the first one had not been cleared)
  inline-answer      the same box, answered
Neither TERM=screen, tmux 3.2a (Ubuntu 22.04), LANG=C, CLAUDE_CODE_NO_FLICKER, a full screen nor auto mode brought this
drawing about in a scratch HOME on the same binary: what decides it is not known (most likely a setting of the
account's that the program fetches).

edge-*: the box as every other installation tried draws it (this machine's rig: scratch HOME, a stand-in API on
127.0.0.1, tmux 3.4, TERM=tmux-256color; a real-model pane on another machine drew the same), a row of "▔" over it:
  edge-answer-first          the first /btw, answered
  edge-answer-second         a second /btw after the first was closed with Escape (it offers "x to clear history")
  edge-answer-second-60x24   the same in 60 columns: the hint wraps onto two rows
  edge-answer-tall           an answer of 60 list rows, scrolling inside the box
  edge-answer-busy           answered while a turn of the main conversation runs (auto mode)

The only change to what was captured: in the inline screens the account's name was replaced by a made-up one of the same
length (user-x), "/home" by "/hdir", and Discord IDs by made-up words of the same length (id-0000000000000021…), so no
row is longer or wraps elsewhere.
