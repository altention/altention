"""The look (C-201 first batch, design instance docs/refactor/c201-design.md section 2 "對帳"; manager 11-17): whether
each channel's pane is where its record (bot/where.py) says, once the bot is online and every LOOK_SECS after, in a
task of its own (never in the pump's way; the looking LOOK_CAP seconds at most in all; what it found is written and
said after, so a look cut short loses nothing it would have said).

Each machine with a channel's pane on it is looked at (every machine the bot reaches only at the first look): this
user's own tmux servers there (tmuxio.look_sockets(): only this uid's socket folder; on another machine dcremote
look), read only but for the channel tag (@altention_channel). The record decides (manager 11-17: the tag follows the
binding, routes.bind_any() writes it and unbind_any() takes it away): the pane at the record's number is there when
its process is the recorded one (a session on the bot's own server: by its name, which holds the channel's ID), and
tagged for the channel if it is not; with no process recorded it is "unsure" (never tagged by its number alone, doctor
lists it). A tag that says a channel whose record points elsewhere is stale: taken away and said in #audit, and
nobody's pane is "gone" for it. A pane plainly not there (its server's socket gone, no such server or pane, its number
another process) is gone: said once in its channel and in #audit (a whole machine's in one #audit line), and once more
when it is back. A machine that cannot be reached, does not answer in time, or a server that cannot be read: not
known, never "gone". A machine's first look (nothing known there before) says what is gone in one #audit line only (the old records
nobody looked at: 11 of 27 routes on 11-17; #code-review on 265dc930: per machine, so one never reached till now
does not keep the others quiet); a session of the bot's own not there was closed by the bot (it opens it again with the
next message): written, not said. A session renamed or a window named in tmux is said in #audit."""
import asyncio
import json
import time
import traceback

from . import deliver, messages, remote, rename, routecore, tmuxio, util, where

LOOK_SECS = 120  # between two looks (design: 2 minutes)
UNKNOWN_LOOKS = 3  # C-198 (manager 11-18): a respawned pane whose record could not be read this many looks in a row
# is gone (said once), not "being checked" for ever
LOOK_CAP = 60  # seconds a whole look may take; past it, this one is dropped (the next one comes)
REMOTE_SECS = 20  # another machine's answer
TOPIC_SECS = 600  # an attached channel's topic made again at most once in this long (manager 11-17)


def entry_of(sockets, sock, local):
    """(socket key, what the look found of it) for a record's socket: as written, or the same socket by another
    spelling (macOS's /private/tmp; on this machine links followed: util.same_socket()); (sock, None) when the look
    did not find it (its server not there)."""
    if sock in sockets:
        return sock, sockets[sock]
    for k, v in sockets.items():
        if util.same_socket(k, sock, local):
            return k, v
    return sock, None


def window_of(p):
    return p.get("window_name") or p.get("window")



def shared_key(machine, socket, pane, here):
    """(machine, socket, pane) as a key of Looks.shared: the socket in one spelling (util.socket_key(), the way
    util.same_socket() compares: #code-review on d4ca3f00)."""
    return machine, util.socket_key(socket, machine == here), pane

def renamed_by_bot(old, new, bots_name):
    """The window the bot named itself (rename.py's do_window(), kept in `named`): the same pane, the same session,
    only the window's name now the bot's. Not a move (drill of d0f26f25, B: "pi:0 → pi:演練-pi" in #audit)."""
    return bool(bots_name) and new.get("window") == bots_name and old.get("session") == new.get("session") \
        and (old.get("machine"), old.get("pane")) == (new.get("machine"), new.get("pane"))


class Looks(object):
    def __init__(self, bot):
        self.bot = bot
        self.looked = False  # a first look done (every machine the bot reaches; the old records said once)
        self.topic_at = {}  # channel id -> when its topic was last made again (topics())
        self.last_found = {}  # machine -> its sockets as the last look read them (None: not known), for repair()
        self.busy = False
        self.shared = {}  # (machine, socket, pane) two records or more point to: neither routed nor tagged by a look
        self.shared_said = set()  # those said in #audit (once while they last)
        self.route_hosts = set()  # machines whose route tables repair() reads again though no record points there now
        self.renames = []  # [(channel id, window name)] the owner named in tmux at this look: taken along after it (2b)
        self.respawned = []  # [channel id] whose pane number runs another process tagged for it: asked after (C-198)
        self.not_followed = set()  # (channel id, pid) the bridge did not follow: gone from then on, not asked again
        self.not_known = {}  # (channel id, pid): looks in a row pane_again() could not tell (at most UNKNOWN_LOOKS)

    async def run(self):
        """The loop: a look now, then every LOOK_SECS; a look that breaks or runs too long is dropped."""
        while True:
            await self.once()
            await asyncio.sleep(LOOK_SECS)

    async def once(self):
        if self.busy or (getattr(self.bot, "lease", None) is not None and not self.bot.leader_ok()):
            return  # (one at a time; a standby that is not leading looks at nothing)
        self.busy = True
        try:
            try:
                found = await asyncio.wait_for(self.look(), LOOK_CAP)
            except asyncio.TimeoutError:
                print("[look] a look ran past %d seconds: dropped" % LOOK_CAP, flush=True)
                return
            await self.tell(*self.write(found))  # (outside the cap: what was found is never half said)
            await self.topics()
            await self.repair(found)
            await self.follow_names()
            await self.follow_respawned()
        except Exception:
            print("[look] a look broke:", flush=True)
            traceback.print_exc()
        finally:
            self.busy = False

    async def machine(self, host, here):
        """{socket: …} as tmuxio.look_sockets() says it, for this machine or another (dcremote look); None when not
        known (not reached, no answer in time, an older dcremote that has no look, an answer that is not one)."""
        if host == here:
            got = await tmuxio.look_sockets()
        else:
            code, out = await remote.dcremote(host, "look", timeout=REMOTE_SECS)
            if code != 0:
                return None
            try:
                got = json.loads(out)
            except ValueError:
                return None
        if not isinstance(got, dict) or isinstance(got.get("error"), str):  # (its socket folder not readable)
            return None
        return {k: v for k, v in got.items() if isinstance(v, dict)}

    async def look(self):
        """Look at the machines and judge each channel; tags written or taken away as it goes. (results, stale tags
        taken away) -- nothing written to the channels' state, nothing said yet (write(), tell())."""
        bot = self.bot
        bot.save()  # (each channel's `where` as its fields are now)
        here, bot_socket = bot.where_context()
        recs = {cid: st["where"] for cid, st in bot.channels_state.items()
                if isinstance(st, dict) and isinstance(st.get("where"), dict) and (st["where"].get("place") or {}).get("socket")}
        hosts = {here} | {r["place"].get("machine") or here for r in recs.values()}
        if not self.looked:
            try:
                hosts |= set(remote.hosts())
            except Exception:
                traceback.print_exc()
        hosts = sorted(hosts)
        seen = await asyncio.gather(*(self.machine(h, here) for h in hosts), return_exceptions=True)
        found = {h: (s if isinstance(s, dict) else None) for h, s in zip(hosts, seen)}
        self.last_found = found
        self.shared = self.sharing(recs, here)
        now, results = time.time(), []
        for cid, rec in recs.items():
            machine = rec["place"].get("machine") or here
            new = await self.judge(cid, rec, found.get(machine), bot_socket, machine != here and machine)
            results.append((cid, dict(new, at=now)))
        stale = await self.stale(found, recs, here, bot_socket)
        return now, results, stale

    def write(self, looked):
        """What the look found written into each channel's where.seen (no await: all or nothing); (audit lines,
        [(channel id, words)]) to say."""
        bot = self.bot
        now, results, stale = looked
        known_on = {((st.get("where") or {}).get("seen") or {}).get("machine") for st in bot.channels_state.values()
                    if isinstance(st, dict) and isinstance(st.get("where"), dict)
                    and ((st["where"].get("seen") or {}).get("known"))}  # (machines a look has known something on)
        audits, said, gone_by_machine, first_gone = [], [], {}, []
        for cid, new in results:
            st = bot.channels_state.get(cid)
            if not isinstance(st, dict) or not isinstance(st.get("where"), dict):
                continue  # (gone from the state meanwhile)
            rec, name = st["where"], bot.channel_name_of(cid) or cid
            old = rec.get("seen") or {}
            known = old.get("known")
            if new["state"] in ("here", "gone", "closed"):
                new["known"] = new["state"]
            elif known:
                new["known"] = known
            new["here_at"] = now if new["state"] == "here" else old.get("here_at")
            if new["here_at"] is None:
                new.pop("here_at")
            rec["seen"] = new
            bots_name = (st.get("named") or {}).get("window") if isinstance(st.get("named"), dict) else None  # (before
            # tmux_named() below keeps an owner's name there too)
            rec = where.put(st, bot.channel_name_of(cid), ctx=bot.where_context()) or rec  # (its place takes what was
            # seen now: else the next save's stamp() said it was written past put(): #code-review-2 on 1a4cad50)
            if new["state"] == "here":
                self.follow(st, new, bot.where_context())
                rec = st.get("where") or rec  # (made again by put())
                place, here = rec.get("place") or {}, bot.where_context()[0]
                shared = shared_key(place.get("machine") or here, place.get("socket"), place.get("pane"), here) in self.shared
                named = None if shared else rename.tmux_named(st, old, new, bot.channel_name_of(cid))  # (C-201 2b,
                # point 3; a pane two records point to: whose name it is, not known, as 2a's routes and tags)
                if named:
                    self.renames.append((cid, named))
            pane = (rec.get("place") or {}).get("pane") or (rec.get("place") or {}).get("session")
            if new["state"] == "here" and known == "gone":
                said.append((cid, messages.reconcile("back", machine=new.get("machine"), pane=new.get("pane"))))
                audits.append(messages.reconcile("audit_back", channel=name, machine=new.get("machine"), pane=new.get("pane")))
            elif new["state"] == "here" and known == "here" and (old.get("session"), old.get("window")) != (new.get("session"), new.get("window")) \
                    and (old.get("session") or old.get("window")) is not None and not renamed_by_bot(old, new, bots_name):
                # (a place cleared to None when the pane was restarted is no move: C-198 L3 drill 11-20, low)
                audits.append(messages.reconcile("audit_moved", channel=name, old="%s:%s" % (old.get("session"), old.get("window")),
                                                 new="%s:%s" % (new.get("session"), new.get("window"))))
            elif new["state"] == "gone" and known != "gone":
                if not known and new.get("machine") not in known_on:  # (that machine's first look: its old records)
                    first_gone.append("%s（%s）" % (name, pane))
                else:
                    gone_by_machine.setdefault(new.get("machine"), []).append("%s（%s）" % (name, pane))
                    said.append((cid, messages.reconcile(
                        "gone", machine=new.get("machine"), pane=pane,
                        at=self.clock(old["here_at"]) if old.get("here_at") else messages.reconcile("never"),
                        what=messages.reconcile("what_window" if st.get("home") else "what_attached"))))
        for machine, rows in sorted(gone_by_machine.items()):
            audits.append(messages.reconcile("audit_gone", machine=machine, n=len(rows), channels="、".join(rows)))
        if first_gone:
            audits.append(messages.reconcile("audit_first", n=len(first_gone), channels="、".join(first_gone)))
        if stale:
            audits.append(messages.reconcile("audit_stale", n=len(stale), panes="、".join(stale)))
        self.looked = True
        bot.save()
        for key in sorted(set(self.shared) - self.shared_said):
            names = ["#%s" % (bot.channel_name_of(c) or c) for c in self.shared[key]]
            audits.append(messages.reconcile("audit_shared", machine=key[0], pane=key[2], channels="、".join(names)))
        self.shared_said = set(self.shared)  # (said again only if it ends and comes back)
        return audits, said

    async def follow_respawned(self):
        """C-198: a channel's pane respawned (its tag kept, its process new) followed or not by the bridge's
        pane_again() (the conversation when it can be read, else the tag; said there); one not followed is gone from
        the next look on (the pid remembered), never asked again for that process; one not known (None: its record
        not read) is asked again at the next look, UNKNOWN_LOOKS in a row at most, then gone too."""
        todo, self.respawned = self.respawned, []
        for cid in todo:
            st = self.bot.channels_state.get(cid)
            ch = self.bot.get_channel(int(cid)) if cid.isdigit() else None
            if ch is None or not isinstance(st, dict) or not isinstance(st.get("pane"), dict):
                continue
            pid = (((st.get("where") or {}).get("seen") or {}).get("pid"))
            try:
                followed = await self.bot.tmux.pane_again(ch, st)
            except Exception:
                traceback.print_exc()
                followed = None
            key = (cid, str(pid))
            if followed is None:  # its record not read: asked again next look, at most UNKNOWN_LOOKS in a row
                self.not_known[key] = self.not_known.get(key, 0) + 1
                if self.not_known[key] < UNKNOWN_LOOKS:
                    continue
            self.not_known.pop(key, None)
            if not followed:
                self.not_followed.add(key)

    @staticmethod
    def follow(st, seen, ctx):
        """C-201 2a (manager 11-17, point 1): a session renamed or a window named in tmux, of the very pane the record
        says (the look found it there, surely its): the old fields take the names it has now (where.put(), the record
        made again with them), so whatever reads them (opening its window again, !detach's record) goes by them."""
        place = (st.get("where") or {}).get("place") or {}
        if not seen.get("pane") or seen.get("pane") != place.get("pane") or seen.get("machine") != place.get("machine") \
                or not seen.get("session") or not st.get("pane"):
            return
        fields = {}
        home = st.get("home") if isinstance(st.get("home"), dict) else None
        if home is None or st.get("pane_session"):
            if st.get("pane_session") != seen["session"]:
                fields["pane_session"] = seen["session"]
        if home is not None:
            moved = dict(home, session=seen["session"])
            if home.get("window") and seen.get("window") and seen.get("window") != seen.get("index"):
                moved["window"] = seen["window"]  # (a window it was opened by name in, named anew; tmux's own name: not taken)
            if moved != home:
                fields["home"] = moved
        if fields:
            where.put(st, ctx=ctx, **fields)

    async def topics(self):
        """An attached channel's topic says its tmux session and window (routes.topic(), written by /attach): made
        again when the look found them otherwise (manager 11-17, point 1), a topic the owner wrote left alone (only
        one in the bot's own words), at most once in TOPIC_SECS a channel (Discord limits topic changes)."""
        from . import routes
        now = time.time()
        for cid, st in list(self.bot.channels_state.items()):
            rec = st.get("where") if isinstance(st, dict) else None
            seen = (rec or {}).get("seen") or {}
            if not rec or rec.get("owned") != "user" or seen.get("state") != "here" or not st.get("cwd"):
                continue
            ch = self.bot.get_channel(int(cid)) if str(cid).isdigit() else None
            topic, machine = getattr(ch, "topic", None), (rec.get("place") or {}).get("machine")
            if ch is None or not topic or not machine:
                continue
            want = routes.topic(machine, {"session": seen.get("session"), "window": seen.get("index") or seen.get("window"),
                                          "cwd": st["cwd"]})
            if topic == want or not routes.topic_of_attach(topic, machine, st["cwd"]):
                continue
            if now - self.topic_at.get(cid, 0) < TOPIC_SECS:
                continue  # (the next look after that does it)
            self.topic_at[cid] = now
            try:
                await ch.edit(topic=want)
            except Exception:
                traceback.print_exc()

    async def follow_names(self):
        """C-201 2b (owner 11-17, point 2 a): a window the owner named in tmux takes the channel, its conversation and
        (the bot's own) session along; once (rename.tmux_named() kept the name), nothing retried."""
        renames, self.renames = self.renames, []
        renamer = getattr(self.bot, "renamer", None)
        for cid, name in renames:
            if renamer is None:
                continue
            try:
                await renamer.follow_tmux(cid, name)
            except Exception:
                traceback.print_exc()

    async def routes_of(self, host, here):
        """A machine's route table ({key: entry}), or None when not known (not reached, an older dcremote)."""
        if host == here:
            return routecore.load()
        code, out = await remote.dcremote(host, "routes", timeout=REMOTE_SECS)
        if code != 0:
            return None
        try:
            got = json.loads(out)
        except ValueError:
            return None
        return got if isinstance(got, dict) else None

    async def repair(self, looked):
        """The route tables follow the records (C-201 2a; manager 11-17, point 2), on facts only, each repair one
        #audit line (machine, pane, from → to):
        - a channel's pane the look surely found (here), with the recorded process, routed elsewhere or nowhere: bound
          to it (only a pane the hooks find by the table: an attached one, or a window the bot opened on another
          machine);
        - a route whose pane the channel's record does not point to, taken away only when that pane is surely not
          its: the pane plainly not there, another process in it than the route's, or tagged for another channel;
          or the channel it goes to records no pane at all now (a !detach that let go of the route while this look
          read the tables, the route bound again: #code-review-2 on 1a4cad50), a window the bot opens whose pane is
          not known yet (home or tmux, no pane) left alone. A machine or server not known, a route with no process,
          a channel not in the state: left as they are."""
        bot = self.bot
        if getattr(bot, "lease", None) is not None and not bot.leader_ok():
            return  # (a failback may have come while topics() waited on Discord: #code-review-2 on 1a4cad50)
        here, bot_socket = bot.where_context()
        now, results, _ = looked
        machines = sorted({r.get("machine") or here for _, r in results} | {here} | self.route_hosts)
        got = await asyncio.gather(*(self.routes_of(h, here) for h in machines), return_exceptions=True)
        tables = {h: (t if isinstance(t, dict) else None) for h, t in zip(machines, got)}
        sockets_of = self.last_found
        lines, bound_on, left_on = [], set(), set()
        for cid, seen in results:
            if seen.get("state") != "here" or not seen.get("pane"):
                continue
            st = bot.channels_state.get(cid)
            pane = st.get("pane") if isinstance(st, dict) else None
            machine = seen.get("machine") or here
            table = tables.get(machine)
            if table is None or not isinstance(pane, dict) or pane.get("id") != seen["pane"] or not pane.get("pid") \
                    or str(pane["pid"]) != str(seen.get("pid")):
                continue
            if st.get("home") and not st["home"].get("host"):
                continue  # (a window the bot opened here: its hooks find it without the table)
            if shared_key(machine, pane.get("socket"), pane["id"], here) in self.shared:
                continue  # (two records point to it: neither is routed back and forth; said once by write())
            k = "%s|%s" % (pane["socket"], pane["id"])
            if routecore.routed_channel(table, k, pane["pid"]) == str(cid):
                continue
            was = routecore.channel_of(table.get(k))
            try:
                from . import routes
                await routes.bind_any(dict(pane, host=machine) if machine != here
                                      else {f: v for f, v in pane.items() if f != "host"}, cid, tag=False)
            except Exception:
                traceback.print_exc()
                continue
            table[k] = routecore.entry(cid, pane["pid"]) if machine == here else {"channel": str(cid), "pid": str(pane["pid"])}
            bound_on.add(machine)
            lines.append(messages.reconcile("audit_bound", machine=machine, pane=pane["id"], channel=bot.channel_name_of(cid) or cid,
                                            was="#%s" % (bot.channel_name_of(was) or was) if was else messages.reconcile("none")))
        for machine, table in sorted(tables.items()):
            sockets = sockets_of.get(machine)
            if table is None:
                if machine in self.route_hosts:
                    left_on.add(machine)  # (not read this time: read again)
                continue
            for k, v in sorted(table.items()):
                parts, cid = k.split("|"), routecore.channel_of(v)
                if len(parts) != 2 or not cid or not isinstance(v, dict) or not v.get("pid"):
                    continue  # (another machine's key, or no process: nothing to go by)
                sock, pid_ = parts
                st = bot.channels_state.get(str(cid))
                if isinstance(st, dict) and not any(st.get(f) for f in ("pane", "home", "tmux")):
                    why = "unbound_unpaned"  # (a fact of this bot's own state: no server needed)
                else:
                    why = None
                place = self.place_now(cid)
                if why is None and place and (place.get("machine") or here) == machine and place.get("pane") == pid_ \
                        and util.same_socket(place.get("socket"), sock, machine == here):
                    continue  # (the record's own pane: the record says what it is)
                if why is None:
                    _, found = entry_of(sockets, sock, machine == here) if sockets is not None else (None, None)
                    if found is None or "error" in found:
                        continue  # (that machine or server not read: not known)
                    p = next((p for p in found.get("panes") or () if p["id"] == pid_), None)
                    if p is None:
                        why = "unbound_gone"
                    elif str(p["pid"]) != str(v["pid"]):
                        why = "unbound_process"
                    elif p.get("channel") and p["channel"] != cid:
                        why = "unbound_tag"
                    else:
                        continue
                try:
                    if machine == here:
                        gone = routecore.unbind_key(k, cid)
                    else:
                        code, out = await remote.dcremote(machine, "unbind-key", k, cid, timeout=REMOTE_SECS)
                        gone = code == 0 and bool((json.loads(out) if out.strip().startswith("{") else {}).get("unbound"))
                except Exception:
                    traceback.print_exc()
                    continue
                if not gone and why == "unbound_unpaned":
                    left_on.add(machine)
                if gone:
                    lines.append(messages.reconcile("audit_unbound", machine=machine, pane=pid_,
                                                    channel=bot.channel_name_of(cid) or cid, why=messages.reconcile(why)))
        # A machine a route was bound on is read again at the next looks, till nothing there goes to a channel that
        # records no pane: once a !detach has let go of its record, no look goes there (#code-review-2 on d4ca3f00).
        self.route_hosts = (bound_on | left_on) - {here}
        for line in lines:
            await bot.audit(line)

    async def tell(self, audits, said):
        for line in audits:
            await self.bot.audit(line)
        for cid, text in said:  # (in each channel itself, after the record is written)
            ch = self.bot.get_channel(int(cid)) if str(cid).isdigit() else None
            if ch is not None:
                try:
                    await deliver.deliver(ch, text, intent="status")
                except Exception:
                    traceback.print_exc()

    def clock(self, ts):
        from .pauses import clock
        return clock(ts)

    async def judge(self, cid, rec, sockets, bot_socket, host):
        """What the look found of one channel by its record: {state: here | gone | closed | unsure | unknown, machine,
        socket, session, window, pane, pid}; the pane tagged for the channel when it is surely the one (its process
        the recorded one, or its session on the bot's own server)."""
        place = rec["place"]
        out = {"state": "unknown", "machine": place.get("machine")}
        if sockets is None:
            return out
        sock, v = entry_of(sockets, place.get("socket"), not host)
        if v is not None and "error" in v:
            return out
        panes = (v or {}).get("panes") or []
        if place.get("pane"):
            p = next((p for p in panes if p["id"] == place["pane"]), None)
            if p is not None and place.get("pid") and str(p["pid"]) != str(place["pid"]) and p.get("channel") == str(cid) \
                    and (str(cid), str(p["pid"])) not in self.not_followed:
                self.respawned.append(str(cid))  # (respawn-pane: its number and tag kept, its process new; whether it
                # is followed is the bridge's to tell, by the same rules as at the next message: C-198, manager 11-18)
                return dict(out, state="respawned", socket=sock, pane=place["pane"], pid=p["pid"])
            if p is None or (place.get("pid") and str(p["pid"]) != str(place["pid"])):
                return dict(out, state="gone", socket=sock, pane=place["pane"])
            sure = bool(place.get("pid")) or p.get("channel") == str(cid)  # (no process recorded: only its tag says)
        elif sock == bot_socket and place.get("session"):
            p = next((p for p in panes if p["session"] == place["session"]), None)
            if p is None:
                return dict(out, state="closed", socket=sock, session=place["session"])
            sure = True  # (the bot's own session: its name holds the channel's ID)
        else:
            return out  # (a window opened by the bot not found yet by its pane: nothing to compare)
        found = dict(out, socket=sock, session=p["session"], window=window_of(p), index=p["window"], pane=p["id"], pid=p["pid"])
        if not sure:
            return dict(found, state="unsure")  # (never tagged by its number alone: manager 11-17)
        if p.get("channel") != str(cid) and self.same_place(cid, place) and \
                shared_key(place.get("machine") or host or self.bot.where_context()[0], place.get("socket"), p["id"],
                           self.bot.where_context()[0]) not in self.shared:  # (bound
            # elsewhere meanwhile, or two records pointing to it: not tagged here; #code-review on 1a4cad50)
            try:
                await tmuxio.tag(sock, p["id"], cid, host=host or None)
                p["channel"] = str(cid)  # (not stale for stale() below)
            except Exception:
                traceback.print_exc()
        return dict(found, state="here")

    @staticmethod
    def sharing(recs, here):
        """{(machine, socket, pane): [channel ids]} two channels' records or more point to (#code-review on 1a4cad50: each look
        routed and tagged it for one and then the other)."""
        by = {}
        for cid, rec in recs.items():
            place = rec.get("place") or {}
            if place.get("pane"):
                by.setdefault(shared_key(place.get("machine") or here, place.get("socket"), place["pane"], here),
                              set()).add(cid)
        return {k: sorted(v) for k, v in by.items() if len(v) > 1}

    def place_now(self, cid):
        """The channel's place as its state says it at this moment (a binding may have come while the look ran:
        #code-review-2 on 0fff85ee), or {} for a channel with none."""
        st = self.bot.channels_state.get(str(cid))
        here, bot_socket = self.bot.where_context()
        rec = where.record(st, None, here, bot_socket) if isinstance(st, dict) else None
        return (rec or {}).get("place") or {}

    def same_place(self, cid, place):
        now = self.place_now(cid)
        return all(now.get(k) == place.get(k) for k in ("machine", "socket", "pane", "session"))

    async def stale(self, found, recs, here, bot_socket):
        """Tags that say a channel whose record points elsewhere (or that has none): taken away (manager 11-17), on
        the machines read; ["#channel（machine · pane）"] of those taken."""
        out = []
        for host, sockets in sorted(found.items()):
            for sock, v in sorted((sockets or {}).items()):
                for p in v.get("panes") or ():
                    cid = p.get("channel")
                    if not cid:
                        continue
                    place = self.place_now(cid)  # (as it is now: one bound while the look ran is not stale)
                    local = host == here
                    if place and not place.get("pane") and place.get("socket") != bot_socket:
                        continue  # (a window the bot opened, its pane not known yet: by its session, which the owner
                        # may rename, it cannot be told stale; #code-review on 0fff85ee)
                    mine = (place.get("machine") or here) == host and util.same_socket(place.get("socket"), sock, local) and (
                        place.get("pane") == p["id"] if place.get("pane") else place.get("session") == p["session"])
                    if mine:
                        continue
                    try:
                        if await tmuxio.untag(sock, p["id"], cid, host=None if host == here else host):
                            out.append("%s（%s · %s）" % (self.bot.channel_name_of(cid) or cid, host, p["id"]))
                    except Exception:
                        traceback.print_exc()
        return out
