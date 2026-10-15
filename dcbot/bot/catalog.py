"""What Claude Code can be asked to do with a "/": its commands and skills, for Discord's "/" menu.

Discord only lets the owner pick commands the bot has registered, so `/deep-research` typed on a phone goes
nowhere unless it is in the menu. The list is asked from Claude Code itself: `claude -p --output-format
stream-json` prints an "init" line naming every slash command and skill before it does anything else (the
process is killed right there, so no request is made). Descriptions come from the skills' SKILL.md files.
The result is cached in state/catalog.json and refreshed once a day.
"""
import json
import os
import re
import time

from altention import background
from altention.connectors.claude_code import commands as cc_commands

from . import config

CACHE = os.path.join(config.STATE, "catalog.json")
MAX_AGE = 24 * 3600
NAME = re.compile(r"^[a-z0-9_-]{1,32}$")  # what Discord accepts as a command name
MAX_DYNAMIC = 70                          # Discord allows 100 commands per server; the bot's own come first

# Built-in commands worth a menu entry of their own (the rest are reachable through /cc). name -> description.
BUILTIN = {
    "model": "Claude Code：切換模型（直接給名稱，例如 opus、sonnet、haiku）",
    "effort": "Claude Code：調整思考深度",
    "fast": "Claude Code：切換 Fast mode",
    "init": "Claude Code：為這個專案產生 CLAUDE.md",
    "security-review": "Claude Code：對目前的修改做安全性檢查",
    "rename": "Claude Code：幫這個對話取名字",
    "goal": "Claude Code：設定這個對話的目標",
    "insights": "Claude Code：分析最近的使用情況",
    "mcp": "Claude Code：看 MCP 伺服器的狀態",
    "agents": "Claude Code：管理子代理的設定",
}
# Skills that ship inside Claude Code have no SKILL.md on disk to read a description from.
KNOWN = {
    "deep-research": "多來源的深入研究，產出一份完整報告（會派出多個子代理）",
    "code-review": "審查目前的修改，或指定的 PR、分支、路徑",
    "simplify": "整理剛改過的程式碼：重用、簡化、效率",
    "loop": "每隔一段時間重複執行一個提示或指令",
    "schedule": "建立、查看、執行排程的雲端代理",
    "run": "啟動並操作這個專案的 app，確認修改真的有效",
    "doctor": "檢查 Claude Code 的安裝與設定",
    "fewer-permission-prompts": "掃描紀錄，把常用的唯讀指令加進允許清單",
    "morning": "晨間簡報",
    "skill-creator": "建立、修改或評估 skill",
}
# Skills that are reference material Claude reads on its own, not something the owner would start.
HIDDEN = {"built-in-browser", "chrome-browser", "computer-use", "docs", "docx", "pdf", "pptx", "xlsx", "google-workspace",
          "dataviz", "claude-api", "workflow-authoring", "plugin-authoring", "design-sync", "import-memory",
          "run-skill-generator", "cowork-plugin-customizer", "create-cowork-plugin", "git-workflow", "update-config"}


def clean_env():
    """The environment for a helper `claude`: it must not look like a session that reports to a channel, and it is
    a background Claude (altention/background.py: the hooks every Claude runs leave it alone)."""
    return background.env(dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "DCBOT_ROOT", "DCBOT_CHANNEL")))


async def ask_claude(cwd, timeout=60):
    """{"slash_commands": [...], "skills": [...]} for a session started in `cwd`, or None."""
    return await cc_commands.ask(config.CLAUDE, cwd, clean_env(), timeout)


def build(found, descriptions, reserved=()):
    """The menu entries for what Claude Code reported: [{"name", "run", "description"}], skills first.

    `name` is the Discord command, `run` what is typed into Claude Code (without the slash). Names the bot
    uses itself (`reserved`) and names Discord would refuse are left out; they remain reachable through /cc."""
    all_names = found.get("slash_commands") or []
    entries, seen = [], set(reserved)

    def add(run, description):
        name = cc_commands.short(run)
        if name in seen or not NAME.match(name):
            return
        seen.add(name)
        entries.append({"name": name, "run": cc_commands.short(run) if cc_commands.short(run) in all_names else run,
                        "description": cut(description, 100)})

    for run in found.get("skills") or []:
        if cc_commands.short(run) not in HIDDEN:
            desc = KNOWN.get(cc_commands.short(run)) or descriptions.get(cc_commands.short(run)) or descriptions.get(run) or "Claude Code 內建的 skill"
            add(run, "Skill：" + first_sentence(desc))
    for run, desc in BUILTIN.items():
        if run in all_names:
            add(run, desc)
    return entries[:MAX_DYNAMIC]


def first_sentence(text):
    text = " ".join(text.split())
    for found in re.finditer(r"[。！？]|[.!?](?=\s|$)", text):
        if found.start() > 12:  # not a full stop inside "e.g." or after a two-word opener
            return text[:found.start() + 1]
    return text


def cut(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def load():
    """The cached catalog: {"ts", "all": [every slash command], "entries": [...]}, or None."""
    try:
        with open(CACHE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("entries"), list) else None
    except (OSError, ValueError):
        return None


def stale(cat):
    return cat is None or time.time() - cat.get("ts", 0) > MAX_AGE


async def refresh(cwd, reserved=()):
    """Ask Claude Code again and rewrite the cache. Returns the new catalog, or None if Claude did not answer."""
    found = await ask_claude(cwd)
    if not found:
        return None
    cat = {"ts": time.time(), "cwd": cwd, "all": sorted(found["slash_commands"]),
           "entries": build(found, cc_commands.skill_descriptions(cwd), reserved)}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)
    return cat
