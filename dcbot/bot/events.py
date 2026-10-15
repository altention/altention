"""Hook events from tmux sessions: read the spool that tools/dchook writes, plus pure helpers on events."""
import json
import os
import re

from altention.connectors.claude_code import transcript
from altention.connectors.claude_code.screen import BTW_MAX  # how long a line Claude Code takes for a paste


def waiting(spool):
    """[(path, event)] oldest first, left in the spool: the caller removes each with done() once it is handled,
    so a restart in the middle of a batch handles the rest again (at worst one twice) instead of losing them.
    A file that is not an event is removed here (it would block the queue forever)."""
    try:
        names = sorted(n for n in os.listdir(spool) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return []
    out = []
    for n in names:
        path = os.path.join(spool, n)
        try:
            with open(path) as f:
                ev = json.load(f)
        except OSError:
            continue
        except ValueError:
            done(path)
            continue
        if isinstance(ev, dict) and ev.get("channel"):
            out.append((path, ev))
        else:
            done(path)
    return out


def done(path):
    try:
        os.remove(path)
    except OSError:
        pass


BAD_KEEP = 200


def bad_summary(spool):
    """(how many events were set aside, when the newest was) or None (for !status and doctor)."""
    bad = spool.rstrip("/") + "-bad"
    try:
        names = [n for n in os.listdir(bad) if n.endswith(".json")]
    except OSError:
        return None
    if not names:
        return None
    import time as _time
    newest = max(os.path.getmtime(os.path.join(bad, n)) for n in names)
    return len(names), _time.strftime("%m-%d %H:%M", _time.localtime(newest))


# not OSError as a whole: a FileNotFoundError from a bug must still count against a new version
TRANSIENT = {"ConnectionError", "TimeoutError", "gaierror", "ClientError", "DiscordServerError", "ConnectionClosed",
             "GatewayNotFound", "RateLimited"}
NET = ".net.json"  # the ending of an event set aside over a network or Discord outage, not a bug in the code


def transient(exc):
    """Did handling fail because Discord or the network did (5xx, 429, a dropped connection, a timeout), rather than
    because of the code? By class name, so this module needs neither discord nor aiohttp."""
    names = {c.__name__ for c in type(exc).__mro__}
    status = getattr(exc, "status", None)
    return bool(names & TRANSIENT) or ("HTTPException" in names and isinstance(status, int) and (status >= 500 or status == 429))


def set_aside(path, spool, net=False):
    """An event whose handling failed: moved to <spool>-bad/ (the newest BAD_KEEP kept) rather than retried
    forever at the head of the queue, where it would hold back every event after it. `net`: it failed over an
    outage (transient()); named *.net.json so that the supervisor does not take it for a broken version."""
    bad = spool.rstrip("/") + "-bad"
    name = os.path.basename(path)
    if net and name.endswith(".json"):
        name = name[:-5] + NET
    try:
        os.makedirs(bad, exist_ok=True)
        os.replace(path, os.path.join(bad, name))
        for old in sorted(os.listdir(bad))[:-BAD_KEEP]:
            os.remove(os.path.join(bad, old))
    except OSError:
        done(path)


def drain(spool):
    """Return the waiting events oldest first and remove them from the spool."""
    try:
        names = sorted(n for n in os.listdir(spool) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return []
    out = []
    for n in names:
        path = os.path.join(spool, n)
        try:
            with open(path) as f:
                ev = json.load(f)
            if isinstance(ev, dict) and ev.get("channel"):
                out.append(ev)
        except (OSError, ValueError):
            pass
        try:
            os.remove(path)
        except OSError:
            pass
    return out


def _norm(text):
    return " ".join(text.split())


def matches(prompt, text):
    """Did Claude receive `text`? `prompt` is what the UserPromptSubmit hook saw (may hold several messages)."""
    return bool(_norm(text)) and _norm(text) in _norm(prompt)


def from_terminal(prompt, typed=()):
    """Was this prompt typed at the terminal? (One the bot typed is matched before this is asked; what is
    left is the owner at the keyboard, unless it is a finished background task or a slash command we sent.)"""
    text = prompt.strip()
    automated = text.startswith("<") or re.match(r"^\[[A-Z][\w -]+\]", text)  # <task-notification>, [Cross-session idle notice]
    return bool(text) and not automated and not any(matches(prompt, t) for t in typed)


def quote(text, limit):
    text = text.strip()
    return "\n".join("> " + line for line in (text if len(text) <= limit else text[:limit] + "…").split("\n"))


def btw_prompt(pairs, question, limit=BTW_MAX, fresh=False):
    """What to type after "/btw" for a follow-up in a btw thread. Claude Code's /btw remembers nothing
    between questions, so the thread's earlier exchanges go along, on one line (the answer is read off
    the screen, and a long question pushes it out of view), newest kept when it does not all fit, the whole
    within `limit` characters. A question longer than that alone is returned as it is: btw_ask refuses it."""
    question = " ".join(question.split())
    earlier = []
    frame = len("（接續這段旁支對話；其他 /btw 的問答與這裡無關。這段對話先前的問答：）現在的問題：")
    for q, a in reversed(pairs):
        item = "問：%s → 答：%s" % (" ".join(q.split())[:80], " ".join(a.split())[:160])
        if frame + len("；".join([item] + earlier)) + len(question) > limit:
            break
        earlier.insert(0, item)
    if not earlier:  # `fresh`: the last /btw came from another thread, and /btw may still remember it
        return "（這是新的旁支問題，和先前其他 /btw 的問答無關）%s" % question if fresh else question
    return "（接續這段旁支對話；其他 /btw 的問答與這裡無關。這段對話先前的問答：%s）現在的問題：%s" % (
        "；".join(earlier), question)




def is_idle_notice(ev):
    """Claude Code nags after a minute of silence; that is not worth a Discord message."""
    return ev.get("notification_type") == "idle_prompt" or "waiting for your input" in (ev.get("message") or "")


def progress_line(ev):
    """One line for the "working…" status from a PreToolUse event."""
    return ("%s %s" % (ev.get("tool_name", "?"), (ev.get("hint") or "").replace("\n", " ")[:80])).strip()


def context_share(ctx):
    """"context 20%" for a context() result, or ""."""
    return "context %d%%" % round(100.0 * ctx[0] / transcript.window(ctx[1])) if ctx else ""


def footer(tools, seconds, background=0, ctx=None):
    """The small grey line under a reply: tools used, time taken, background work still running, how full
    the context window is."""
    bits = []
    if tools:
        bits.append(", ".join("%s×%d" % kv for kv in sorted(tools.items(), key=lambda kv: -kv[1])))
    if seconds is not None:
        bits.append("%.0f 秒" % seconds)
    if background:
        bits.append("背景工作 %d 個進行中" % background)
    if ctx:
        bits.append(context_share(ctx))
    return "-# " + " · ".join(bits) if bits else ""


FAILURES = {"rate_limit": "用量達到上限", "authentication_failed": "登入失效", "billing_error": "帳務問題",
            "invalid_request": "請求被拒", "server_error": "API 伺服器錯誤", "max_output_tokens": "回覆超過長度上限",
            "unknown": "API 錯誤"}


def failure(ev):
    """The words for why a request of Claude's failed (a StopFailure, as the connector sorted it: `dc_why` is the
    API's error code), and what the API said of it: (what, detail)."""
    what = FAILURES.get(ev.get("dc_why") or "unknown", "API 錯誤（%s）" % ev.get("error"))
    return what, (ev.get("last_assistant_message") or ev.get("error_details") or "").strip()
