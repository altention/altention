"""usage/chart.py's projects panel (11-09: two project names cut to the same label folded into one bar and every
amount beside it moved one row). matplotlib is not needed: stand-ins take its place."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_chart():
    fake = types.ModuleType("matplotlib")
    fake.use = lambda *a, **k: None
    fake.dates = types.ModuleType("matplotlib.dates")
    fake.pyplot = types.ModuleType("matplotlib.pyplot")
    fake.pyplot.rcParams = {}
    fake.font_manager = types.ModuleType("matplotlib.font_manager")

    def no_font(path):
        raise OSError(path)  # (no font added: chart.py goes on without one)
    fake.font_manager.fontManager = types.SimpleNamespace(addfont=no_font)
    mods = {"matplotlib": fake, "matplotlib.dates": fake.dates, "matplotlib.pyplot": fake.pyplot,
            "matplotlib.font_manager": fake.font_manager}
    with mock.patch.dict(sys.modules, mods):
        spec = importlib.util.spec_from_file_location("usage_chart_test", os.path.join(ENGINE, "usage", "chart.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class Ax(object):
    def __init__(self):
        self.bars, self.ticks, self.labels, self.texts = None, None, None, []

    def barh(self, ys, widths, **kw):
        self.bars = (list(ys), list(widths))

    def set_yticks(self, ys):
        self.ticks = list(ys)

    def set_yticklabels(self, labels):
        self.labels = list(labels)

    def text(self, x, y, s, **kw):
        self.texts.append((x, y, s))

    def set_title(self, *a, **k):
        pass

    def tick_params(self, *a, **k):
        pass

    def grid(self, *a, **k):
        pass


class Projects(unittest.TestCase):
    RESULTS = [{"machine": "lap", "projects": [
        {"project": "sample-project-long-name-branch-alpha-0921", "cost": 943.0},
        {"project": "sample-project-long-name-branch-beta-1029", "cost": 120.0},
        {"project": "agent-home", "cost": 282.0}]}]

    def test_two_long_names_stay_apart(self):
        chart = load_chart()
        a, b = (chart.short(p["project"]) for p in self.RESULTS[0]["projects"][:2])
        self.assertNotEqual(a, b, "cut to the start alone they read the same")
        self.assertLessEqual(len(a), 24)
        self.assertEqual(chart.short("agent-home"), "agent-home")

    def test_every_amount_beside_its_own_bar_even_with_equal_labels(self):
        chart = load_chart()
        ax = Ax()
        with mock.patch.object(chart, "short", lambda name, keep=24: "same…（cut）"):  # (labels equal on purpose)
            chart.projects_panel(ax, self.RESULTS)
        ys, widths = ax.bars
        self.assertEqual(ys, [0, 1, 2], "one row per project, at numbers")
        self.assertEqual(ax.ticks, ys)
        self.assertEqual(len(ax.labels), 3)
        self.assertEqual([(x, y) for x, y, _ in ax.texts], list(zip(widths, ys)), "each amount at its own bar")
        self.assertEqual(ax.texts[-1][2], " $943", "the biggest at the top, with its own amount")


if __name__ == "__main__":
    unittest.main()
