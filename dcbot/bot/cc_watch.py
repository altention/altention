"""What the bot does when Claude Code on its machine fails (C-162 step 2, design sections 2.0 to 2.3).

Failures are counted (cc_health.note_fail) only when nothing says they are none of the version's
(cc_health.not_the_version: the usage limit, the login, the network, an OOM kill, a machine too busy). Counting
only makes the bot suspect a version; then the probe (cc_probe) runs, once more a minute later if it fails, and
only two failures at the same step bring this machine's channels down a layer (no pinned older version yet: that is
the next batch). Layer 2 itself broken (print turns with no result, claude exiting 0) brings them to layer 3.
The way back: a Claude Code version other than the one that brought them down passes the probe (looked at, at
most every VERSION_SECS, when a channel held down runs a turn), or `!mode retry` and the probe passes.

Inert unless main.main() starts it (ACTIVE): a test that makes the bridge never probes nor writes the health file.
"""
import asyncio
import time
import traceback

from . import calls, cc_health, deliver, messages

ACTIVE = False
PIN_KEEP, PIN_NEW = "ccpin:keep", "ccpin:new"  # the reminder's buttons (section 3.3)
TICK_SECS = 5 * 60
VERSIONS_SECS = 3600
DRILL_SECS = 30 * 60
RETRY_SECS = 60
VERSION_SECS = 10 * 60


def start():
    global ACTIVE
    ACTIVE = True


class Watch(object):
    def __init__(self, bridge, probe=None, started=None):
        self.bridge = bridge
        self.probe = probe
        self.started = started or time.time()
        self.held = []          # failures during the grace after a start: looked into once it is over
        self.busy = set()       # versions being probed
        self.version_seen = (0, None)
        self.sha_checked = 0
        self.versions_looked = 0
        self.drilling = None

    # ---------------------------------------------------------------- failures
    def version(self):
        return cc_health.version_of(cc_health.binary())

    async def failed(self, kind, channel, version=None, why="", text="", signal=""):
        """A failure of `kind` ("tmux", "resume", "json") in `channel`. Returns what was decided (for the log and
        the tests): "not_the_version", "held", "counted", "suspect", "json"."""
        if not ACTIVE:
            return None
        version = version or self.version()
        if not version:
            return None
        other = cc_health.not_the_version(text or why, signal)
        if other:
            print("[cc] %s 的失敗不算（%s）：#%s %s" % (version, other, channel, why[:120]), flush=True)
            return "not_the_version"
        if time.time() - self.started < cc_health.GRACE_SECS and kind != "json":
            self.held.append((kind, channel, version, why))
            return "held"
        got = cc_health.note_fail(kind, version, channel, why)
        if got == "suspect":
            self.spawn(self.suspect(version))
        elif got == "json":  # stream-json, checked on its own before it counts as broken (every step probed first)
            self.spawn(self.suspect_json(version))
        elif got == "start":  # claude -p exits non-zero twice (section 3.2): this version checked first
            self.spawn(self.suspect_start(version, why))
        return got or "counted"

    def worked(self, channel, kind="tmux", version=None):
        if ACTIVE:
            cc_health.note_ok(version or self.version(), channel, kind)

    async def after_grace(self):
        """The grace after a start is over: one probe for what failed in it, if anything did."""
        await asyncio.sleep(max(0, self.started + cc_health.GRACE_SECS - time.time()))
        if self.held:
            version = self.held[-1][2]
            self.held = []
            await self.suspect(version)

    async def ticking(self):
        """tick() every TICK_SECS, for good (main.Bot.cc_start(), after the grace)."""
        while ACTIVE:
            try:
                await self.tick()
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(TICK_SECS)

    def spawn(self, coro):
        task = asyncio.ensure_future(coro)
        task.add_done_callback(lambda t: t.cancelled() or t.exception() and traceback.print_exception(
            type(t.exception()), t.exception(), t.exception().__traceback__))
        return task

    # ---------------------------------------------------------------- probe and decision (section 2.0)
    async def probed(self, version):
        """The probe of `version`, twice a minute apart when it fails; the result of the last one, with "same"
        True when both failed at the same step for the same kind of reason."""
        first = await self.probe.run(cc_health.binary())
        if first.get("ok") is not False:
            return first
        await asyncio.sleep(RETRY_SECS)
        second = await self.probe.run(cc_health.binary())
        if second.get("ok") is False:
            second["same"] = second.get("step") == first.get("step") and \
                kind_of(second.get("why")) == kind_of(first.get("why"))
        return second

    async def suspect(self, version):
        """tmux suspected: the probe, twice; the same failure both times (and not the login or the limit) breaks
        "tmux" on this machine. Print channels are not touched (manager 11-07: each way on its own)."""
        if version in self.busy or cc_health.probed_lately(version) or "tmux" in cc_health.broken() or self.probe is None:
            return
        self.busy.add(version)
        try:
            got = await self.probed(version)
            cc_health.mark_probed(version, {k: got.get(k) for k in ("ok", "step", "why", "same")})
            await self.audit("C-162：Claude Code %s 探測%s" % (version, "通過" if got.get("ok") else (
                "沒辦法跑（%s）" % got.get("why") if got.get("ok") is None else "失敗（第 %s 步：%s）" % (got.get("step"), got.get("why")))))
            if got.get("ok"):
                cc_health.forget_failures(version)
            elif got.get("ok") is False and got.get("same"):
                other = cc_health.not_the_version(got.get("screen", ""))
                if other:  # (section 2.1: the probe too shows the usage limit or the login: their own way, not this)
                    await self.audit("C-162：探測失敗，但不是版本的問題（%s），不降級" % other)
                elif not await self.fall_back(version, got.get("why")):  # section 2.0 step 1: the older version first
                    await self.down("tmux", version, got.get("why"))
        finally:
            self.busy.discard(version)

    async def suspect_json(self, version):
        """stream-json suspected (print turns with no result though claude exited 0): one print turn in the probe's
        folder (Probe.print_check); it failing too breaks "json" (print channels then run text)."""
        if "json" in cc_health.broken() or ("json", version) in self.busy or self.probe is None or \
                cc_health.probed_lately(version, key="json_probed"):  # (30 minutes, as the probe: #code-review-2)
            return
        self.busy.add(("json", version))
        try:
            got = await self.probe.print_check(cc_health.binary())
            cc_health.mark_probed(version, {k: got.get(k) for k in ("ok", "why")}, key="json_probed")
            await self.audit("C-162：Claude Code %s 簡易模式檢查%s" % (version, "通過" if got.get("ok") else "失敗（%s）" % got.get("why")))
            if got.get("ok"):
                cc_health.forget_failures(version)
            elif got.get("ok") is False and not cc_health.not_the_version(got.get("why", "")):
                await self.down("json", version, got.get("why"))
        finally:
            self.busy.discard(("json", version))

    async def suspect_start(self, version, why):
        """Print turns exited non-zero: one print turn of this version in the probe's folder first (every step probed
        first, #code-review on 0c0b58d: a conversation file gone or a folder deleted exits 1 too); only it failing,
        for none of the other reasons, tries the older version."""
        if ("start", version) in self.busy or self.probe is None or cc_health.probed_lately(version, key="start_probed"):
            return
        self.busy.add(("start", version))
        try:
            got = await self.probe.print_check(cc_health.binary())
            cc_health.mark_probed(version, {k: got.get(k) for k in ("ok", "why")}, key="start_probed")
            await self.audit("C-162：Claude Code %s 起不來的檢查%s" % (version, "通過" if got.get("ok") else "失敗（%s）" % got.get("why")))
            if got.get("ok"):
                cc_health.forget_failures(version)
            elif got.get("ok") is False and not cc_health.not_the_version(got.get("why", "")):
                await self.fall_back(version, why, check="print")
        finally:
            self.busy.discard(("start", version))

    async def fall_back(self, version, why, check="tmux"):
        """Section 2.0 step 1 and 3.2: Claude Code `version` cannot be used; cc_last_good (its kept copy, verified)
        is another version and passes the same check (`check`: the probe, or a print turn): run it instead (the
        pin), say so once. True when pinned."""
        good = await off_loop(cc_health.last_good, check=True)  # (sha256 of a 300 MB file: not on the event loop)
        if good is None or good["version"] == version or cc_health.pin() is not None or self.probe is None:
            return False
        got = await self.probe.run(good["path"])  # the probe in every case: the pin runs every window with it too
        if got.get("ok") and check == "print":   # (#code-review-2 on 0c0b58d); and a print turn when print broke
            got = await self.probe.print_check(good["path"])
        if not got.get("ok"):
            await self.audit("C-162：退回用的 Claude Code %s 也不能用（%s）" % (good["version"], got.get("why")))
            return False
        cc_health.set_pin(good, version)
        self.bridge.reopen_off(good["version"])  # windows running the version that broke: reopened on the kept one
        head = "%s %s" % (messages.cc("pinned", new=version, old=good["version"]), calls.mention())
        await calls.system(self.bridge.notify_channel, "cc:pin", version, head.strip())
        await self.audit("C-162：Claude Code %s 不能用（%s），機器人改用 %s" % (version, why, good["version"]))
        return True

    def worded_down(self, kind, version):
        """The words of a way that broke, as the channels now run: tmux broken (print channels too → text)."""
        got = cc_health.broken()
        if kind == "tmux":
            return messages.cc("down_text" if "json" in got else "down_print", version=version)
        return messages.cc("down_text" if "tmux" in got else "down_json", version=version)

    async def down(self, kind, version, why):
        if kind in cc_health.broken():
            return
        cc_health.set_broken(kind, version)
        held = self.bridge.held_channels()
        alone = kind == "json" and "tmux" not in cc_health.broken()  # print channels only, and they still answer:
        # nothing for the owner to do, no mention (C-103; #release 11-07)
        head = self.worded_down(kind, version) + ("" if alone else " " + calls.mention())
        more = ([] if alone else [messages.cc("missing")]) + ([calls.channels_text(held)] if held else [])
        await calls.system(self.bridge.notify_channel, "cc:" + kind, version, head.strip(), more)
        await self.audit("C-162：Claude Code %s 的%s壞了（%s）；頻道：%s" % (
            version, "互動畫面" if kind == "tmux" else "簡易模式（stream-json）", why, "、".join("#%s" % c for c in held) or "沒有"))

    # ---------------------------------------------------------------- the way back (section 2.3)
    async def maybe_back(self):
        """Called when a channel held down runs a turn, and by tick(): a Claude Code version other than the one that
        broke a way, or the one the pin stands in for, is checked (at most every VERSION_SECS; a version checked
        lately and failed, not again for 30 minutes). Pinned: the installed one (cc_health.installed()) is what is tried."""
        pinned = cc_health.pin()
        if not ACTIVE or not (cc_health.broken() or pinned) or self.probe is None:
            return
        now = time.time()
        if now - self.version_seen[0] < VERSION_SECS:
            return
        if pinned:
            version = cc_health.version_of(cc_health.installed())
            self.version_seen = (now, version)
            if version and version != pinned.get("from") and version != pinned.get("version") and \
                    version not in self.busy and not cc_health.probed_lately(version):
                self.spawn(self.try_unpin(version))
            return
        version = self.version()
        self.version_seen = (now, version)
        broke = set(v.get("version") for v in cc_health.broken().values())
        if not version or version in broke or version in self.busy or cc_health.probed_lately(version):
            return
        self.spawn(self.try_back(version))

    async def try_unpin(self, version):
        """The installed Claude Code `version` (not the one pinned against) passes the probe and a print turn: the
        pin goes, the channels run it (section 3.3: a new version that passes lets it go)."""
        self.busy.add(version)
        try:
            got = await self.probe.run(cc_health.installed())
            cc_health.mark_probed(version, {k: got.get(k) for k in ("ok", "step", "why")})
            if got.get("ok"):
                got = await self.probe.print_check(cc_health.installed())
        finally:
            self.busy.discard(version)
        if got.get("ok"):
            await self.unpin(version)
        return got

    async def unpin(self, version):
        cc_health.clear_pin()
        await calls.over(self.bridge.notify_channel, "cc:pin", messages.cc("back", version=version))
        await self.audit("C-162：Claude Code %s 檢查通過，機器人不再停在舊版" % version)

    async def tick(self, now=None):
        """Every few minutes once started: the way back (maybe_back), a new Claude Code version drilled (hourly look,
        section 5.2), the kept copy's sha256 once a day while it is pinned (a copy that differs is no pin), and the
        reminder of a pin held too long (section 3.3)."""
        now = now or time.time()
        await self.maybe_back()
        if self.drilling is None or self.drilling.done():  # on a task of its own: up to 30 minutes, and the rest of
            self.drilling = self.spawn(self.new_version(now))  # the tick goes on (#code-review on f743a61)
        pinned = cc_health.pin()
        if not pinned:
            return
        if now - self.sha_checked > 86400:
            self.sha_checked = now
            good = cc_health.last_good()
            if good is None or good["path"] != pinned["path"] or await off_loop(cc_health.last_good, check=True) is None:
                cc_health.clear_pin()
                await self.audit("C-162：留下的 Claude Code %s 跟當初記下的不一樣，不再用它" % pinned.get("version"))
                return
        days = cc_health.pin_days()
        if now - pinned.get("since", now) > days * 86400 and not pinned.get("reminded"):
            cc_health.pin_reminded(now)
            await self.remind(pinned, days)

    async def new_version(self, now):
        """Section 5.2: once an hour, a version installed that has not been drilled is (bot.cc_drill --json: the
        bot's way down and back against it, its screen and its stream-json, a fake API, no login, no usage). #audit
        says how it went and that !cctest updates the way back; failing twice in a row, the kept version (if any)
        takes over before a channel meets the new one."""
        if now - self.versions_looked < VERSIONS_SECS:
            return
        self.versions_looked = now
        version = cc_health.version_of(cc_health.installed())
        rec = cc_health.entry().get("drilled") if isinstance(cc_health.entry().get("drilled"), dict) else {}
        if not version or rec.get("version") == version and (rec.get("ok") or rec.get("tries", 0) >= 2):
            return
        if cc_health.busy_machine():  # (minutes of CPU and a few Claude Codes: not on a machine short of room; next hour)
            return
        got = await drill()
        if got.get("busy"):  # (the last drill still runs: looked at again next hour, not counted)
            return
        itself = bool(got.get("screen")) and bool(got.get("print"))  # step 0: this Claude Code itself (the other
        # steps rehearse the bot's own decisions with stand-ins, and step 10 measures the older version: #code-review)
        tries = (rec.get("tries", 0) if rec.get("version") == version else 0) + 1
        cc_health.note_drilled(version, itself, tries)
        mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
        await self.audit(messages.cc("drilled", version=version, start=mark(got.get("started")), screen=mark(got.get("screen")),
                                     print=mark(got.get("print"))) + (
                         "" if got.get("ok") else "（%s）" % (got.get("why") or "演練有一項沒接住")))
        if not itself and tries >= 2:
            await self.confirm_new(version, got)

    async def confirm_new(self, version, got):
        """The new version failed the drill's own step twice (its screen or stream-json, on a fake API): checked
        with the real login before it counts (the fake API may lack something it asks for: #code-review on f743a61);
        failing that too, for none of the other reasons, the kept version takes over."""
        if self.probe is None:
            return
        real = {"ok": True}
        if not got.get("screen"):
            real = await self.probe.run(cc_health.installed())
        if real.get("ok") and not got.get("print"):
            real = await self.probe.print_check(cc_health.installed())
        await self.audit("C-162：Claude Code %s 用真的連線再檢查一次：%s" % (version, "通過" if real.get("ok") else real.get("why")))
        if real.get("ok") is False and not cc_health.not_the_version(real.get("screen") or real.get("why") or ""):
            await self.fall_back(version, real.get("why") or "新版的檢查沒過", check="print")

    async def remind(self, pinned, days):
        try:
            ch = await self.bridge.notify_channel()
            if ch is None:
                return
            import discord
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label=messages.cc("pin_keep_button", days=days), custom_id="ccpin:keep"))
            view.add_item(discord.ui.Button(label=messages.cc("pin_new_button"), custom_id="ccpin:new"))  # (PIN_*)
            text = "%s %s" % (messages.cc("pin_long", old=pinned.get("version"), days=days), calls.mention())
            msg = await deliver.deliver(ch, text, view=view, allowed_mentions=calls.allowed(), intent="card", key="ccpin")
            cc_health.pin_card(ch.id, msg.id)  # (C-187: --waiting lists it there, --answer settles it)
        except Exception:
            traceback.print_exc()

    async def on_interaction(self, interaction):
        """The two buttons of the reminder (custom ids carry everything: they work after a restart). Only the owner."""
        from . import config as cfg
        cid = (interaction.data or {}).get("custom_id")
        if cid not in (PIN_KEEP, PIN_NEW):
            return False
        if interaction.user.id != cfg.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        await interaction.response.edit_message(view=None)
        said = await self.choose_pin(cid == PIN_KEEP)
        if said:
            await interaction.followup.send(said)
        return True

    def pin_choices(self, pinned=None):
        """The reminder's two answers, as its buttons read: (keep, new)."""
        return messages.cc("pin_keep_button", days=cc_health.pin_days()), messages.cc("pin_new_button")

    async def choose_pin(self, keep, by=None, why=None, mark=""):
        """The reminder answered: keep the old version (asked again in pin_days) or go to the new one in print
        mode. `by`: another channel's Claude ("#x 的 Claude"; `mark`: looked up or said, for #audit) (C-187, manager 11-12: what the owner may do an agent may, with a why,
        said in #audit, and how to change it back); None: the owner's button. The words for the channel, or None
        when nothing is pinned any more."""
        pinned = cc_health.pin()
        if pinned is None:
            return None
        if keep:
            cc_health.pin_reminded(None, since=time.time())
            said = messages.cc("pin_kept", old=pinned.get("version"), days=cc_health.pin_days())
        else:
            cc_health.clear_pin()
            cc_health.set_broken("tmux", pinned.get("from"))
            said = messages.cc("pin_left", new=pinned.get("from"))
        chose = "繼續停" if keep else "改用新版的簡易模式"
        if by is None:
            await self.audit("C-162：停在舊版的提醒，使用者選了%s" % chose)
            return said
        await self.audit("C-162：停在舊版的提醒，%s%s 選了%s（為什麼：%s）" % (by, mark, chose, why or "沒寫"))
        return "%s\n-# %s" % (said, messages.cc("pin_by_keep" if keep else "pin_by_new", by=by))

    async def cctest(self, say):
        """`!cctest` (section 5.1 B, by hand only): the installed Claude Code checked with the real login, Haiku: the
        probe (tmux, --resume), a stream-json turn, a text turn. All passing makes it cc_last_good (its copy kept), and
        lets a pin go. The words for the channel."""
        version = cc_health.version_of(cc_health.installed()) or "?"
        if self.probe is None:
            return self.failed_words(version, "沒有探測可以用")
        await say(messages.cc("cctest_start", version=version))
        failed = []
        got = await self.probe.run(cc_health.installed())
        if not got.get("ok"):
            failed.append("一般模式：%s" % (got.get("why") or "原因不明"))
        for name, check in (("簡易模式", self.probe.print_check), ("純文字模式", self.probe.text_check)):
            got = await check(cc_health.installed())
            if not got.get("ok"):
                failed.append("%s：%s" % (name, got.get("why") or "原因不明"))
        await self.audit("C-162：!cctest Claude Code %s：%s" % (version, "通過" if not failed else "；".join(failed)))
        if failed:
            return self.failed_words(version, "；".join(failed))
        got = await off_loop(cc_health.keep_copy, version, cc_health.installed())  # (copied and hashed: not on the loop)
        why = got if isinstance(got, str) else None
        if why is None:
            cc_health.keep_record(version, *got)  # (cc_health.json written on the loop, as everything else writes it)
        pinned = cc_health.pin()
        if pinned and version != pinned.get("version"):
            await self.unpin(version)
        if why:
            await self.audit("C-162：Claude Code %s 沒有留下備份（%s）" % (version, why))
            return messages.cc("cctest_ok_nokeep", version=version, why=why)
        return messages.cc("cctest_ok", version=version)

    def failed_words(self, version, what):
        good = cc_health.last_good()
        if good:
            return messages.cc("cctest_fail", version=version, what=what, good=good["version"])
        return messages.cc("cctest_fail_none", version=version, what=what)

    async def try_back(self, version):
        """Check every broken way with `version`; those that pass are let go. What failed: {kind: why}."""
        self.busy.add(version)
        failed = {}
        try:
            for kind in list(cc_health.broken()):
                got = await (self.probe.run(cc_health.binary()) if kind == "tmux" else self.probe.print_check(cc_health.binary()))
                if kind == "tmux":
                    cc_health.mark_probed(version, {k: got.get(k) for k in ("ok", "step", "why")})
                if got.get("ok"):
                    await self.back(kind, version)
                else:
                    failed[kind] = got.get("why") or "原因不明"
        finally:
            self.busy.discard(version)
        return failed

    async def back(self, kind, version):
        cc_health.clear_broken(kind)
        await calls.over(self.bridge.notify_channel, "cc:" + kind,
                         messages.cc("back" if kind == "tmux" else "back_json", version=version))
        await self.audit("C-162：Claude Code %s 檢查通過，%s回來了" % (version, "互動畫面" if kind == "tmux" else "簡易模式"))

    async def retry(self, say=None):
        """`!mode retry`: every broken way checked now (C-162 section 2.3); `say` is told first that it is being
        tried (it takes minutes). The words for the channel."""
        if not cc_health.broken():
            return messages.cc("retry_free")
        version = self.version() or "?"
        mode = messages.cc("mode_" + cc_health.effective("tmux", {}))
        if self.probe is None or not ACTIVE:
            cc_health.clear_broken()
            return messages.cc("retry_held")
        if say is not None:
            await say(messages.cc("retry_trying"))
        failed = await self.try_back(version)
        if not failed:
            return messages.cc("retry_held") + messages.cc("retry_held_after", mode=mode)
        return messages.cc("retry_failed_json" if list(failed) == ["json"] else "retry_failed", version=version,
                           why="；".join(failed.values()), mode=messages.cc("mode_" + cc_health.effective("tmux", {})))

    async def audit(self, text):
        try:
            await self.bridge.host.audit(text)
        except Exception:
            traceback.print_exc()


async def drill():
    """python -m bot.cc_drill --json in a process of its own (minutes, CPU): {"ok", "screen", "print", "why"}."""
    import json
    import os
    import sys
    dcbot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "bot.cc_drill", "--json", cwd=dcbot,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), DRILL_SECS)
    except asyncio.TimeoutError:
        proc.terminate()  # its SIGTERM closes its world (scratch HOME, tmux server); killed only if it does not end
        try:
            await asyncio.wait_for(proc.wait(), 20)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return {"ok": False, "why": "演練超過 %d 分鐘" % (DRILL_SECS // 60)}
    except OSError as e:
        return {"ok": False, "why": "演練跑不起來（%s）" % e}
    for line in reversed(out.decode("utf-8", "replace").splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False, "why": cc_health.tail(err.decode("utf-8", "replace"), 2) or "演練沒有結果（結束碼 %s）" % proc.returncode}


async def off_loop(fn, *args, **kw):
    """`fn` in a thread (a copy and sha256 of a Claude Code file take a while, on NFS more: #code-review on 0c0b58d)."""
    import functools
    return await asyncio.get_running_loop().run_in_executor(None, functools.partial(fn, *args, **kw))


def kind_of(why):
    """What kind of reason a probe failed for, to tell "the same" from another: it ended at once, or a screen not
    known, or no reply, or else its words."""
    why = why or ""
    for word in ("一開就結束", "找不到輸入框", "沒有得到回覆", "答不出"):
        if word in why:
            return word
    return why
