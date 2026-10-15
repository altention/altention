"""What Claude Code's transcript and hooks say is masked before the bot posts it to Discord (backlog B-12): API keys,
tokens, private keys (altention.redact, the one pattern chatlog uses too) become "[REDACTED]", and a message that had
some says so in a small line at its end (messages.note("masked")).

Where: everything the bot posts of Claude's words and of what its tools printed: the replies, what it said along the
way, the 📋 turn and its expansions, the history posted on /attach, !resume and dcpost --history, the subagents'
threads, what another session sent, the "working…" lines. Not the bot's own sentences, not the owner's commands.

A text cut short (a tool output clipped, a piece that goes on in the next message) may end in the first part of a
key, which the pattern alone does not see: what looks like the start of one at such an end is masked too
(PARTIAL), and a private key whose end line is missing is masked to the end of the text.
"""
import re

from altention.redact import MARK, redact

from . import messages

# The first part of a key, at the end of a text cut short (as altention.redact's pattern begins them)
PARTIAL = re.compile(r"(?<![A-Za-z0-9_-])"  # a word of its own (after a blank, punctuation or the start): not "disk-usage"
                     r"(sk-[A-Za-z0-9_-]{4,}|gh[pousr]_[A-Za-z0-9]{4,}|github_pat_[A-Za-z0-9_]{4,}|AKIA[0-9A-Z]{4,}"
                     r"|xox[baprs]-[A-Za-z0-9-]{2,}|AIza[0-9A-Za-z_-]{4,}"
                     r"|(?:M[Tjz]|N[DTjz]|O[DT])[A-Za-z0-9_-]{18,}(?:\.[A-Za-z0-9_-]*){0,2}"  # a Discord token: an id's digits in base64
                     r"|discord(?:app)?\.com/api/webhooks/\S*)$")
PEM_OPEN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?![\s\S]*-----END [A-Z ]*PRIVATE KEY-----)[\s\S]*")
CUT_MARK = re.compile(r"…（還有 \d+ 字")  # where the bot itself cut a text short (process.py, events.clip)


def mask(text, cut=False):
    """(text masked, how many places were masked). `cut`: the text itself ends where something was cut off."""
    if not isinstance(text, str) or not text:
        return text, 0
    before = text.count(MARK)
    out = PEM_OPEN.sub(MARK, redact(text))
    pieces = CUT_MARK.split(out)
    if len(pieces) > 1 or cut:  # what comes right before a cut, and the end of a text that is cut there
        marks = CUT_MARK.findall(out)
        ends = list(range(len(pieces) - 1)) + ([len(pieces) - 1] if cut else [])
        for i in ends:
            pieces[i] = PARTIAL.sub(MARK, pieces[i].rstrip()) + pieces[i][len(pieces[i].rstrip()):]
        out = pieces[0] + "".join(m + p for m, p in zip(marks, pieces[1:]))
    return out, out.count(MARK) - before


def masked(text, cut=False, limit=None):
    """The text masked, with the small line at its end when anything was. `limit`: cut to that many characters,
    after masking (a key cut in two is not seen by the pattern), the small line kept."""
    out, n = mask(text, cut)
    if limit is not None and len(out) > limit:
        out = out[:limit]
    return out + "\n" + messages.note("masked", n=n) if n else out
