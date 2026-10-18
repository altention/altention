#!/usr/bin/env python3
"""Make machines match the instance (stage 3a.8): on each, update the engine and the instance, install what the
plugins on it need and write the crontab and the hooks: both through the one installer, install.sh (3a.9), so a
machine set up by hand and one set up from here go the same way.

  python3 ops/apply.py host-a                 # one machine
  python3 ops/apply.py all                    # every machine
  python3 ops/apply.py all --dry-run          # what each would change; nothing updated, installed or written
  python3 ops/apply.py host-b --force-move    # see "single-machine plugins" below
  python3 ops/apply.py mac --allow-time chatlog/sync   # passed on to plugins.py apply there (a line's time changed)
  python3 ops/apply.py lap --moved-from ~/old-engine   # the engine moved and its old folder is gone: its lines go too

Where each plugin runs comes from plugin.json and the instance's plugins.json (ops/plugins.py show). A machine where
that cannot be worked out is left alone and said (exit 1); the other machines are done (manager 2026-10-06).

Single-machine plugins (machines: one, and the bot): the machine itself is asked first (plugins.py held); one it
holds already is not a move and nobody else is asked; if it cannot be read, the machine is not done. A plugin it is
to get anew is added only once every other machine on the list has been read to hold none of its lines; machines
that lose one are done first, so their lines go before. Another machine that cannot be reached or read counts as
still holding it: the plugin is not added, and it is said. --force-move adds it anyway, and says the other machine
may still be running it (manager 2026-10-06).

A machine the bot has left (its supervisor lines still there, plugins.py held says "bot") has the bot stopped first
(dcbot/uninstall.sh), then plugins.py apply takes the two lines out (their item's own "on" leaves the
machine out: "elsewhere"). A state/stop someone put there (SUPERVISOR-RESCUE.md) is never removed: the supervisor is
not started, every other step is done, and the exit status is 3. A todo of the installer (a token missing, a file in
a link's place) makes it 2. 1 (something failed) comes before 2, 2 before 3.

--dry-run asks every machine what it holds now and assumes nothing done: in a move, the machine that is to get the
plugin shows "別台還有" (exit 1) until the run for real has taken it off the other one.

Update the controlling machine first: apply.py waits for the installer's last line (ok core.done), which an older
engine on the other machine may not print; it stops there, writing nothing (manager 2026-10-06).

The instance is updated with `ops/instance.py pull --take-refused`: past the settings check the sync holds a broken
upstream back with (the way out when that check is wrong; docs/gotchas.md), as the engine is with home-pull's.

Any step that fails stops that machine there and names the step; plugins.py apply --write runs only after every step
before it went through.
"""
import json
import os
import subprocess
import sys

from common import engine_on, inventory, run_on

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import config  # noqa: E402
import plugins  # noqa: E402

def engine_origin():
    """Where this machine's engine comes from (its origin), for a machine that has none yet. A machine that has one
    keeps its own origin: apply never rewrites it (a fork's or a private copy's stays as it is)."""
    r = subprocess.run(["git", "-C", os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remote", "get-url",
                        "origin"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    return r.stdout.strip()


def clone_from(inv):
    """Where a machine without an engine clones it from: the instance's engine_repo when it names one, else this
    engine's origin; "" when neither."""
    return (inv.get("instance") or {}).get("engine_repo") or engine_origin()



SCRIPT = r'''
H={engine}
mkdir -p "$HOME/.cache" || {{ echo "  ✗ 停在「準備」：建不了 ~/.cache"; exit 1; }}
step() {{  # a step's name, the line it prints once it really ran ("" for none), then its command: run reading
  # /dev/null (not this script), its output indented; a failure, or 0 without that line (something else of the same
  # name ran instead and did nothing: manager 2026-10-06), stops here
  # Read and matched with the shell's own read and case, never grep or sed: a stand-in grep that only gives 0 would
  # make every mark look found (#code-review-2 2026-10-06)
  NAME="$1"; MARK="$2"; shift 2
  "$@" </dev/null > "$HOME/.cache/apply-step.out" 2>&1 && SRC=0 || SRC=$?
  SEEN=0; SAID_STOP=0
  while IFS= read -r L || [ -n "$L" ]; do
    case "$L" in
      "ALT-STEP-OK "*) [ "$L" = "$MARK" ] && SEEN=1 ;;
      *) case "$L" in *"supervisor 被人停著"*) SAID_STOP=1 ;; esac; printf '  %s\n' "$L" ;;
    esac
  done < "$HOME/.cache/apply-step.out"
  [ "$SRC" = 0 ] || {{ echo "  ✗ 停在「$NAME」（結束碼 $SRC），後面的步驟都沒做"; exit 1; }}
  if [ -n "$MARK" ] && [ "$SEEN" = 0 ]; then
    echo "  ✗ 停在「$NAME」：結束碼 0，但沒看到它跑完的記號（$MARK），可能被別的同名指令頂替了；後面的步驟都沒做"; exit 1
  fi
}}
if [ -d "$H/.git" ]; then
  if [ -x "$H/bin/home-pull" ]; then step "更新引擎" "" "$H/bin/home-pull" --quiet --take-refused; else step "更新引擎" "" git -C "$H" pull -q --ff-only; fi
elif [ -e "$H" ]; then
  echo "  ✗ 停在「更新引擎」：$H 在，但不是 git repo"; exit 1
elif [ -z {repo} ]; then
  echo "  ✗ 停在「更新引擎」：這台沒有引擎，也不知道從哪裡 clone（實例沒寫 engine_repo，控制端的引擎也沒有 origin）"; exit 1
else
  step "clone 引擎" "" git clone -q {repo} "$H"
fi
REV="$(git -C "$H" rev-parse --short HEAD 2>/dev/null)" || REV=""
case "$REV" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) echo "程式碼：$REV" ;;
  *) echo "  ✗ 停在「更新引擎」：讀不到引擎的版本（git rev-parse 沒給 hash），引擎可能沒更新；後面的步驟都沒做"; exit 1 ;;
esac
NAME={name}
if [ -n "$NAME" ] && [ ! -s "$HOME/.config/altention/host" ]; then  # a name it already goes by is kept: chatlog's folder follows it
  {{ mkdir -p "$HOME/.config/altention" && printf '%s\n' "$NAME" > "$HOME/.config/altention/host"; }} || {{ echo "  ✗ 停在「機器名」：寫不了 ~/.config/altention/host"; exit 1; }}
fi
INSTANCE={instance}
if [ -n "$INSTANCE" ]; then step "接上實例" "ALT-STEP-OK instance-join" python3 "$H/ops/instance.py" join "$INSTANCE"; fi
# the instance up to date too, past its settings check (the way out when that check holds back by mistake)
if [ -n "$INSTANCE" ]; then step "更新實例" "ALT-STEP-OK instance-pull" python3 "$H/ops/instance.py" pull --take-refused; fi
if [ {bot_leaves} = 1 ]; then  # the bot has moved away: stop it first, the installer's plugins.py apply then takes its two lines out
  step "停掉機器人（dcbot/uninstall.sh；先停行程，cron 的兩行接著由安裝拿掉）" "ALT-STEP-OK dcbot-uninstall" "$H/dcbot/uninstall.sh"
fi
# the one installer (3a.9): the same steps a person or a new machine runs, one copy of them; not its PATH check,
# which would run the user's rc files on every apply (#code-review on 5601ddb)
ALT_NO_PATH_CHECK=1 "$H/install.sh" {allow} </dev/null > "$HOME/.cache/apply-step.out" 2>&1 && SRC=0 || SRC=$?
DONE=0; STOPPED=0; TODO=0
while IFS= read -r L || [ -n "$L" ]; do
  case "$L" in
    "ok core.done "*) DONE=1 ;;
    "todo dcbot.stopped "*) STOPPED=1 ;;
    "todo "*) TODO=1 ;;
  esac
  printf '  %s\n' "$L"
done < "$HOME/.cache/apply-step.out"
case "$SRC" in 0|2) ;; *) echo "  ✗ 停在「安裝（install.sh）」（結束碼 $SRC）"; exit 1 ;; esac
[ "$DONE" = 1 ] || {{ echo "  ✗ 停在「安裝（install.sh）」：結束碼 $SRC，但沒看到它跑完的那一行（ok core.done），可能被別的同名指令頂替了"; exit 1; }}
if [ "$TODO" = 1 ]; then exit 2; fi
if [ "$STOPPED" = 1 ]; then exit 3; fi
'''

PREVIEW = r'''
H={engine}
if [ ! -d "$H/.git" ]; then echo "  這台還沒有引擎：真的跑時會 clone，之後才知道要改什麼"; exit 0; fi
echo "程式碼：$(git -C "$H" rev-parse --short HEAD)（預覽用這台現在的引擎，沒有更新）"
OUT="$(python3 "$H/ops/plugins.py" apply {allow} 2>&1)" && R=0 || R=$?
printf '%s\n' "$OUT" | sed 's/^/  /'
exit $R
'''

HELD = r'''
H={engine}
if [ ! -f "$H/ops/plugins.py" ]; then echo '{{"unknown": "這台沒有引擎"}}'; exit 0; fi
python3 "$H/ops/plugins.py" held {names}
'''


def singles(rows, machine, bot):
    """The single-machine plugins that run on `machine`: machines one and on here, and "bot" where the bot runs."""
    out = [n for n, _, d, where, _ in rows if where == "on" and d is not None and d.get("machines") == "one"]
    return out + (["bot"] if machine == bot else [])


def held_on(m, names):
    """{name: True/False} for the plugins `names` on machine `m`, or a string saying why it cannot be told."""
    alias, code, out, err = run_on(m, HELD.format(engine=engine_on(m), names=" ".join(config.shell_quote(n) for n in names)),
                                   interpreter="sh", timeout=120)
    if alias is None or code == 255 or code == 124:
        return "連不上（%s）" % ((err or "").strip()[:120] or code)
    try:
        got = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "看不懂它的回答（結束碼 %s；它的引擎可能還沒有 plugins.py held）：%s" % (
            code, " ".join((out or err or "").split())[:100])
    if not isinstance(got, dict) or "unknown" in got:
        return "讀不出來（%s）" % (got.get("unknown") if isinstance(got, dict) else got)
    return got


OLD = ("那台的行不是新標記，plugins.py apply 不會拿：要手動移除（crontab -e），或在它還歸那台時先在那台跑 "
       "plugins.py apply --write 認領成新標記，再搬")


# COMPAT: crontab 還是舊格式的機器（# planner、# usage，或沒有標記的行）；每台的 crontab 都沒有舊格式的行（plugins.py plan 沒有 old、unclaimed）之後
def has(v):
    """A plugins.py held answer for one plugin: lines there ("marked", "old"; true from nothing older)."""
    return v not in (False, None)


def still_held(name, needs, inv):
    """[(other machine, [plugins], why)]: the single-machine plugins `needs` another machine still has, or cannot be
    told not to."""
    out = []
    for other, m in inv["machines"].items():
        if other == name:
            continue
        got = held_on(m, needs)
        if isinstance(got, str):
            out.append((other, needs, got))
            continue
        marked = [n for n in needs if has(got.get(n, True)) and got.get(n) != "old"]
        old = [n for n in needs if got.get(n) == "old"]
        if marked:
            out.append((other, marked, "還有它們的排程（先在那台跑 apply 拿掉）"))
        if old:
            out.append((other, old, OLD))
    return out


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry = force = False
    allow, moved, names, odd = [], [], [], []
    while args:  # nothing unknown is ignored: a mistyped flag must not turn into a write
        a = args.pop(0)
        if a == "--dry-run":
            dry = True
        elif a == "--force-move":
            force = True
        elif a == "--allow-time" and args and not args[0].startswith("-"):
            allow.append(args.pop(0))  # passed on to plugins.py apply on every machine done (the Mac's minute)
        elif a == "--moved-from" and args and not args[0].startswith("-"):
            moved.append(args.pop(0))  # passed on too: an old engine folder that is gone (C-17), ~ is that machine's
        elif a.startswith("-"):
            odd.append(a)
        else:
            names.append(a)
    if odd or not names:
        print("不認得：%s。用法：python3 ops/apply.py <機器…|all> [--dry-run] [--force-move] [--allow-time 插件/項目]… [--moved-from 舊引擎資料夾]…"
              % " ".join(odd or ["（沒有機器）"]))
        return 2
    allow_args = " ".join(["--allow-time " + config.shell_quote(x) for x in allow] +
                          ["--moved-from " + config.shell_quote(x) for x in moved])  # quoted: plugins.py expands ~ there
    try:
        inv = inventory()
    except Exception as e:  # a one-off tool: fail, plainly, before touching any machine
        print("機器清單讀不出來，沒有動任何機器：%s" % e)
        return 1
    if inv.get("single"):  # no list: no machine to go by
        print("沒有機器清單（實例沒有 machines.json）：只有這台，不用 apply。這台要照設定重裝排程和 hook：在引擎跑 ./install.sh；"
              "要更新引擎到最新的發佈版本：在引擎跑 python3 altention/update.py --release，說「更新了」之後再跑 ./install.sh。")
        return 1
    names = list(inv["machines"]) if names == ["all"] else names
    unknown = [n for n in names if n not in inv["machines"]]
    if unknown:
        print("清單裡沒有：%s" % ", ".join(unknown))
        return 1
    try:
        bot = config.bot_machine()[0]
    except Exception as e:
        bot, why = None, str(e)
    else:
        why = "機器清單上沒有任何一台有 dcbot 角色，這台也沒有機器人的設定"
    if not bot:  # not "no machine is the bot's": every machine would be taken for one the bot has left, and stopped
        print("不知道機器人在哪台（%s），沒有動任何機器" % why)
        return 1
    places = {n: plugins.placements(n) for n in inv["machines"]}
    holders = {}
    for n, (rows, _, _) in places.items():
        for s in singles(rows, n, bot):
            holders.setdefault(s, []).append(n)
    two = ["%s（%s）" % (s, "、".join(h)) for s, h in sorted(holders.items()) if len(h) > 1]
    if two:
        print("只能在一台跑的插件算出來在兩台以上：%s；沒有動任何機器" % "；".join(two))
        return 1
    # each machine is asked once what it holds now; one that is losing a single-machine plugin goes before one
    # gaining it, so a move is done in one run (#code-review-2 2026-10-06)
    every = sorted(set(holders) | {"bot"})
    mine_all = {n: held_on(inv["machines"][n], every) for n in names if not places[n][2]}

    def order(n):
        got = mine_all.get(n)
        if not isinstance(got, dict):
            return 1
        own = set(singles(places[n][0], n, bot))
        if any(has(got.get(s)) for s in every if s not in own):
            return 0  # loses one
        if any(not has(got.get(s)) for s in own):
            return 2  # gains one
        return 1
    names.sort(key=order)
    rc, stopped, by_hand = 0, False, False
    for n in names:
        m = inv["machines"][n]
        rows, problems, unplaced = places[n]
        on = [r[0] for r in rows if r[3] == "on"] + (["dcbot"] if n == bot else [])
        print("== %s：%s" % (n, "、".join(on) or "（沒有插件）"))
        if unplaced:
            for line in problems + unplaced:
                print("  ✗ %s" % line)
            print("  ✗ 這台不做：插件在哪台跑算不出來")
            rc = 1
            continue
        needs = singles(rows, n, bot)
        mine = mine_all[n]  # this machine's own: what it holds already, and an old bot
        if isinstance(mine, str):
            print("  ✗ 這台不做：讀不出它自己有哪些排程（%s）" % mine)
            rc = 1
            continue
        bot_leaves = n != bot and has(mine.get("bot"))
        if bot_leaves:
            print("  機器人已經不在這台跑：先停掉它（dcbot/uninstall.sh），再由 plugins.py apply 拿掉 supervisor 的兩行")
            # COMPAT: crontab 還是舊格式的機器（# planner、# usage，或沒有標記的行）；每台的 crontab 都沒有舊格式的行（plugins.py plan 沒有 old、unclaimed）之後
            if mine.get("bot") == "old":
                print("  ⚠ supervisor 的行不是新標記，plugins.py apply 不會拿：停掉之後請手動移除（crontab -e）")
        for s in sorted(set(every) - set(needs) - {"bot"}):
            if mine.get(s) == "old":
                print("  ⚠ %s 不在這台跑，但這台還有它的舊格式排程：%s" % (s, OLD))
        needs = [s for s in needs if not has(mine.get(s))]  # held here already: not a move, the others not asked
        if needs:
            left = still_held(n, needs, inv)
            for other, which, why in left:
                print("  %s %s 在 %s：%s" % ("⚠" if force else "✗", "、".join(which), other, why))
            blocked = sorted({p for _, which, _ in left for p in which}) if not force else []
            if blocked:
                # still done, without adding these: what it is to let go of goes now, so a swap of two machines'
                # plugins comes right in the next run (#code-review-2 2026-10-06)
                print("  ✗ 先不動 %s：只能在一台跑，別台還有或確認不了（先在那台跑 apply 移除；確定要做加 --force-move）；"
                      "這台其餘照做" % "、".join(blocked))
                rc = 1
            elif left:
                print("  ⚠ --force-move：照樣做，上面那幾台可能還在跑同一個插件，請盡快處理")
        else:
            blocked = []
        holds = "".join(" --hold " + config.shell_quote(p) for p in blocked)  # "bot": only the items whose own on is bot
        if dry:
            script = PREVIEW.format(engine=engine_on(m), allow=allow_args + holds)  # what is held back is shown as such (#code-review-2)
        else:
            script = SCRIPT.format(engine=engine_on(m), repo=config.shell_quote(clone_from(inv)), name=config.shell_quote(n),
                                   instance=config.shell_quote((inv.get("instance") or {}).get("repo", "")),
                                   bot_leaves="1" if bot_leaves else "0", allow=allow_args + holds)
        alias, code, out, err = run_on(m, script, interpreter="sh", timeout=900)
        if out.strip():
            print(out.rstrip())
        if code == 3 and not dry:  # everything done; only the supervisor someone stopped was left stopped
            print("  ⚠ 這台都做完了，只有 supervisor 被人停著（state/stop），沒有啟動")
            stopped = True
        elif code == 2 and not dry:  # done as far as it could go; the todo lines above say what a person has to do
            print("  ⚠ 這台需要人動手：見上面 todo 那幾行")
            by_hand = True
        elif code != 0:
            print("  ✗ 失敗（%s，結束碼 %s）%s" % (alias, code, "：" + (err or "").strip()[:300] if (err or "").strip() else ""))
            rc = 1
    if not dry:
        print("\n執行 python3 ops/doctor.py 檢查結果。")
    return rc or (2 if by_hand else 3 if stopped else 0)  # 1 failed; 2 a person has to do something; 3 a stopped supervisor


if __name__ == "__main__":
    sys.exit(main())
