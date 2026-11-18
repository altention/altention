Claude Code 2.1.310 waiting at a weekly usage limit (2026-10-26, a throwaway channel spending its quota; captured with
tmux capture-pane -p every 20 s). Cut to the rows from the limit's message down: the conversation above held nothing
of these tests' concern.

- armed.txt: the limit hit; Claude Code waits ("Continuing automatically at 5am") under the box, and has written its
  own line about it in the conversation above.
- soon.txt: at the reset ("Continuing shortly").
- quoted.txt: another channel's screen whose conversation quotes those lines (no wait): rows of it, and its box.
At the reset Claude Code sent the prompt "Your claude.ai usage limit has reset. Continue the task …" and went on
(Notification quota_auto_resume_fired); no stale screen was seen.
