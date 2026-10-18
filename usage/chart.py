#!/usr/bin/env python3
"""Usage charts for the dashboard channel, as one PNG (run with the venv that has matplotlib, see usage/install.sh).

  chart.py DATA.json OUT.png

DATA.json (written by collect.py): {"today", "results": [usage/remote.py output per machine], "samples": [...],
"plan_pro": 20, "plan_name": "Max 20x" (both null when usage.plan is not set), "utc_offset": seconds east of UTC of the boards' zone (collect.py: the instance's core.timezone, else
this machine's)}. Three panels: API-equivalent cost per day and machine (30 days), the subscription's weekly
percentage over time (from the hourly samples), and the projects of the last 7 days.
"""
import datetime
import getpass
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

for path in ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
             "/System/Library/Fonts/PingFang.ttc"):
    try:
        font_manager.fontManager.addfont(path)
        plt.rcParams["font.family"] = [font_manager.FontProperties(fname=path).get_name(), "DejaVu Sans"]
        break
    except (OSError, RuntimeError):
        continue
plt.rcParams["axes.unicode_minus"] = False
TZ = datetime.datetime.now().astimezone().tzinfo  # main() sets the boards' zone (utc_offset); this machine's without it
COLORS = ["#4C78A8", "#F58518", "#54A24B", "#B279A2", "#E45756", "#72B7B2"]


def daily_panel(ax, results, today, days=30):
    dates = [(today - datetime.timedelta(days=i)) for i in range(days - 1, -1, -1)]
    bottom = [0.0] * days
    for i, r in enumerate(results):
        costs = [sum(x["cost"] for x in r.get("daily", []) if x["date"] == d.isoformat()) for d in dates]
        if not any(costs):
            continue
        ax.bar(range(days), costs, bottom=bottom, color=COLORS[i % len(COLORS)], label=r["machine"], width=0.8)
        bottom = [b + c for b, c in zip(bottom, costs)]
    ax.set_xticks(range(0, days, 3))
    ax.set_xticklabels([dates[i].strftime("%m-%d") for i in range(0, days, 3)], fontsize=8)
    ax.set_title("每天的用量（%s，API 等值美元）" % getpass.getuser(), fontsize=11)
    ax.set_ylabel("US$")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3)


def weekly_panel(ax, samples, pro, plan="?"):
    pts = [(datetime.datetime.fromtimestamp(s["t"], TZ), s["week"]) for s in samples if s.get("week") is not None]
    cutoff = datetime.datetime.now(TZ) - datetime.timedelta(days=28)
    pts = [p for p in pts if p[0] >= cutoff]
    if pts:
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=COLORS[0], marker="." if len(pts) < 50 else None)
        ax.set_ylim(0, max(100, max(p[1] for p in pts) + 5))
        ax.axhline(100, color="#E45756", lw=0.8, ls="--")
        if pro:  # usage.plan not set: no Pro / Max 5x lines (they would be a guess)
            ax.axhline(100.0 / pro * 5, color="#F58518", lw=0.8, ls=":", label="Max 5x 的整週額度")
            ax.axhline(100.0 / pro, color="#54A24B", lw=0.8, ls=":", label="Pro 的整週額度")
            ax.legend(fontsize=8, loc="upper left")
        short_span = pts[-1][0] - pts[0][0] < datetime.timedelta(days=2)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m-%d %H:%M" if short_span else "%m-%d", tz=TZ))
        ax.tick_params(axis="x", labelsize=8)
    else:
        ax.text(0.5, 0.5, "還沒有資料（每小時記一次）", ha="center", va="center", transform=ax.transAxes)
    ax.set_title("訂閱額度：這週用了幾 %%（整個 Claude 帳號，%s）" % plan, fontsize=11)
    ax.set_ylabel("%")
    ax.grid(alpha=0.3)


def projects_panel(ax, results, top=8):
    short = lambda name: name if len(name) <= 24 else name[:22] + "…"  # noqa: E731
    items = sorted(((p["cost"], "%s（%s）" % (short(p["project"]), r["machine"])) for r in results
                    for p in r.get("projects", [])), reverse=True)[:top]
    items = [i for i in items if i[0] >= 0.5][::-1]
    if items:
        ax.barh([n for _, n in items], [c for c, _ in items], color=COLORS[0])
        for y, (c, _) in enumerate(items):
            ax.text(c, y, " $%.0f" % c, va="center", fontsize=8)
    ax.set_title("近 7 天用最多的專案（API 等值美元）", fontsize=11)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="x", alpha=0.3)


def main(data_path, out):
    global TZ
    with open(data_path) as f:
        data = json.load(f)
    if isinstance(data.get("utc_offset"), int):
        TZ = datetime.timezone(datetime.timedelta(seconds=data["utc_offset"]))
    today = datetime.date.fromisoformat(data["today"])
    fig, axes = plt.subplots(3, 1, figsize=(9, 12), gridspec_kw={"height_ratios": [1.1, 1, 1]})
    daily_panel(axes[0], data["results"], today)
    weekly_panel(axes[1], data["samples"], data.get("plan_pro"), data.get("plan_name") or "方案沒設定")
    projects_panel(axes[2], data.get("results7") or data["results"])
    fig.suptitle("用量  %s" % datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), fontsize=13)
    fig.tight_layout()
    fig.savefig(out, dpi=110)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
