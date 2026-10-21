"""Markdown tables in Claude's final reply, made readable on Discord (C-71a): Discord shows `| a | b |` as it is, a
heap of bars and dashes. Before the reply is posted, a small table outside any ``` block becomes:
- an aligned code block, when every character of it is printable ASCII and it is at most WIDE wide;
- a list otherwise (Chinese, full-width, emoji, or ASCII too wide): one item a row, "- **first cell**：header2 value2；
  header3 value3", no alignment, so no character widths to get wrong.
A table of more than COLS columns or ROWS rows is left as it is, and so is the whole reply when anything here fails:
a reply must never fail to go out over this. Pure functions; the bot calls rewrite() (tmux_bridge, on Stop).
C-71b will put a picture in place of a Chinese table where one can be made, with the list as what it falls back to."""
import re

COLS = 5    # at most this many columns
ROWS = 20   # at most this many rows under the header
WIDE = 60   # an aligned code block at most this wide (an iPad held upright wraps a code block at about 60)
GAP = "  "  # between two columns of the code block

DELIMITER = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
WRAPPED = re.compile(r"^(\*\*|__|`+)(.+)\1$")  # a whole cell in bold or in code: the marks show as they are in a block


def cells(line, spans=True):
    """The cells of a table row: split at | but not at \\| nor inside a code span (`ps aux | grep x`: one cell,
    #code-review on 3db7694), the outer bars dropped, each cell stripped. A backtick run never closed is no code
    span (as Markdown has it): the row is split again with backticks as plain characters."""
    out, cur, i, tick = [], "", 0, 0  # tick: the length of the backtick run that opened the code span we are in
    line = line.strip()
    while i < len(line):
        ch = line[i]
        if ch == "\\" and line[i + 1:i + 2] == "|":
            cur += "\\|"
            i += 2
            continue
        if ch == "`" and spans:
            run = len(line[i:]) - len(line[i:].lstrip("`"))
            tick = 0 if tick == run else (run if not tick else tick)
            cur += "`" * run
            i += run
            continue
        if ch == "|" and not tick:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    if tick:
        return cells(line, spans=False)
    if out and not out[0].strip():
        out = out[1:]  # the bar the row starts with
    if out and not out[-1].strip() and line.endswith("|"):
        out = out[:-1]  # and the one it ends with
    return [c.strip().replace("\\|", "|") for c in out]


def plain(cell):
    """A cell without the bold or code marks around all of it."""
    m = WRAPPED.match(cell)
    return m.group(2).strip() if m else cell


def ascii_only(rows):
    return all(" " <= ch <= "~" for row in rows for cell in row for ch in cell)


def aligns(delimiter):
    """"left", "right" or "center" for each column, from the colons of the delimiter row."""
    out = []
    for c in cells(delimiter):
        out.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "left")
    return out


def as_block(head, rows, how):
    widths = [max(len(r[i]) for r in [head] + rows) for i in range(len(head))]

    def line(row):
        out = []
        for i, cell in enumerate(row):
            pad = {"right": cell.rjust, "center": cell.center}.get(how[i], cell.ljust)
            out.append(pad(widths[i]))
        return GAP.join(out).rstrip()
    rule = GAP.join("-" * w for w in widths)
    return ["```", line(head), rule] + [line(r) for r in rows] + ["```"]


def as_list(head, rows):
    """"- **first**：header2 value2；header3 value3"; a row with its first cell empty starts with "header1 value1"."""
    out = []
    for row in rows:
        rest = ["%s %s" % (head[i], row[i]) if head[i] else row[i] for i in range(1, len(row)) if row[i]]
        if row[0]:
            first = row[0] if "**" in row[0] else "**%s**" % row[0]  # bold already in part of it: not bolded again
            out.append("- %s%s" % (first, "：" + "；".join(rest) if rest else ""))
        else:
            out.append("- " + "；".join(rest) if rest else "-")
    return out


def table_at(lines, i):
    """(end, head, rows, aligns) of the table starting at lines[i], or None. `end`: the line after its last row."""
    if i + 1 >= len(lines) or "|" not in lines[i] or not DELIMITER.match(lines[i + 1].strip()):
        return None
    head = cells(lines[i])
    how = aligns(lines[i + 1])
    if len(head) != len(how) or len(head) < 2:
        return None
    rows, j = [], i + 2
    while j < len(lines) and lines[j].strip() and "|" in lines[j]:
        rows.append(cells(lines[j]))
        j += 1
    return j, head, rows, how


def rewritten(head, rows, how):
    """The lines a table becomes, or None to leave it as it is (too many columns or rows, or none)."""
    if len(head) > COLS or not rows or len(rows) > ROWS:
        return None
    n = len(head)
    if any(len(r) > n for r in rows) or any("```" in c for r in [head] + rows for c in r):
        return None  # a row with more cells than the header (what goes where is not known), or a fence in a cell
    rows = [r + [""] * (n - len(r)) for r in rows]  # a row short of cells: the rest are empty
    head, rows = [plain(c) for c in head], [[plain(c) for c in r] for r in rows]
    if ascii_only([head] + rows):
        block = as_block(head, rows, how)
        if max(len(l) for l in block) <= WIDE:
            return block
    return as_list(head, rows)


def rewrite(text):
    """(text, how many tables were rewritten). Lines inside ``` blocks are never touched; anything that fails gives
    back the text as it was."""
    try:
        return _rewrite(text)
    except Exception:
        return text, 0


def _rewrite(text):
    lines, out, fence, done, i = (text or "").split("\n"), [], None, 0, 0
    while i < len(lines):
        line = lines[i]
        mark = line.strip()[:3]
        if mark in ("```", "~~~") and fence in (None, mark):  # a code block: ``` or ~~~, closed by its own kind
            fence = None if fence else mark
        elif not fence:
            found = table_at(lines, i)
            if found:
                end, head, rows, how = found
                new = rewritten(head, rows, how)
                if new is not None:
                    out.extend(new)
                    done += 1
                    i = end
                    continue
                out.extend(lines[i:end])  # left as it is, all of it (its rows are not looked at again)
                i = end
                continue
        out.append(line)
        i += 1
    return "\n".join(out), done
