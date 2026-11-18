"""Typing into a pane by its tmux:// identifier (C-92 b-1, `altention tmux send`): run on the machine the pane is on,
in one process from the first look to the keys (dcremote tmux-send), so little can change in between.

The order (manager 2026-10-24):
1. The identifier must carry the pane's #% id; what is to be typed is looked at (no new line or control character in
   text: a new line is an Enter; only key names tmux knows; nothing starting with sudo, su or doas, typed or spelled
   out in keys; anywhere a command can start — best effort: sent in two goes, or inside `bash -c "…"`, it is not
   caught; the real bounds are when this is used at all and #audit).
2. A look that changes nothing: the pane at that place is the one #% names; on a Claude Code pane only its ordinary
   input box takes keys (a dialog, its ! shell mode and its agents list are refused), and only while nobody's text
   is in it: what is there and not grey (a placeholder or a suggestion is) may be the owner's half-typed line, and
   on a screen without colours grey cannot be told, so that is a refusal too (the bot's own typist does the same,
   claude_code.owners_text; #code-review on 0303aec); every process in the pane's foreground is this user's, and not
   knowing is a refusal.
3. A line in #audit, and its answer awaited (`audit` is handed it): no answer, nothing is typed (and when the line
   may still come, a second one says it was not sent).
4. The same look again, right before the keys: anything changed (another pane, another process, the screen, the
   foreground), nothing is typed and #audit is told.
5. The keys.

Exit codes (the "code" of a refusal): 2 a wrong identifier or arguments, 3 no such pane or another one there now, 4
refused, 5 #audit did not take the line. Standard library only; the screen and Claude checks are handed in."""
import re
import time

from . import tmuxref

KEY = re.compile(r"^(?:[CMS]-)*(?:[!-~]|Enter|Escape|Tab|BTab|BSpace|Space|Up|Down|Left|Right|Home|End|PageUp|"
                 r"PageDown|PPage|NPage|IC|DC|F(?:[1-9]|1[0-2]))$")
# sudo, su, doas or pkexec where a command starts (the text's start, after ; & | ( { or a backquote: && || $( too),
# perhaps after env (with its NAME=value words), command, exec, nohup or time; inside quotes it is refused all the same
ROOT_WORDS = re.compile(r"(?:^|[;&|({`])\s*(?:(?:env(?:\s+[A-Za-z_][A-Za-z0-9_]*=\S*)*|command|exec|nohup|time)\s+)*"
                        r"(?:sudo|su|doas|pkexec)(?=$|[\s;&|)}`])", re.M)
NEW_LINE_KEYS = ("Enter", "C-m", "C-j")
# Keys that change what was typed already (manager on #code-review's finding on afce55b): with them, what the box
# ends up holding is not what spelled() reads ("x BSpace ! l s" is "!ls" there), so no guess is made of it
EDIT_KEYS = ("BSpace", "C-h", "C-u", "C-w", "Left", "Right", "Home", "End", "C-a", "C-e")
AUDIT_MOST = 300  # characters of what is typed that #audit shows (masked first)


def box_check(screen):
    """`box_text` for look() from the connector's screen readers (input_text, input_dim, colourless): (raw screen,
    screen with colours or None) -> None when nobody's text is in the input box, else why not to type."""
    def check(view, colour_view):
        text = screen.input_text(view)
        if not text:
            return None
        quoted = text[:60]
        if colour_view is not None and screen.input_dim(colour_view):
            return None  # grey: a placeholder or a suggestion, typed over
        if colour_view is None or screen.colourless(colour_view):
            return "讀不到 Claude Code 畫面的顏色，看不出輸入框裡的「%s」是不是使用者打的：不知道就不送" % quoted
        return "Claude Code 的輸入框裡已經有字「%s」（可能是使用者打到一半）：不打進去" % quoted
    return check


class Refused(Exception):
    def __init__(self, why, code=4):
        Exception.__init__(self, why)
        self.code = code


def spelled(keys):
    """The text a run of keys types: single characters, Space, and a new line for each Enter; every other key is
    passed over (manager on #code-review-2's finding: stopping at the first one let Enter s u d o … through)."""
    out = []
    for k in keys:
        if len(k) == 1:
            out.append(k)
        elif k == "Space":
            out.append(" ")
        elif k in NEW_LINE_KEYS:
            out.append("\n")
    return "".join(out)


def what_is_typed(text, keys):
    """Refused (code 2) for arguments that are wrong, (code 4) for what is not typed: see the module."""
    if (text is None) == (not keys):
        raise Refused("要給文字或 --keys 其中一種", 2)
    if text is not None:
        if not text:
            raise Refused("文字是空的", 2)
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise Refused("文字裡有換行或控制字元：換行等於按 Enter，要按 Enter 請用 --enter")
        if ROOT_WORDS.search(text):
            raise Refused("文字裡有指令以 sudo／su／doas／pkexec 開頭：要用 root，請使用者在那個窗格自己打")
    for k in keys:
        if not KEY.match(k):
            raise Refused("不認得的按鍵：%s（只收 tmux 的鍵名，例如 Enter、Escape、C-c、Up）" % k, 2)
    if keys and ROOT_WORDS.search(spelled(keys)):
        raise Refused("按鍵拼出來有指令以 sudo／su／doas／pkexec 開頭：要用 root，請使用者在那個窗格自己打")


def look(ref, is_claude, pane_state, folder=None, box_text=None):
    """{"pane", "socket", "pid", "command", "claude", "state"} of the pane `ref` names, or Refused (code 3: not there
    or another one; 4: not to be typed into)."""
    if not ref.get("pane_id"):
        raise Refused("識別碼要有 #% 窗格編號（用 altention tmux list 給的）", 2)
    path, now = tmuxref.at(ref, folder)
    if now is None:
        raise Refused("那個位置沒有窗格（%s:%s.%s）" % (ref["session"], ref["window"], ref["pane"]), 3)
    if now != ref["pane_id"]:
        raise Refused("那個位置現在是窗格 %s，不是 %s：請重新 list" % (now, ref["pane_id"]), 3)
    code, out, _ = tmuxref.tmux(path, "display-message", "-p", "-t", now, "#{pane_id}\t#{pane_pid}\t#{pane_current_command}")
    f = out.rstrip("\n").split("\t")
    if code != 0 or len(f) != 3 or f[0] != now:
        raise Refused("讀不到窗格 %s" % now, 3)
    pid, command = f[1], f[2]
    claude = bool(is_claude(command, pid))
    state = None
    if claude:
        code, view, _ = tmuxref.tmux(path, "capture-pane", "-p", "-t", now)
        state = pane_state(view) if code == 0 else None
        if state != "box":
            why = {"dialog": "Claude Code 的畫面上不是一般輸入框（權限問題、選單或面板）",
                   "shell": "Claude Code 的輸入框在 ! 模式：打進去會當成 shell 指令執行",
                   "agents": "Claude Code 開著背景對話清單：打字會開一個新的對話"}.get(state, "讀不到 Claude Code 的畫面")
            raise Refused(why)
        code, colour_view, _ = tmuxref.tmux(path, "capture-pane", "-p", "-e", "-t", now)
        why = box_text(view, colour_view if code == 0 else None) if box_text else "讀不到 Claude Code 的輸入框"
        if why:
            raise Refused(why)
    others = tmuxref.foreground(pid)
    if others is None:
        raise Refused("看不出窗格前景的行程是誰的：不知道就不送")
    if others:
        raise Refused("窗格前景的行程有別的使用者（%s）：需要的話請使用者在那個窗格開 root" % "、".join(others))
    return {"pane": now, "socket": path, "pid": pid, "command": command, "claude": claude, "state": state}


def audit_line(ident, text, keys, enter, caller, reason):
    caller, reason = (tmuxref.masked(x).rstrip("\n") if x else x for x in (caller, reason))
    shown = tmuxref.masked(text if text is not None else " ".join(keys)).rstrip("\n")
    if len(shown) > AUDIT_MOST:
        shown = shown[:AUDIT_MOST] + "…（共 %d 字）" % len(shown)
    what = ("打字「%s」" % shown) if text is not None else ("按鍵 %s" % shown)
    return "⌨️ tmux send：%s%s → %s（%s%s）" % (what, "，再按 Enter" if enter else "", ident, caller or "不知道是誰",
                                               "；理由：%s" % reason if reason else "")


def type_into(look_, text, keys, enter, pace=None):
    """The keys. `pace`: (characters, seconds) — text longer than that goes in stretches with a pause between, as the
    bot types (Claude Code takes what arrives in one go as pasted: the connector's TYPE_CHUNK and TYPE_PAUSE)."""
    path, pane = look_["socket"], look_["pane"]
    if text is not None:
        size, pause = pace or (len(text), 0)
        for i in range(0, len(text), size):
            if i:
                time.sleep(pause)
            part = text[i:i + size]
            if part.endswith(";"):  # an argument ending in ";" ends a tmux command: "\\;" types it
                if part[:-1]:
                    tmuxref.tmux(path, "send-keys", "-t", pane, "-l", "--", part[:-1])
                part = "\\;"
            tmuxref.tmux(path, "send-keys", "-t", pane, "-l", "--", part)
    if keys:
        tmuxref.tmux(path, "send-keys", "-t", pane, *[k[:-1] + "\\;" if k.endswith(";") else k for k in keys])
    if enter:
        tmuxref.tmux(path, "send-keys", "-t", pane, "Enter")


def send(ref, ident, text=None, keys=(), enter=False, caller="", reason="", audit=None, is_claude=None,
         pane_state=None, folder=None, pace=None, box_text=None, audit_later=None):
    """{"sent": True, "pane"} or {"error", "code"}: the order of the module. `audit(line)` posts to #audit and says
    whether it got there: True only then, None when not known (the time was up: it may still come), False when it
    did not; `audit_later(line)` posts a line without waiting for it (default: audit); `is_claude(command, pid)`,
    `pane_state(raw screen)` and `box_text` (box_check) are the connector's."""
    keys = list(keys or [])
    try:
        what_is_typed(text, keys)
        first = look(ref, is_claude, pane_state, folder, box_text)
    except Refused as e:
        return {"error": str(e), "code": e.code}
    # (manager on #code-review-2's) any line, keys split by Enter as for sudo: an empty box takes Enter as nothing,
    # and the "!" after it opens ! mode all the same (#code-review-2 on e5f710b)
    if first["claude"] and any(line.startswith("!") for line in (text if text is not None else spelled(keys)).split("\n")):
        return {"error": "以 ! 開頭的字在 Claude Code 的輸入框會進 ! 模式，當成 shell 指令執行：不打進去", "code": 4}
    if first["claude"] and any(k in EDIT_KEYS for k in keys) and any(len(k) == 1 or k == "Space" for k in keys):
        return {"error": "--keys 裡同時有編輯鍵（%s）和打字的鍵：看不出輸入框最後會是什麼字，不打進 Claude Code"
                % "、".join(k for k in EDIT_KEYS if k in keys), "code": 4}
    took = audit(audit_line(ident, text, keys, enter, caller, reason))
    if took is None:  # it may still be posted: then it says what was not done
        # not waited for: the caller's own time (altention tmux: SEND_TIMEOUT) is not to run out on a second wait
        (audit_later or audit)("⌨️ tmux send：上一則沒有送出：#audit 沒有及時回答")
    if not took:
        return {"error": "#audit 記不上（機器人不在或沒有回答）：沒有送出", "code": 5}
    try:
        again = look(ref, is_claude, pane_state, folder, box_text)
        if (again["pane"], again["pid"], again["command"], again["state"]) != \
                (first["pane"], first["pid"], first["command"], first["state"]):
            raise Refused("檢查之後窗格變了（%s → %s）" % (
                "%s %s %s" % (first["pane"], first["pid"], first["command"]),
                "%s %s %s" % (again["pane"], again["pid"], again["command"])))
    except Refused as e:
        audit("⌨️ tmux send：上一則沒有送出：%s" % e)
        return {"error": "沒有送出：%s" % e, "code": 4}  # any change between the looks is a refusal (the design's 4)
    type_into(again, text, keys, enter, pace)
    return {"sent": True, "pane": again["pane"]}
