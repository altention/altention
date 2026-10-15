Real screens of Claude Code 2.1.296 and 2.1.288 around "the conversation moved to the background" (3b 2.4d, #issues 32),
captured 2026-10-14 by #code-review-2 for the manager (scratch HOME, a stand-in API on 127.0.0.1, no client attached to the
tmux pane, theme dark, TERM=tmux-256color, NO_COLOR= FORCE_COLOR=3). Script: ncrig/bgs.sh <world> <cols> <rows> <idle|busy>.
Claude Code was started from a shell in the pane (claude --permission-mode bypassPermissions), so the pane lives on.

<version>/colour-120x50/ and <version>/colour-80x24/, the same 18 states in each; per state three files as in the connector's
samples: <state>.txt (tmux capture-pane -p), <state>.esc.txt (capture-pane -p -e), <state>.meta.json (version, pane size,
cursor_xy, cursor_shown, alternate_screen, how the state was reached, footer_row = the last row with text, as it is).

Conversation A is the pane's own first conversation (one turn: "Q-A-one…"). B, C, D are started by typing a task in the list.

The idle pass, in the order captured:
  a-idle-before-left      A idle after one turn, nothing pressed yet (for comparison)
  list-one-row            Left with an empty box: the agents list, one row (A)                                   [asked: 1]
  list-typed              the list, text typed in its box, Enter not pressed                                     [asked: 2]
  list-four-rows          the list with four rows: A, and B, C, D                                                 [asked: 1]
  opened-from-list        Enter in the list: the row under the cursor is opened (it was D, the newest)            [asked: 5]
  opened-after-turn       a turn run in that opened conversation
  list-after-exit         /exit typed there: back in the list                                                     [asked: 6]
  returned-a-idle         Escape in the list: back in A, idle                                                     [asked: 4]
  returned-a-busy         a turn of A running (a tool of 25 s)                                                    [asked: 4]
  list-a-working          Left while A (already a background conversation) runs: the list, A under Working
  returned-a-busy-again   Escape in the list while A still runs: back in A, busy                                  [asked: 4]
  attach-view             `claude attach <id>` of one background conversation, in a second pane                   [asked: 7]
  attach-view-other       `claude attach <id>` of another one, in a third pane                                    [asked: 7]
  attach-view-busy        the attach view while its conversation runs a turn
The busy pass (a fresh start each time):
  busy-before-left        A (still the pane's own) running a turn with a tool of 16 s
  left-while-busy-waiting Left pressed meanwhile: not moved yet ("Backgrounding after the current tool finishes…")  [asked: 3]
  left-while-busy-moved   the tool finished: moved, the list is shown                                             [asked: 3]
  left-while-busy-returned Escape after that: back in A

Which conversation the attach views show differs between the runs (the ids are taken in the order the sessions folder lists
them): the first user line on the screen says which ("Q-A-one…", "Q-B-one…", …).

Nothing in the captures was changed. No capture shows the account's name or uid (checked with grep).
