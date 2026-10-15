"""Subagents and workflows, shown as Discord threads under the session's channel.

In the terminal a subagent is a line in the footer that the owner can step into. Here each one gets a
status message in the channel ("running" -> "done, 40 s") with a thread hanging from it: the task it was
given, what it does (tool calls and what it says, read from its own transcript), and its result. A
workflow gets one thread for the whole run, with every agent's start and result in it. A finished thread
is archived, so the channel list stays short; the status message keeps the way back to it.

Sources (see the hook events in the connector's hooks.py):
- SubagentStart / SubagentStop carry agent_id and agent_type; Stop also carries the result text.
- PreToolUse from inside a subagent carries its agent_id.
- PostToolUse of the Workflow tool carries the run id, name and task id.
- <session>/subagents/agent-<id>.meta.json holds the description; workflow agents live one level down, in
  <session>/subagents/workflows/<run id>/, which is how an agent is known to belong to a run.

What must survive a bot restart (thread and message ids) is kept in the channel's state: st["rooms"] and
st["subs"]. Buffers and file positions are memory only.
"""
import asyncio
import time
import traceback

from altention.connectors.claude_code import hooks as cc_hooks
from altention.connectors.claude_code import transcript

from . import config, events, masking
from .util import split_message

FLUSH_SECS = 4        # a thread gets at most one progress message this often
KEEP_SECS = 24 * 3600  # forget rooms and agents that never reported an end


def cut(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def tools_text(tools):
    return ", ".join("%s×%d" % kv for kv in sorted(tools.items(), key=lambda kv: -kv[1]))


def agent_status(sub, done=False, now=None, failed=None):
    """`failed`: why its request failed (words), when it ended that way."""
    head = "🤖 **子代理**%s · %s" % ("（%s）" % sub["type"] if sub.get("type") else "", sub["label"])
    if not done:
        return head + "\n⏳ 執行中，過程在討論串裡"
    bits = ["%.0f 秒" % ((now or time.time()) - sub["t0"])] + ([tools_text(sub["tools"])] if sub.get("tools") else [])
    return head + ("\n❌ 失敗（%s） · " % failed if failed else "\n✅ 完成 · ") + " · ".join(bits)


def workflow_status(room, done=False, now=None):
    head = "🧩 **Workflow** · %s" % room["title"]
    count = "%d／%d 個代理完成" % (room.get("done", 0) - room.get("failed", 0), room.get("started", 0))
    if room.get("failed"):
        count += "、%d 個失敗" % room["failed"]
    if not done:
        return "%s\n⏳ 執行中，%s，過程在討論串裡" % (head, count)
    return "%s\n✅ 完成 · %s · %.0f 秒" % (head, count, (now or time.time()) - room["t0"])


OWN = (cc_hooks.AGENT_START, cc_hooks.AGENT_STOP, cc_hooks.AGENT_PROGRESS, cc_hooks.AGENT_LAUNCHED, cc_hooks.WORKFLOW_START)
# the kinds of event (the connector's hooks.normalize) that are a subagent's or a workflow's own


class Agents(object):
    """`host` needs get_channel(id) and save(), like the bridge's host."""

    def __init__(self, host):
        self.host = host
        self.buf, self.flushed = {}, {}  # (channel id, room key) -> lines waiting / when last posted
        self.pos = {}                    # agent id -> (transcript path, bytes already relayed)

    # ------------------------------------------------------------------ events
    async def handle(self, ch, st, ev):
        """Take the event if it belongs to a subagent or workflow. Returns True if the bridge should not."""
        what, aid = cc_hooks.normalize(ev).get("dc_kind"), ev.get("agent_id")  # (the bridge has made the kind out)
        if not config.AGENT_THREADS:
            return bool(aid) or what in OWN
        if what in (cc_hooks.WORKFLOW_START, cc_hooks.AGENT_LAUNCHED):
            started = ev.get("started") or {}
            if what == cc_hooks.WORKFLOW_START and started.get("runId"):
                st.setdefault("workflows", {})[started["runId"]] = {
                    "name": started.get("workflowName") or ev.get("hint") or started["runId"],
                    "summary": started.get("summary") or "", "task": started.get("taskId"), "t0": time.time()}
                room = st.get("rooms", {}).get("wf:" + started["runId"])
                if room:  # its first agents can report before the tool call itself has returned
                    room["title"] = self.workflow_title(st, started["runId"])
                    await self.retitle(ch, room)
            return True
        if what == cc_hooks.AGENT_START and aid and ev.get("agent_type"):
            await self.start(ch, st, ev)
            return True
        if what == cc_hooks.AGENT_STOP:
            if aid and aid in st.get("subs", {}):
                await self.stop(ch, st, ev)
            return True  # also the unnamed helper agents Claude Code runs for itself (prompt suggestions, …)
        if what == cc_hooks.AGENT_PROGRESS:
            if aid not in st.get("subs", {}) and ev.get("agent_type"):
                await self.start(ch, st, ev)  # started while the bot was not listening
            if aid in st.get("subs", {}):
                self.progress(ch, st, ev)
            return True
        if what == cc_hooks.TURN_FAILED and aid:
            # A subagent's own request failed (Claude Code tells it as a StopFailure that bears the subagent's id, and
            # no SubagentStop follows): its thread says so and is closed. It is not the conversation's turn that
            # failed: nothing of it goes to the channel (#issues 30: "這一輪中斷了" and a button to try again, while
            # the conversation went on).
            await self.failed(ch, st, ev)
            return True
        if what == cc_hooks.TURN_END:
            await self.main_stopped(ch, st, ev)
        return False

    def workflow_title(self, st, run):
        w = st.get("workflows", {}).get(run) or {}
        return cut("%s%s" % (w.get("name") or run, "：" + w["summary"] if w.get("summary") else ""), 150)

    async def start(self, ch, st, ev):
        aid, path = ev["agent_id"], ev.get("transcript_path") or ""
        agent_path = ""
        if not ev.get("host"):  # (another machine's: read there by tools/dcremote, and on the event as it comes)
            # The agent's files appear a moment after the event, and its first row (what it was asked) with them or
            # a moment after: both are waited for (#issues 24), by the connector's count, in this loop's own way.
            for gap in cc_hooks.agent_waits(ev):
                await asyncio.sleep(gap)
            found = transcript.locate(path, aid)
            agent_path = found[1] if found else ""  # what it says from here on is read from it (said())
        # one shape, wherever it was read: agent_meta, agent_run, agent_prompt on the event, or none of them (its
        # files never came: a thread by its type and id)
        meta, run, prompt = ev.get("agent_meta") or {}, ev.get("agent_run"), ev.get("agent_prompt") or ""
        label = cut(meta.get("description") or prompt.split("\n")[0] or ev.get("agent_type") or aid, 150)
        sub = {"label": label, "type": ev.get("agent_type") or "", "t0": time.time(), "tools": {},
               "room": "wf:" + run if run else "a:" + aid}
        rooms = st.setdefault("rooms", {})
        room = rooms.get(sub["room"])
        if room is None:
            room = rooms[sub["room"]] = {"kind": "workflow" if run else "agent", "t0": time.time(), "started": 0,
                                         "done": 0, "title": self.workflow_title(st, run) if run else label}
            text = workflow_status(room) if run else agent_status(sub)
            try:
                msg = await ch.send(text, silent=True)
                room["msg"] = msg.id
                thread = await msg.create_thread(name=cut(("🧩 " if run else "🤖 ") + room["title"], 95),
                                                 auto_archive_duration=60)
                room["thread"] = thread.id
            except Exception:  # no thread (no permission, or this is a DM): the status line still works
                traceback.print_exc()
        st.setdefault("subs", {})[aid] = sub
        room["started"] = room.get("started", 0) + 1
        self.pos[aid] = (agent_path, 0)
        if run:
            await self.restatus(ch, room, workflow_status(room))
            self.add(ch, sub["room"], "▶️ **%s**%s" % (cut(label, 80), "\n" + events.quote(cut(prompt, 600), 600)
                                                       if prompt and cut(prompt, 80) != cut(label, 80) else ""))
        elif prompt:
            self.add(ch, sub["room"], "**任務**\n" + events.quote(prompt, 1500))
        self.host.save()

    def progress(self, ch, st, ev):
        """A tool call inside a subagent: one line in its thread, after anything it said since the last one."""
        aid = ev["agent_id"]
        sub = st["subs"][aid]
        name = ev.get("tool_name", "?")
        sub["tools"][name] = sub["tools"].get(name, 0) + 1
        who = "**%s** " % cut(sub["label"], 30) if sub["room"].startswith("wf:") else ""
        for text in self.said(aid, ev):
            self.add(ch, sub["room"], who + events.quote(cut_block(text, 700), 700))
        self.add(ch, sub["room"], "%s`%s` %s" % (who, name, cut(ev.get("hint"), 160)))

    def said(self, aid, ev=None):
        if ev is not None and ev.get("host"):
            return []  # its transcript is on another machine
        path, pos = self.pos.get(aid) or ("", 0)
        if not path and ev is not None:
            found = transcript.locate(ev.get("transcript_path") or "", aid)
            path = found[1] if found else ""
            pos = transcript.size(path) if path else 0  # picked up midway: only what is said from here on
        if not path:
            return []
        texts, pos = transcript.new_texts(path, pos, sidechain=True)
        self.pos[aid] = (path, pos)
        return texts

    async def failed(self, ch, st, ev):
        """A subagent's request failed: said in its thread, which ends as it does at a stop; #audit is told. One whose
        thread is over already (or that never had one: a helper agent) leaves a line in #audit and no more."""
        aid = str(ev["agent_id"])
        what, detail = events.failure(ev)
        known = aid in st.get("subs", {})
        label = st["subs"][aid]["label"] if known else ev.get("agent_type") or aid
        if known:
            await self.stop(ch, st, dict(ev, agent_id=aid), failed=(what, detail))
        await self.host.audit("#%s：子代理 %s → 失敗（%s）" % (getattr(ch, "name", "DM"), cut(label, 60), what))

    async def stop(self, ch, st, ev, failed=None):
        """`failed`: (why, what the API said) when the subagent ended by a failed request, not by a result."""
        aid = ev["agent_id"]
        sub = st["subs"].pop(aid)
        room = st.get("rooms", {}).get(sub["room"]) or {}
        result = (ev.get("last_assistant_message") or "").strip()
        if failed:
            result = "⚠️ %s%s" % (failed[0], "\n" + events.quote(failed[1], 1500) if failed[1] else "")
        self.said(aid, ev)  # whatever is left in the transcript is the result itself, not progress
        self.pos.pop(aid, None)
        took = "%.0f 秒" % (time.time() - sub["t0"]) + (" · " + tools_text(sub["tools"]) if sub["tools"] else "")
        if room.get("kind") == "workflow":
            room["done"] = room.get("done", 0) + 1
            if failed:
                room["failed"] = room.get("failed", 0) + 1
            self.add(ch, sub["room"], "%s **%s**（%s）\n%s" % ("❌" if failed else "✅", cut(sub["label"], 80), took,
                                                             result if failed else cut_block(result, 1500) or "（沒有文字結果）"))
            await self.restatus(ch, room, workflow_status(room))
        else:
            self.add(ch, sub["room"], "**%s**（%s）\n%s" % ("失敗" if failed else "結果", took, result or "（沒有文字結果）"))
            await self.flush(ch, sub["room"], room, everything=True)
            await self.restatus(ch, room, agent_status(sub, done=True, failed=failed and failed[0]))
            await self.close(ch, st, sub["room"])
        self.host.save()

    async def main_stopped(self, ch, st, ev):
        """The session's own turn ended, and the event lists what still runs in the background. A subagent
        that is not on the list is over, even if its SubagentStop never came (a session started before that
        hook existed, or the bot was down); a workflow whose task is not on it is over too."""
        running = set(str(t.get("id")) for t in ev.get("background_tasks") or [] if isinstance(t, dict))
        for aid, sub in list(st.get("subs", {}).items()):
            if aid not in running and not sub["room"].startswith("wf:"):
                texts = self.said(aid, ev)
                await self.stop(ch, st, dict(ev, agent_id=aid, last_assistant_message=texts[-1] if texts else ""))
        for key, room in list(st.get("rooms", {}).items()):
            if room.get("kind") != "workflow":
                continue
            task = (st.get("workflows", {}).get(key[3:]) or {}).get("task")
            if task and str(task) not in running and room.get("done", 0) >= room.get("started", 0):
                await self.flush(ch, key, room, everything=True)
                await self.restatus(ch, room, workflow_status(room, done=True))
                await self.close(ch, st, key)
                st.get("workflows", {}).pop(key[3:], None)

    # ------------------------------------------------------------------ Discord
    def add(self, ch, key, text):
        self.buf.setdefault((ch.id, key), []).append(text)

    async def thread(self, ch, room):
        tid = room.get("thread")
        if not tid:
            return None
        found = self.host.get_channel(tid)
        if found is None:
            try:
                found = await ch.guild.fetch_channel(tid)
            except Exception:
                return None
        return found

    async def flush(self, ch, key, room, everything=False):
        """Post what has gathered for one thread: one message's worth, or all of it."""
        lines = self.buf.pop((ch.id, key), [])
        self.flushed[(ch.id, key)] = time.time()
        thread = await self.thread(ch, room)
        if not lines or thread is None:
            return
        parts = split_message(masking.masked("\n".join(lines)), 1900)
        if not everything and len(parts) > 1:
            self.buf[(ch.id, key)] = ["\n".join(parts[1:])]
            parts = parts[:1]
        for part in parts:
            try:
                await thread.send(part, silent=True)
            except Exception:
                traceback.print_exc()
                return

    async def restatus(self, ch, room, text):
        if room.get("msg"):
            try:
                await ch.get_partial_message(room["msg"]).edit(content=text)
            except Exception:
                pass

    async def retitle(self, ch, room):
        await self.restatus(ch, room, workflow_status(room))
        thread = await self.thread(ch, room)
        if thread is not None:
            try:
                await thread.edit(name=cut("🧩 " + room["title"], 95))
            except Exception:
                pass

    async def close(self, ch, st, key):
        room = st.get("rooms", {}).pop(key, None) or {}
        thread = await self.thread(ch, room)
        if thread is not None:
            try:
                await thread.edit(archived=True)
            except Exception:
                pass
        self.buf.pop((ch.id, key), None)
        self.flushed.pop((ch.id, key), None)

    async def tick(self):
        """Called a couple of times a second by the bridge: post gathered progress, at a gentle pace."""
        now = time.time()
        for (cid, key) in list(self.buf):
            if now - self.flushed.get((cid, key), 0) < FLUSH_SECS:
                continue
            ch = self.host.get_channel(cid)
            st = self.host.channels_state.get(str(cid)) or {}
            room = st.get("rooms", {}).get(key)
            if ch is None or room is None:
                self.buf.pop((cid, key), None)
                continue
            await self.flush(ch, key, room)

    def prune(self):
        """Drop agents and rooms that never reported an end (the session died, the bot was down)."""
        old = time.time() - KEEP_SECS
        for st in self.host.channels_state.values():
            for name in ("subs", "rooms", "workflows", "btw"):
                for k, v in list((st.get(name) or {}).items()):
                    if v.get("t0", 0) < old:
                        st[name].pop(k, None)


def cut_block(text, n):
    """Like cut(), but keeps line breaks: for text shown as a block."""
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rstrip() + " …（略）"
