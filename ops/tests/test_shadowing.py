"""No loop may reuse a name that is already in use around it.

Twice a loop variable silently replaced another variable of the same name: tools/dcremote removed the
conversation's transcript instead of the spool file (a4c098c), and ops/doctor.py returned 'docs/refactor' as
the machine's name (98fdff2). Three shapes are refused, in every Python file the machines run:

- a loop variable with the name of one of the function's parameters;
- an inner loop variable with the name of an outer loop's variable;
- a tuple assignment inside a loop that rebinds the loop's own variable (`for path, ev in …: path, kind = …`).

Standard library only. Run from the engine's root: python3 -m unittest <this file>.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import ast
import glob
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATTERNS = ["ops/*.py", "altention/*.py", "chatlog/*.py", "usage/*.py", "progress/*.py", "planner/*.py", "bin/*",
            "dcbot/tools/*", "dcbot/bot/*.py", "dcbot/supervisor.py"]
LOOPS = (ast.For, ast.AsyncFor)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def names_in(target):
    """The names a target binds (`ev["x"], pos[path] = …` binds none: ev and path are only read there)."""
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def inside(node):
    """The nodes under `node`, without going into nested functions (they have their own names)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, FUNCS + (ast.Lambda,)):
            continue
        yield child
        for sub in inside(child):
            yield sub


def problems(source, path="<source>"):
    """[(line, what)] for the three shapes in one file's source."""
    found = []
    tree = ast.parse(source)
    for fn in [n for n in ast.walk(tree) if isinstance(n, FUNCS)]:
        args = fn.args
        params = {a.arg for a in args.args + args.kwonlyargs + getattr(args, "posonlyargs", [])} - {"self", "cls"}
        for node in inside(fn):
            if isinstance(node, LOOPS):
                for name in sorted(names_in(node.target) & params):
                    found.append((node.lineno, "%s(): the loop variable %r is also a parameter" % (fn.name, name)))
    for loop in [n for n in ast.walk(tree) if isinstance(n, LOOPS)]:
        outer = names_in(loop.target)
        for node in inside(loop):
            if isinstance(node, LOOPS):
                for name in sorted(names_in(node.target) & outer):
                    found.append((node.lineno, "the inner loop variable %r is the outer loop's too" % name))
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Tuple):
                        for name in sorted(names_in(target) & outer):
                            found.append((node.lineno, "a tuple assignment rebinds the loop variable %r" % name))
    return ["%s:%d %s" % (path, line, what) for line, what in sorted(set(found))]


def python_files(root):
    out = []
    for pattern in PATTERNS:
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            if not os.path.isfile(path):
                continue
            if not path.endswith(".py"):  # scripts without an extension: only the Python ones
                with open(path, "rb") as f:
                    if b"python" not in f.readline():
                        continue
            out.append(path)
    return out


class Shadowing(unittest.TestCase):
    def test_the_two_real_bugs_would_have_been_caught(self):
        dcremote = ("def stream():\n    for path, ev in waiting():\n"
                    "        path, kind = ev.get('transcript_path') or '', ev.get('hook_event_name')\n"
                    "        done(path)\n")
        self.assertIn("rebinds the loop variable 'path'", "\n".join(problems(dcremote)))
        doctor = ("def check(name, m, head):\n    for name, state in paths.items():\n        pass\n    return name\n")
        self.assertIn("'name' is also a parameter", "\n".join(problems(doctor)))
        self.assertEqual(problems("def f():\n    for path, ev in w():\n        ev['texts'], pos[path] = g()\n"), [],
                         "writing into ev[...] or pos[path] reads the names, it does not rebind them")
        nested = "def f():\n    for i in a:\n        for i in b:\n            pass\n"
        self.assertIn("inner loop variable 'i'", "\n".join(problems(nested)))

    def test_what_is_fine_stays_fine(self):
        fine = ("def main(names):\n    names = names or list(all_names)\n    for n in names:\n"
                "        a, b = n, 1\n        for m in n:\n            pass\n"
                "    for n in names:\n        def inner(n):\n            for k in n:\n                pass\n")
        self.assertEqual(problems(fine), [])

    def test_no_file_the_machines_run_has_one(self):
        files = python_files(ROOT)
        self.assertGreater(len(files), 20, "the patterns found the code (is ROOT right?)")
        found = []
        for path in files:
            with open(path, encoding="utf-8") as f:
                found += problems(f.read(), os.path.relpath(path, ROOT))
        self.assertEqual(found, [], "\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
