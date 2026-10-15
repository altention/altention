More real screens of Claude Code 2.1.296, captured 2026-10-11 by #code-review-2 while rehearsing 3fa39a6 + 818ce83
(scratch HOME, a stand-in API, no client attached, colour: NO_COLOR= FORCE_COLOR=3). Same three files per state as
the 88 beside this folder (<state>.txt, <state>.esc.txt, <state>.meta.json: its "how" says what the screen is).
Script: #code-review-2's rig (ncrig/z.sh), not in this repository.
  A/  bypass mode 120x50: the /btw box 2, 12, 22, 32 s into waiting (idle) and 3, 13, 23 s (a turn running); answered:
      idle, a turn running, a turn running with a message queued, a background command running
  C/  panes 70, 60, 50, 40 columns x 30: first answer, second answer (the hint wraps), a third not answered yet
  D/  panes 120x24 and 80x24: a question of 545 characters and an answer of 40 paragraphs
  E/  bypass mode, panes 120x24 and 70x24: an answer of 40 paragraphs / 40 list items / a code block of 40 lines,
      scrolled to the top, 7 rows down, the bottom
  L/  the trust dialog and the bypass warning in panes 120x50, 120x12, 100x24, 70x24, 60x24, 50x24, 40x24
  W/  one session resized 80 → 36 columns (x30): the second /btw not answered yet, the third answered
      (resized panes, not started at that width: C/ has panes started narrow)
The trust dialogs of L/ show the scratch folder's path (D/ shows only ~/proj).

Kept in the repository on 2026-10-11. The only change to what was captured, as in the folder above: in the scratch
folder's path (the trust dialogs of L/) the account's uid and the name of its home were replaced by made-up ones of
the same length, and that folder's "home" reads "base", also where a narrow pane breaks the path inside them; no row
is longer or breaks elsewhere. Read by tests/samples.py (extra()), pinned in tests/test_screen_samples.py.

B/ (added 2026-10-12, captured by #code-review-2 for stage 3b): the /btw answer box when its request fails. 500 and 429
are tried again ("· API error · retrying in 2s · attempt 3/10", "✶ Rate limited · retrying in 18s · attempt 6/10": the
box has not answered, its hint offers no "c to copy"); 529 again and again, and 400, end in Claude Code's own last word
("(API error: …)", with "c to copy"). 2, 8, 20 and 45 seconds after sending. Their meta.json was written when they
were kept (pane size from the capture itself); nothing in them shows the scratch path.
