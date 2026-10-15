"""Run one Claude Code turn for a Discord channel, keeping a persistent session per channel."""
import asyncio
import json
import os
import uuid

from . import config

def deny():
    """Claude Code's own deny rules for the installation's secrets (they hold even in bypassPermissions mode), from
    the one list (altention/secrets.py); the guard hook (dchook --guard) is in force as well, for every Claude Code."""
    from altention import secrets
    return secrets.deny_rules()


def settings():
    return json.dumps({"permissions": {"deny": deny()}})


class TurnResult(object):
    def __init__(self, text, session_id, cost=0.0, tools=None, error=None, duration=0.0):
        self.text, self.session_id, self.cost = text, session_id, cost
        self.tools, self.error, self.duration = tools or {}, error, duration


async def run_turn(prompt, session_id, cwd, system_note, on_progress=None, procs=None, key=None, channel_id=""):
    """Send `prompt` into the channel's session. Creates the session on first use.
    on_progress(str) is called with short tool-use notes while Claude works."""
    new = session_id is None
    sid = session_id or str(uuid.uuid4())
    cmd = [config.CLAUDE, "-p", "--model", config.MODEL, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "bypassPermissions", "--settings", settings(),
           "--append-system-prompt", system_note]
    cmd += ["--session-id", sid] if new else ["--resume", sid]
    env = dict(os.environ, DCBOT_ROOT=config.ROOT, DCBOT_CHANNEL=str(channel_id))  # deploy.sh reports back here
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=cwd, env=env, limit=64 * 1024 * 1024)
    if procs is not None and key is not None:
        procs[key] = proc
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    tools, final = {}, None
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=config.TURN_TIMEOUT)
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        tools[name] = tools.get(name, 0) + 1
                        if on_progress:
                            inp = b.get("input") or {}
                            hint = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
                            await on_progress("%s %s" % (name, str(hint)[:80]))
            elif ev.get("type") == "result":
                final = ev
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        return TurnResult("", sid, tools=tools, error="超過 %d 秒，已中止" % config.TURN_TIMEOUT)
    finally:
        if procs is not None and key is not None:
            procs.pop(key, None)
    if final is None:
        err = (await proc.stderr.read()).decode("utf-8", "replace")[-500:]
        if proc.returncode and proc.returncode < 0:
            err = "已被中止"
        return TurnResult("", sid, tools=tools, error=err or "Claude 沒有回應（rc=%s）" % proc.returncode)
    return TurnResult(final.get("result") or "", final.get("session_id") or sid,
                      cost=float(final.get("total_cost_usd") or 0), tools=tools,
                      error=final.get("result") if final.get("is_error") else None,
                      duration=(final.get("duration_ms") or 0) / 1000.0)
