"""C-148 batch 1: golden files of what other programs, versions and machines rely on: the machine lines the engine
prints and reads (spec §11.13), and the interfaces it offers plugins with their versions (§10.7). A change here is
a change of interface: tools/golden update <case>, and a "golden:" line in the commit (tools/golden --help)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import re
import subprocess
import unittest

from altention import plugin, testing

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# A machine line as the code writes or looks for it: inside quotes, so a comment naming one is not counted
MARKER = re.compile(r"[\"'](ALT-[A-Z]+(?:-[A-Z]+)*(?: [a-z][a-z0-9-]*)?)")


SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "tests", "docs", "state"}


def engine_files():
    """The engine's own files that run, tests and prose aside: git's when the engine is a repository of its own,
    else (an exported or unpacked tree, or one inside another repository) every file under it."""
    r = subprocess.run(["git", "-C", ENGINE, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True, timeout=60)
    if r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(ENGINE):
        r = subprocess.run(["git", "-C", ENGINE, "ls-files"], stdout=subprocess.PIPE, universal_newlines=True, timeout=60)
        files = r.stdout.split("\n")
    else:
        files = []
        for top, dirs, names in os.walk(ENGINE):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            files += [os.path.relpath(os.path.join(top, n), ENGINE) for n in names]
    return [f for f in files if f and not set(f.split(os.sep)[:-1]) & SKIP_DIRS
            and not f.endswith((".md", ".json", ".txt"))]


class Markers(unittest.TestCase):
    def test_the_machine_lines(self):
        """§11.13: callers tell a step really ran by these lines; adding, renaming or dropping one is an interface
        change (another machine may run another version)."""
        found = set()
        for f in engine_files():
            try:
                with open(os.path.join(ENGINE, f), encoding="utf-8") as fh:
                    found |= set(MARKER.findall(fh.read()))
            except (OSError, UnicodeDecodeError):
                continue
        with open(os.path.join(ENGINE, "ops", "install.py"), encoding="utf-8") as fh:
            if '"ok core.done ' in fh.read():
                found.add("ok core.done (install.sh's last line starts so)")
        testing.golden("cli/markers", "".join(m + "\n" for m in sorted(found)), "§11.13")


class Interfaces(unittest.TestCase):
    def test_the_interfaces_and_their_versions(self):
        """§10.7: what plugins may use, each version and the older ones still offered; the built-in plugins' own
        too (usage.spend). Raising a version or dropping an "also" changes this file in the same commit."""
        rows = []
        for name, e in sorted(plugin.interfaces().items()):
            rows.append(("engine", name, e))
        for n, _f, d in plugin.discover()[0]:
            for name, e in sorted(((d or {}).get("interfaces") or {}).items()):
                rows.append(("plugin %s" % n, name, e))
        text = "".join("%s %s v%d%s%s\n" % (name, "(%s)" % who, e["version"],
                                            " also %s" % ",".join("v%d" % v for v in e["also"]) if e.get("also") else "",
                                            " transitional" if e.get("transitional") else "")
                       for who, name, e in sorted(rows, key=lambda r: r[1]))
        testing.golden("interfaces/versions", text, "§10.7")


if __name__ == "__main__":
    unittest.main()
