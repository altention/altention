"""A terminal screen as a picture, for /screen on a phone.

A 120-column screen in a code block wraps every line on a phone and loses its colours. tmux gives the screen
with its colours (`capture-pane -e`: the text plus SGR escape sequences); this draws it, cell by cell, the
way a terminal would. Chinese and other wide characters take two cells; glyphs the main font lacks come
from the next font that has them.
"""
import io
import os
import re
import unicodedata

from PIL import Image, ImageDraw, ImageFont

SIZE = 16
PAD = 12
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
         "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf"]
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
FG, BG = (212, 212, 212), (24, 24, 27)
BASIC = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188), (17, 168, 205),
         (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67), (59, 142, 234),
         (214, 112, 214), (41, 184, 219), (255, 255, 255)]
# Claude Code's symbols that no font on the machine has, drawn as the nearest one that exists
SUBSTITUTE = {"\u23f5": "\u25b8", "\u23fa": "\u25cf", "\u23bf": "\u2514", "\u23f8": "\u2016", "\u23f9": "\u25a0"}
ESCAPE = re.compile(r"\x1b(?:\[([0-9;:?]*)([A-Za-z])|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Za-z]|.)")


def color256(n):
    if n < 16:
        return BASIC[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return steps[n // 36], steps[n // 6 % 6], steps[n % 6]
    v = 8 + (n - 232) * 10
    return v, v, v


def extended(nums):
    """A colour from the numbers after 38/48 ([5, n] or [2, r, g, b]) and how many numbers it used."""
    if nums[:1] == [5] and len(nums) >= 2:
        return color256(nums[1] % 256), 2
    if nums[:1] == [2] and len(nums) >= 4:
        return tuple(max(0, min(255, v)) for v in nums[1:4]), 4
    return None, len(nums)


def sgr(params, a):
    """Apply one SGR sequence's parameters to the attributes `a` (a dict, changed in place)."""
    groups = params.split(";") if params else ["0"]
    i = 0
    while i < len(groups):
        g = groups[i]
        if ":" in g:  # 38:2::r:g:b, 38:5:n: a colour in one group
            sub = [int(x) if x.isdigit() else None for x in g.split(":")]
            if sub[0] in (38, 48) and len(sub) > 2:
                nums = sub[1:]
                if nums[0] == 2 and len(nums) == 5:
                    nums = [2] + nums[2:]  # the empty colour-space id
                col, _ = extended([n or 0 for n in nums])
                a["fg" if sub[0] == 38 else "bg"] = col
            i += 1
            continue
        n = int(g) if g.isdigit() else 0
        if n in (38, 48):
            rest = [int(x) if x.isdigit() else 0 for x in groups[i + 1:i + 5]]
            col, used = extended(rest)
            a["fg" if n == 38 else "bg"] = col
            i += 1 + used
            continue
        if n == 0:
            a.clear()
        elif n == 1:
            a["bold"] = True
        elif n == 2:
            a["dim"] = True
        elif n == 4:
            a["under"] = True
        elif n == 7:
            a["rev"] = True
        elif n == 22:
            a.pop("bold", None), a.pop("dim", None)
        elif n == 24:
            a.pop("under", None)
        elif n == 27:
            a.pop("rev", None)
        elif 30 <= n <= 37 or 90 <= n <= 97:
            a["fg"] = BASIC[n - 30 if n < 90 else n - 82]
        elif n == 39:
            a.pop("fg", None)
        elif 40 <= n <= 47 or 100 <= n <= 107:
            a["bg"] = BASIC[n - 40 if n < 100 else n - 92]
        elif n == 49:
            a.pop("bg", None)
        i += 1


def width(ch):
    if unicodedata.combining(ch) or ch in "​‍️":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def parse(text):
    """Rows of cells [char, attributes, width] from `capture-pane -e` output. Attributes carry over lines,
    as on a terminal."""
    rows, a = [], {}
    for line in text.split("\n"):
        row, pos = [], 0
        for m in ESCAPE.finditer(line + "\x1b["):
            for ch in line[pos:m.start()]:
                w = width(ch)
                if w == 0 and row:
                    row[-1][0] += ch
                elif w:
                    row.append([ch, dict(a), w])
            pos = m.end()
            if m.group(2) == "m":
                sgr(m.group(1) or "", a)
        rows.append(row)
    while rows and not any(c[0].strip() for c in rows[-1]):
        rows.pop()
    return rows


class Fonts(object):
    def __init__(self):
        self.faces = [ImageFont.truetype(p, SIZE) for p in FONTS if os.path.exists(p)]
        self.bold = ImageFont.truetype(BOLD, SIZE) if os.path.exists(BOLD) else self.faces[0]
        self.missing = [bytes(f.getmask("￿")) for f in self.faces]
        self.cache = {}
        self.cell = self.faces[0].getlength("M")
        ascent, descent = self.faces[0].getmetrics()
        self.ascent, self.line = ascent, ascent + descent + 2

    def face(self, ch, bold):
        """The first font that has this character's glyph (the main font in bold if it is the one)."""
        if ch not in self.cache:
            self.cache[ch] = 0
            for i, f in enumerate(self.faces):
                if not ch.strip() or bytes(f.getmask(ch[0])) != self.missing[i]:
                    self.cache[ch] = i
                    break
        i = self.cache[ch]
        return self.bold if bold and i == 0 else self.faces[i]


_fonts = None


def render(text, cols=None):
    """PNG bytes of a screen given as `capture-pane -e` output."""
    global _fonts
    _fonts = _fonts or Fonts()
    f = _fonts
    rows = parse(text) or [[]]
    cols = max([cols or 0] + [sum(c[2] for c in r) for r in rows])
    w, h = int(PAD * 2 + cols * f.cell), PAD * 2 + len(rows) * f.line
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(rows):
        x, top = 0, PAD + y * f.line
        for ch, a, cw in row:
            fg, bg = a.get("fg") or FG, a.get("bg")
            if a.get("rev"):
                fg, bg = bg or BG, fg
            if a.get("dim"):
                fg = tuple((c + b) // 2 for c, b in zip(fg, BG))
            left, right = PAD + x * f.cell, PAD + (x + cw) * f.cell
            if bg:
                draw.rectangle([left, top, right, top + f.line - 1], fill=bg)
            if ch in "─━":  # rules and boxes join up across cells
                draw.line([left, top + f.line // 2, right, top + f.line // 2], fill=fg, width=2 if ch == "━" else 1)
            elif ch in "│┃":
                mid = (left + right) // 2
                draw.line([mid, top, mid, top + f.line], fill=fg, width=2 if ch == "┃" else 1)
            elif ch.strip():
                ch = SUBSTITUTE.get(ch, ch)
                face = f.face(ch, a.get("bold"))
                at = left + max(0, (right - left - face.getlength(ch)) / 2) if cw == 2 else left  # wide: centred
                draw.text((at, top + f.ascent + 1), ch, font=face, fill=fg, anchor="ls")
            if a.get("under"):
                draw.line([left, top + f.line - 2, right, top + f.line - 2], fill=fg)
            x += cw
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
