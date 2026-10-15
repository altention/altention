"""The file browser: one Discord message that shows a folder or a file and is edited in place as the owner clicks.

Where it shows up:
- `/files` or `!files [path]` in any channel: a browser from the channel's folder (on the channel's machine),
  closed with ✖;
- a files channel (setup card → 📂, `kind: "files"`): one browser that stays; typing a path jumps there,
  typing anything else searches the folder's files;
- the setup card's folder picker (mode "pick"): only folders, and "✅ 用這個資料夾" hands the folder back to
  bot/setup.py (Setup.picked);
- 📂 buttons under Claude's replies for the files they mention (links()).
A folder is a code block with the page's entries and a menu of the same entries (Discord menus hold 25, so
long folders are paged; 🔍 finds by name instead). A file is a page of numbered lines in a code block,
Discord colouring it by its extension; images are shown, 📎 sends the whole file.

The reading is bot/filecore.py, here or through `dcremote files` on another machine; it refuses secrets
(~/.config, ~/.ssh, keys, .env, …), names included. What each browser shows is kept in the channel's state
(`browsers`: message id -> state), and buttons are told apart by custom_id, so they keep working after a restart.
"""
import asyncio
import base64
import io
import json
import os
import re
import traceback
import unicodedata

import discord

from . import config, filecore, remote

PREFIX = "fb"
LINK = "fbl"
PAGE = 25           # entries per page: what one Discord menu holds
BUDGET = 1900       # characters in the message
KEEP = 20           # browsers remembered per channel
LINE_MAX = 180      # a longer line is cut in the file view
NAME_W = 24         # name column of a folder
LINKS_MAX = 5
LANGS = {"py", "js", "ts", "tsx", "jsx", "json", "sh", "bash", "zsh", "md", "yml", "yaml", "toml", "ini", "cfg", "html",
         "css", "scss", "c", "h", "cc", "cpp", "hpp", "go", "rs", "java", "kt", "rb", "php", "lua", "sql", "diff",
         "patch", "xml", "swift", "cs", "r", "m", "tex", "dockerfile", "makefile", "cmake", "vim", "proto", "csv"}


# ---------------------------------------------------------------------------------------------- reading
async def call(host, req):
    """filecore.op(req) here, or on `host` through dcremote."""
    if host:
        code, out = await remote.dcremote(host, "files", input=json.dumps(req), timeout=40)
        try:
            return json.loads(out) if code == 0 and out.strip() else {"error": remote.unreachable(host) if code == 255 else "%s 上的指令沒有回應（結束碼 %s）" % (host, code)}
        except ValueError:
            return {"error": "%s 回了看不懂的東西" % host}
    return await asyncio.get_running_loop().run_in_executor(None, filecore.op, req)


def host_of(st):
    """The machine a channel works on (None: this one)."""
    return (st.get("home") or {}).get("host") or (st.get("pane") or {}).get("host") or (st.get("files") or {}).get("host")


# ---------------------------------------------------------------------------------------------- text
def short(path, home):
    if home and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~" + path[len(home.rstrip("/")):]
    return path


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def fit(s, w):
    """`s` cut to `w` display columns (with …) and padded to exactly `w`."""
    if width(s) > w:
        out = ""
        for c in s:
            if width(out + c) > w - 1:
                break
            out += c
        s = out + "…"
    return s + " " * (w - width(s))


def safe(text):
    """Text that cannot close our code block."""
    return text.replace("```", "`​``")


def lang(path):
    base = os.path.basename(path).lower()
    ext = base.rsplit(".", 1)[-1] if "." in base else base
    return ext if ext in LANGS else ""


def where(b):
    return "　· %s" % b["host"] if b.get("host") else ""


def pages(n):
    return max(1, (n + PAGE - 1) // PAGE)


def shown_entries(data, b):
    entries = data.get("entries") or []
    return [e for e in entries if e["d"]] if b.get("mode") == "pick" else entries


def render_dir(data, b):
    """(content, components) of a folder. Components are plain tuples; view() makes them Discord's."""
    entries = shown_entries(data, b)
    total = pages(len(entries))
    b["page"] = page = min(max(0, b.get("page", 0)), total - 1)
    chunk = entries[page * PAGE:(page + 1) * PAGE]
    home = data.get("home")
    path = data["path"]
    g = data.get("git") or {}
    head = ("📁 **選資料夾：%s**" if b.get("mode") == "pick" else "📂 **%s**") % short(path, home)
    head += where(b)
    if g.get("branch"):
        head += "　`%s`%s" % (g["branch"], " · %d 個未提交" % g["dirty"] if g.get("dirty") else "")
    rows = []
    w = min(NAME_W, max([width(e["n"]) + 1 for e in chunk] or [1]))  # narrow enough for a phone
    for e in chunk:
        name = e["n"] + ("/" if e["d"] else "")
        info = ("%s 個" % e["c"] if e.get("c") is not None else "") if e["d"] else filecore.human(e["s"])
        mark = (g.get("marks") or {}).get(e["n"], "")
        rows.append(("%s %s %6s %s" % ("📁" if e["d"] else "📄", fit(name, w), info, mark)).rstrip())
    body = "```\n%s\n```" % safe("\n".join(rows) if rows else "（空的）")
    foot = "-# 第 %d/%d 頁 · 共 %d 項" % (page + 1, total, len(entries))
    if data.get("hidden") and not b.get("hidden"):
        foot += " · 另有 %d 個隱藏檔" % data["hidden"]
    if b.get("mode") == "pick":
        foot += "\n-# 點選單進資料夾，到了按「✅ 用這個資料夾」"
    elif b.get("mode") == "channel":
        foot += "\n-# 直接打字：路徑就跳過去，其他的就找檔案"
    content = "%s\n%s\n%s" % (head, body, foot)
    while len(content) > BUDGET and rows:  # very long names: fewer lines rather than a failed edit
        rows.pop()
        content = "%s\n```\n%s\n```\n%s" % (head, safe("\n".join(rows)), foot)
    comps = []
    if chunk:
        options = []
        for i, e in enumerate(chunk):
            idx = page * PAGE + i
            value = ("d:" if e["d"] else "f:") + e["n"]
            options.append((("📁 " if e["d"] else "📄 ") + e["n"][:95], value if len(value) <= 100 else "#%d" % idx,
                            ("%s 個項目" % e["c"] if e.get("c") is not None else "資料夾") if e["d"] else filecore.human(e["s"])))
        comps.append(("select", "open", "打開…", options, 0))
    comps += [("button", "up", "⬆ 上層", path == "/", 1), ("button", "prev", "◀", page == 0, 1),
              ("button", "next", "▶", page >= total - 1, 1), ("button", "find", "🔍 找", False, 1),
              ("button", "home", "🏠", path == b.get("root"), 1),
              ("button", "hidden", "🙈 藏起隱藏檔" if b.get("hidden") else "👁 隱藏檔", False, 2),
              ("button", "refresh", "🔄", False, 2)]
    if b.get("mode") == "pick":
        comps += [("button", "use", "✅ 用這個資料夾", False, 2), ("button", "back", "← 返回", False, 2)]
    elif b.get("mode") != "channel":
        comps.append(("button", "close", "✖ 關閉", False, 2))
    return content, comps


def number_lines(lines, first, budget, from_end=False):
    """Numbered lines that fit in `budget` characters: (text, first line number, last line number)."""
    if not lines:
        return "", first, first - 1
    last_no = first + len(lines) - 1
    w = len(str(last_no))
    picked, size = [], 0
    order = range(len(lines) - 1, -1, -1) if from_end else range(len(lines))
    for i in order:
        line = lines[i].expandtabs(4)
        if len(line) > LINE_MAX:
            line = line[:LINE_MAX - 1] + "…"
        row = "%*d│ %s" % (w, first + i, line)
        if picked and size + len(row) + 1 > budget:
            break
        picked.append((i, row))
        size += len(row) + 1
    picked.sort()
    return "\n".join(r for _, r in picked), first + picked[0][0], first + picked[-1][0]


def render_file(data, b, from_end=False):
    path, home = data["path"], data.get("home")
    head = "📄 **%s**%s　%s" % (short(path, home), where(b), filecore.human(data.get("size", 0)))
    comps_tail = [("button", "dir", "⬆ 回資料夾", False, 1), ("button", "attach", "📎 整個檔案", False, 1)]
    if b.get("mode") != "channel":
        comps_tail.append(("button", "close", "✖ 關閉", False, 1))
    if data.get("kind") == "image":
        return "🖼️ " + head[2:], comps_tail
    if data.get("kind") != "text":
        return head + "\n-# %s，這裡顯示不了；按 📎 下載" % ("二進位檔" if data.get("kind") == "binary" else "讀不了"), comps_tail
    total, lines, start = data.get("total"), data.get("lines") or [], data.get("start", 1)
    md = b.get("md") and lang(path) == "md"
    room = BUDGET - len(head) - 120
    if md:
        text, first, last = plain_lines(lines, start, room, from_end)
        body = text or "（空的）"
    else:
        text, first, last = number_lines(lines, start, room, from_end)
        body = "```%s\n%s\n```" % (lang(path), safe(text) if text else "（空檔案）")
    b["line"], b["last"] = first, last
    span = "第 %d–%d 行 / 共 %s 行" % (first, last, total if total is not None else "很多") if lines else "空檔案"
    content = "%s · %s\n%s" % (head, span, body)
    comps = [("button", "prev", "◀ 上一頁", first <= 1, 0),
             ("button", "next", "▶ 下一頁", total is not None and last >= total, 0),
             ("button", "goto", "↧ 跳到…", False, 0)]
    if lang(path) == "md":
        comps.append(("button", "md", "📝 原始碼" if md else "📖 排版", False, 0))
    return content, comps + comps_tail


def plain_lines(lines, first, budget, from_end=False):
    picked, size = [], 0
    order = range(len(lines) - 1, -1, -1) if from_end else range(len(lines))
    for i in order:
        if picked and size + len(lines[i]) + 1 > budget:
            break
        picked.append(i)
        size += len(lines[i]) + 1
    if not picked:
        return "", first, first - 1
    picked.sort()
    return "\n".join(lines[i][:budget] for i in picked), first + picked[0], first + picked[-1]


def render_find(data, b):
    home = data.get("home")
    matches = data.get("matches") or []
    head = "🔍 **找「%s」**　在 %s%s" % (b.get("query", ""), short(data.get("root", ""), home), where(b))
    rows = ["%2d. %s" % (i + 1, m) for i, m in enumerate(matches)]
    body = "```\n%s\n```" % safe("\n".join(rows)) if rows else "找不到符合的檔案。換個關鍵字（空白分開的幾個字會一起比對）。"
    content = "%s\n%s" % (head, body)
    while len(content) > BUDGET and rows:
        rows.pop()
        content = "%s\n```\n%s\n```" % (head, safe("\n".join(rows)))
    comps = []
    if matches:
        comps.append(("select", "pick", "打開…", [(("📁 " if m.endswith("/") else "📄 ") + m[-95:], "m:%d" % i, None)
                                                 for i, m in enumerate(matches)], 0))
    comps += [("button", "find", "🔍 再找", False, 1), ("button", "dir", "⬆ 回資料夾", False, 1)]
    if b.get("mode") != "channel":
        comps.append(("button", "close", "✖ 關閉", False, 1))
    return content, comps


def view(comps):
    v = discord.ui.View(timeout=None)
    for c in comps:
        if c[0] == "select":
            _, action, placeholder, options, row = c
            v.add_item(discord.ui.Select(custom_id="%s:%s" % (PREFIX, action), placeholder=placeholder, row=row,
                                         options=[discord.SelectOption(label=label[:100], value=value[:100],
                                                                       description=(desc or None) and desc[:100])
                                                  for label, value, desc in options[:25]]))
        else:
            _, action, label, disabled, row = c
            style = discord.ButtonStyle.success if action == "use" else discord.ButtonStyle.secondary
            v.add_item(discord.ui.Button(custom_id="%s:%s" % (PREFIX, action), label=label, disabled=disabled, row=row,
                                         style=style))
    return v


# ---------------------------------------------------------------------------------------------- links
URL = re.compile(r"\w+://\S+")
PATH = re.compile(r"(?<![\w/.~@-])((?:~/|/|\.\.?/)?(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.[A-Za-z][A-Za-z0-9]{0,7})(?::(\d+))?")


def mentioned(text):
    """[(path as written, line or None)] that look like file paths, in order, without repeats."""
    out, seen = [], set()
    for m in PATH.finditer(URL.sub(" ", text or "")):
        key = (m.group(1), m.group(2))
        if key not in seen and not m.group(1).endswith("."):
            seen.add(key)
            out.append((m.group(1), int(m.group(2)) if m.group(2) else None))
        if len(out) >= 40:
            break
    return out


# ---------------------------------------------------------------------------------------------- the browser
class PathModal(discord.ui.Modal):
    def __init__(self, files, title, label, action, default=""):
        super().__init__(title=title, timeout=1800)
        self.files, self.action = files, action
        self.field = discord.ui.TextInput(label=label, default=default or None, max_length=300)
        self.add_item(self.field)

    async def on_submit(self, interaction):
        try:
            await self.files.submitted(interaction, self.action, str(self.field.value).strip())
        except Exception:
            traceback.print_exc()


class Files(object):
    """`bot` is the Discord bot: chan(), save(), setup (for the folder picker)."""

    def __init__(self, bot):
        self.bot = bot

    def states(self, ch):
        return self.bot.chan(ch).setdefault("browsers", {})

    def remember(self, ch, msg_id, b):
        browsers = self.states(ch)
        browsers[str(msg_id)] = b
        keep = (self.bot.chan(ch).get("files") or {}).get("browser")
        for k in list(browsers)[:-KEEP]:
            if k != str(keep):
                browsers.pop(k, None)
        self.bot.save()

    def new_state(self, host, root, mode="temp", **extra):
        b = {"host": host, "root": root, "path": root, "page": 0, "view": "dir", "mode": mode, "hidden": False}
        b.update(extra)
        return b

    async def render(self, b):
        """(content, components, file to attach or None)."""
        if b["view"] == "find":
            data = await call(b["host"], {"op": "find", "root": b["path"], "query": b.get("query", ""),
                                         "hidden": b.get("hidden", False)})
            if data.get("error"):
                return "⚠️ " + data["error"], [("button", "dir", "⬆ 回資料夾", False, 0)], None
            b["matches"] = [os.path.join(data["root"], m) for m in data.get("matches") or []]
            content, comps = render_find(data, b)
            return content, comps, None
        if b["view"] == "file":
            back = b.pop("back", False)
            start = max(1, b.get("line", 1) - (60 if back else 0))
            count = (b.get("line", 1) - start) if back else 60
            data = await call(b["host"], {"op": "read", "path": b["file"], "start": start, "count": max(count, 1)})
            if data.get("error"):
                b["view"] = "dir"
                return await self.render(b) if data.get("dir") else ("⚠️ " + data["error"], [("button", "dir", "⬆ 回資料夾", False, 0)], None)
            content, comps = render_file(data, b, from_end=back)
            attach = None
            if data.get("kind") == "image":
                got = await call(b["host"], {"op": "raw", "path": b["file"]})
                if got.get("b64"):
                    attach = discord.File(io.BytesIO(base64.b64decode(got["b64"])), filename=got["name"])
                else:
                    content += "\n-# %s" % got.get("error", "")
            return content, comps, attach
        data = await call(b["host"], {"op": "list", "path": b["path"], "hidden": b.get("hidden", False)})
        if data.get("error"):
            comps = [("button", "up", "⬆ 上層", False, 0), ("button", "home", "🏠", False, 0)]
            if b.get("mode") == "pick":
                comps.append(("button", "back", "← 返回", False, 0))
            elif b.get("mode") != "channel":
                comps.append(("button", "close", "✖ 關閉", False, 0))
            return "⚠️ %s" % data["error"], comps, None
        b["path"] = data["path"]
        b["entries"] = [("d:" if e["d"] else "f:") + e["n"] for e in shown_entries(data, b)]
        content, comps = render_dir(data, b)
        return content, comps, None

    async def open(self, ch, host, root, send=None, mode="temp", target=None, line=None, **extra):
        """Post a new browser in `ch` (with `send` when given, e.g. a slash command's follow-up). `target`: a
        path to start at instead (a folder, or a file shown from `line`). Returns the message."""
        b = self.new_state(host, root, mode, **extra)
        m = re.match(r"^(.+?):(\d+)$", target or "")
        if m and line is None:  # "bot/main.py:120"
            target, line = m.group(1), int(m.group(2))
        if target:
            b.update(await self.locate(host, target, root))
            if line:
                b["line"] = max(1, line - 5)
        content, comps, attach = await self.render(b)
        v = view(comps)
        kwargs = {"view": v}
        if attach is not None:
            kwargs["file"] = attach
        msg = await (send(content, **kwargs) if send else ch.send(content, **kwargs))
        v.stop()
        if msg is not None:
            self.remember(ch, msg.id, b)
        return msg

    async def locate(self, host, target, cwd):
        """Browser state fields that show `target` (relative to cwd): a folder, or a file in its folder."""
        found = (await call(host, {"op": "resolve", "cwd": cwd, "paths": [target]})).get("found") or []
        if not found:
            return {"view": "dir", "path": cwd, "missing": target}
        _, path, is_dir = found[0]
        if is_dir:
            return {"view": "dir", "path": path, "page": 0}
        return {"view": "file", "path": os.path.dirname(path), "file": path, "line": 1}

    async def show(self, interaction, b, followup=None):
        """Redraw the browser message an interaction came from (already deferred)."""
        content, comps, attach = await self.render(b)
        if b.pop("missing", None):
            content = "⚠️ 找不到，留在原地\n" + content
        if followup:
            content = followup + "\n" + content
        v = view(comps)
        await interaction.edit_original_response(content=content[:2000], view=v, attachments=[attach] if attach else [])
        v.stop()
        self.remember(interaction.channel, interaction.message.id, b)

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        cid = (interaction.data or {}).get("custom_id") or ""
        kind, _, action = cid.partition(":")
        if kind not in (PREFIX, LINK):
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        try:
            if kind == LINK:
                return await self.link(interaction, action) or True
            b = self.states(interaction.channel).get(str(interaction.message.id))
            if b is None:
                await interaction.response.edit_message(content="這個檔案瀏覽器已經過期，請重新打 `/files`。", view=None)
                return True
            await self.act(interaction, b, action)
        except Exception:
            traceback.print_exc()
        return True

    async def act(self, interaction, b, action):
        values = (interaction.data or {}).get("values") or []
        if action in ("find", "goto"):  # these ask for text first
            name = os.path.basename(b["path"].rstrip("/")) or "/"
            modal = PathModal(self, ("找檔案（在 %s 底下）" % name)[:45], "檔名或路徑的一部分，空白分開可以多個字",
                              "find", b.get("query", "")) if action == "find" else \
                PathModal(self, "跳到第幾行", "行號", "goto")
            return await interaction.response.send_modal(modal)
        if action in ("use", "back") and b.get("mode") == "pick":
            return await self.bot.setup.picked(interaction, b, b["path"] if action == "use" else None)
        if action == "close":
            await interaction.response.defer()
            self.states(interaction.channel).pop(str(interaction.message.id), None)
            self.bot.save()
            return await interaction.message.delete()
        if action == "attach":
            await interaction.response.defer(thinking=True)
            got = await call(b["host"], {"op": "raw", "path": b.get("file", "")})
            if not got.get("b64"):
                return await interaction.followup.send("⚠️ %s" % got.get("error", "讀不到"))
            return await interaction.followup.send("📎 `%s`" % short(got["path"], None),
                                                   file=discord.File(io.BytesIO(base64.b64decode(got["b64"])), filename=got["name"]))
        await interaction.response.defer()
        if action == "open":
            v = values[0] if values else ""
            if v.startswith("#"):
                entries = b.get("entries") or []
                i = int(v[1:])
                v = entries[i] if i < len(entries) else ""
            name = v[2:]
            if v.startswith("d:"):
                b.update({"path": os.path.join(b["path"], name), "page": 0, "view": "dir"})
            elif v.startswith("f:"):
                b.update({"file": os.path.join(b["path"], name), "line": 1, "view": "file", "md": False})
        elif action == "pick":  # a search result
            matches = b.get("matches") or []
            i = int(values[0][2:]) if values and values[0].startswith("m:") else -1
            if 0 <= i < len(matches):
                m = matches[i]
                if m.endswith("/"):
                    b.update({"path": m.rstrip("/"), "page": 0, "view": "dir"})
                else:
                    b.update({"path": os.path.dirname(m), "file": m, "line": 1, "view": "file", "md": False})
        elif action == "up":
            if b["view"] == "dir":
                b["path"] = os.path.dirname(b["path"].rstrip("/")) or "/"
            b.update({"page": 0, "view": "dir"})
        elif action == "dir":
            b["view"] = "dir"
        elif action == "home":
            b.update({"path": b["root"], "page": 0, "view": "dir"})
        elif action == "hidden":
            b.update({"hidden": not b.get("hidden"), "page": 0})
        elif action == "md":
            b.update({"md": not b.get("md")})
        elif action == "prev":
            if b["view"] == "file":
                b["back"] = True
            else:
                b["page"] = max(0, b.get("page", 0) - 1)
        elif action == "next":
            if b["view"] == "file":
                b["line"] = b.get("last", b.get("line", 1)) + 1
            else:
                b["page"] = b.get("page", 0) + 1
        await self.show(interaction, b)

    async def submitted(self, interaction, action, text):
        """A modal from a browser: find or go to a line."""
        b = self.states(interaction.channel).get(str(interaction.message.id)) if interaction.message else None
        if b is None:
            return await interaction.response.send_message("這個檔案瀏覽器已經過期。", ephemeral=True)
        await interaction.response.defer()
        if action == "goto":
            digits = re.sub(r"\D", "", text)
            b["line"] = max(1, int(digits)) if digits else 1
        elif action == "find":
            if b["view"] == "file":
                b["view"] = "dir"
            b.update({"query": text, "view": "find"})
        await self.show(interaction, b)

    async def typed(self, ch, text):
        """A message in a files channel: a path to go to, or words to look for. The browser moves to the bottom."""
        st = self.bot.chan(ch)
        conf = st.get("files") or {}
        old_id = conf.get("browser")
        old = self.states(ch).get(str(old_id)) if old_id else None
        host, root = conf.get("host"), conf.get("root") or st["cwd"]
        cwd = (old or {}).get("path") or root
        m = re.match(r"^(\S+?)(?::(\d+))?$", text.strip())
        target = None
        if m and ("/" in m.group(1) or m.group(1).startswith("~") or "." in m.group(1)):
            found = (await call(host, {"op": "resolve", "cwd": cwd, "paths": [m.group(1)]})).get("found") or []
            if not found and cwd != root:
                found = (await call(host, {"op": "resolve", "cwd": root, "paths": [m.group(1)]})).get("found") or []
            target = found[0][1] if found else None
        extra = {"hidden": (old or {}).get("hidden", False)}
        if target:
            msg = await self.open(ch, host, root, mode="channel", target=target,
                                  line=int(m.group(2)) if m.group(2) else None, **extra)
        else:
            msg = await self.open(ch, host, root, mode="channel", view="find", query=text.strip(), **dict(extra, path=cwd))
        if msg is not None:
            conf["browser"] = msg.id
            st["files"] = conf
            self.bot.save()
            if old_id:
                self.states(ch).pop(str(old_id), None)
                try:
                    await ch.get_partial_message(int(old_id)).delete()
                except Exception:
                    pass

    async def start_channel(self, ch):
        """The browser of a files channel, posted when it is set up."""
        st = self.bot.chan(ch)
        conf = st.get("files") or {}
        msg = await self.open(ch, conf.get("host"), conf.get("root") or st["cwd"], mode="channel")
        conf["browser"] = msg.id
        st["files"] = conf
        self.bot.save()

    # ------------------------------------------------------------------ 📂 under Claude's replies
    async def add_links(self, ch, st, message, text):
        """Buttons under `message` for the files `text` mentions that exist (checked where the channel works)."""
        try:
            want = mentioned(text)
            if not want or not hasattr(message, "edit"):
                return
            found = (await call(host_of(st), {"op": "resolve", "cwd": st.get("cwd"),
                                              "paths": [p for p, _ in want]})).get("found") or []
            where = dict((given, (path, is_dir)) for given, path, is_dir in found)
            links, seen = [], set()
            for given, line in want:
                if given in where and (where[given][0], line) not in seen:
                    seen.add((where[given][0], line))
                    links.append([where[given][0], line, given])
                if len(links) >= LINKS_MAX:
                    break
            if not links:
                return
            v = discord.ui.View(timeout=None)
            for i, (path, line, given) in enumerate(links):
                label = "📂 %s%s" % (given if len(given) <= 60 else "…" + given[-59:], ":%d" % line if line else "")
                v.add_item(discord.ui.Button(custom_id="%s:%d" % (LINK, i), label=label[:80], style=discord.ButtonStyle.secondary))
            await message.edit(view=v)
            v.stop()
            saved = st.setdefault("links", {})
            saved[str(message.id)] = links
            for k in list(saved)[:-50]:
                saved.pop(k, None)
            self.bot.save()
        except Exception:
            traceback.print_exc()

    async def link(self, interaction, action):
        st = self.bot.chan(interaction.channel)
        links = (st.get("links") or {}).get(str(interaction.message.id)) or []
        i = int(action) if action.isdigit() else -1
        if not 0 <= i < len(links):
            return await interaction.response.send_message("這個連結已經過期。", ephemeral=True)
        path, line, _ = links[i]
        await interaction.response.defer()
        await self.open(interaction.channel, host_of(st), st.get("cwd") or filecore.home(), target=path, line=line)
