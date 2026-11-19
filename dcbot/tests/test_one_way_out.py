"""C-172a P1b: the bot's own messages go out one way only, deliver() (bot/deliver.py). Nothing else in bot/ calls a
channel's send(), a message's edit() or delete(), or hands a send() on to be called later; what still does is listed
below, each with its reason (manager 11-16: an exceptions list, a reason for each)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import os
import re
import tokenize
import unittest

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

BOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")

# A send/edit called or handed on, a delete() called. An interaction's own answer (response.…, followup.…) is not a
# message of the bot's in a channel: Discord keeps it with the interaction.
OUT = re.compile(r"\.(send|edit)\b(?!_)|\.delete\(\)")
NOT_OURS = re.compile(r"\b(response|followup)\.")

# Whole files.
FILES = {
    "deliver.py": "the one way itself",
    "lease.py": "#bot-lease's lease message is the bots' own record, edited every round: not chat, and the ledger "
                "would be written that often",
    "layout.py": "channels and categories themselves moved (position, category): not messages",
}

# Single calls, by what the line holds.
CALLS = {
    ("posts.py", "ch.edit(category="): "a channel moved to its category: not a message",
    ("posts.py", "ch.edit(topic="): "a channel's topic: not a message",
    ("agents.py", "thread.edit(name="): "a thread renamed (a thread is a channel): not a message",
    ("agents.py", "thread.edit(archived="): "a thread put away: not a message",
    ("archive.py", "cat.edit(position="): "the archive category moved: not a message",
    ("archive.py", "ch.edit(category="): "a channel moved into or out of the archive (with its topic): not a message",
    ("archive.py", "ch.edit(topic="): "an archived channel's topic: not a message",
    ("reconcile.py", "ch.edit(topic="): "an attached channel's topic following its tmux session (C-201 2a): not a message",
    ("rename.py", "ch.edit(name="): "a channel renamed (/rename and what follows it, C-201 2b): not a message",
    ("main.py", "ch.edit(category="): "a channel put in the archive with its topic: not a message",
}


def lines(path):
    """(line number, line, the line without its comment) of a file; a "#" inside a string is not a comment."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    rows = text.split("\n")
    cut = {}
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            cut[tok.start[0]] = tok.start[1]
    return [(n, row, row[:cut.get(n, len(row))]) for n, row in enumerate(rows, 1)]


def found():
    """(file, line number, line) of every way out that is not deliver() and not listed above."""
    out = []
    for name in sorted(os.listdir(BOT)):
        if not name.endswith(".py") or name in FILES:
            continue
        for n, line, code in lines(os.path.join(BOT, name)):
            if not OUT.search(code) or NOT_OURS.search(code):
                continue
            if any(where == name and what in code for where, what in CALLS):
                continue
            out.append((name, n, line.strip()))
    return out


class OneWayOut(unittest.TestCase):
    def test_nothing_else_sends_edits_or_deletes(self):
        self.assertEqual(found(), [], "a message of the bot's goes out through deliver.deliver()/deliver.remove(); "
                                      "anything else needs its reason in FILES or CALLS")

    def test_each_exception_has_a_reason_and_is_still_there(self):
        for name, why in FILES.items():
            self.assertTrue(why.strip(), name)
            self.assertTrue(os.path.exists(os.path.join(BOT, name)), "%s is gone: take it off the list" % name)
        for (name, what), why in CALLS.items():
            self.assertTrue(why.strip(), what)
            with open(os.path.join(BOT, name), encoding="utf-8") as f:
                self.assertIn(what, f.read(), "%s no longer does %s: take it off the list" % (name, what))

    def test_the_check_catches_what_it_is_for(self):
        for line in ("await ch.send('x')", "msg = await m.edit(content=1)", "await m.delete()",
                     "self.command(ch, text, message.channel.send)"):
            self.assertTrue(OUT.search(line) and not NOT_OURS.search(line), line)
        for line in ("await interaction.response.send_message('x')", "await interaction.followup.send('x')",
                     "await deliver.deliver(ch, 'x', intent='status')", "await pane.send_keys('x')"):
            self.assertFalse(OUT.search(line) and not NOT_OURS.search(line), line)


if __name__ == "__main__":
    unittest.main()
