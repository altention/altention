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
import unicodedata

from altention.redact import MARK, PATTERN, redact

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


# A key wrapped onto the next row: the run of a key's characters a row ends with, and the one the next row starts
# with after its frame (Claude Code's dialogs draw "│" down their left side) and blanks.
KEYISH = r"[A-Za-z0-9_.+/=:-]"
TAIL = re.compile(KEYISH + r"+$")
WRAPPED = re.compile(r"^([\s│┃|╎╏]*)(" + KEYISH + r"+)")


def shown(row):
    """How many columns `row` takes on a terminal (a wide character two)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in row)


def screen(text):
    """A screen (or part of one) read off a pane, as it is to be posted: (masked text, the small line to add after
    it, "" when nothing was masked). Masked whole, before anything cuts it (C-127).

    A pane breaks a long row where its width ends (and Claude Code breaks its own boxes' rows): a key can stand on
    two rows or more, a real one being over 100 characters and a pane maybe 40 wide, and the break can fall
    anywhere in it, after "sk" as well as after "sk-ant-api03-". So the pattern is not run on each row alone: a
    row that reaches the pane's width (the widest row there is: a row the pane did not break is shorter) and ends
    in a run of a key's characters is joined to the run the next row starts with (after its frame and blanks),
    and on to the row after while a row is all such characters and as wide; the pattern runs on what is joined,
    and what it finds is masked on every row it stands on (#code-review and manager on ab3a424 and 31b7f85). A
    word that only follows a key at a row's end may be masked with it: more masked, never less."""
    text = text if isinstance(text, str) else ""
    rows = text.split("\n")
    full = max((shown(r.rstrip()) for r in rows), default=0) - 1  # a row the pane broke reaches its width: the widest
    spans = [[] for _ in rows]  # per row: (start, end) of what is to be masked
    found = 0
    i = 0
    while i < len(rows):
        pieces, joined, k = [(i, 0, len(rows[i]))], rows[i], i  # (row, start, end) of each piece in `joined`
        while k + 1 < len(rows) and TAIL.search(rows[k].rstrip()) and len(rows[k].rstrip()) == pieces[-1][2] \
                and shown(rows[k].rstrip()) >= full:
            m = WRAPPED.match(rows[k + 1])
            if not m:
                break
            pieces.append((k + 1, m.start(2), m.end(2)))
            joined += m.group(2)
            k += 1
            if rows[k][m.end(2):].strip():  # the row goes on with something else: the key ended there
                break
        hits = [(h.start(), h.end()) for h in PATTERN.finditer(joined)]
        found += len([1 for a, b in hits if a < len(rows[i])])  # counted on the row it starts on
        at = 0
        for row, a, b in pieces:
            n = b - a
            for x, y in hits:
                lo, hi = max(x, at), min(y, at + n)
                if lo < hi:
                    spans[row].append((a + lo - at, a + hi - at))
            at += n
        i = max(k, i + 1)
    for r, row in enumerate(rows):
        for a, b in sorted(set(spans[r]), reverse=True):  # from the right: the left ones keep their places
            row = row[:a] + MARK + row[b:]
        rows[r] = re.sub(re.escape(MARK) + r"(?:" + re.escape(MARK) + r")+", MARK, row)  # overlapping spans: one mark
    out, more = mask("\n".join(rows))  # what the joining left (a private key, a key a cut ends with)
    n = found + more
    return out, ("\n" + messages.note("masked", n=n) if n else "")


def masked(text, cut=False, limit=None):
    """The text masked, with the small line at its end when anything was. `limit`: cut to that many characters,
    after masking (a key cut in two is not seen by the pattern), the small line kept."""
    out, n = mask(text, cut)
    if limit is not None and len(out) > limit:
        out = out[:limit]
    return out + "\n" + messages.note("masked", n=n) if n else out
