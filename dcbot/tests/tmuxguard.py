"""No test reaches a real tmux server: a stand-in for `tmux` in front of the real one, for every test of dcbot.

Imported by every test file (tests/fakepane.py imports it; a test file without a fake pane imports it itself;
test_tmuxguard pins that each does), so it is in force however the tests are run: the whole folder, one file, by
deploy.sh. It puts a folder with a `tmux` of its own first in PATH, for this process and all it starts. That
`tmux` passes on to the real one only what cannot be somebody's server:

- `-L <name>`: a name that says it is a test's (dcbot-test-…, dctest…);
- `-S <path>`: a socket outside this user's real socket folder, or in it under such a name;
- neither: refused (it would go to the server $TMUX names, or to the owner's default one).

What it refuses does not run (exit 97) and is written down; the test in which it happened fails when it ends, also
where the code that started tmux did not look at the result. The stand-in stays where it is when the tests end (one
small folder for a user, a real tmux and a real socket folder; written once): a process a test left behind, calling
tmux later, still meets it and not the real one. 2026-10-13: a test started a session on the server of
the bot that was running, and three others listed the panes of every server of the owner's."""
import atexit
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

TEST_NAMES = ("dcbot-test-", "dctest")
SCRIPT = r"""#!/bin/sh
# tests/tmuxguard.py: see there
real=%(real)s
realdir=%(realdir)s
log=${DCBOT_TMUXGUARD:-%(log)s}
check() {
    L=; S=; hasL=; hasS=
    while [ $# -gt 0 ]; do
        case "$1" in
            -L) L=$2; hasL=1; shift; [ $# -gt 0 ] && shift;;
            -S) S=$2; hasS=1; shift; [ $# -gt 0 ] && shift;;
            -L?*) L=${1#-L}; hasL=1; shift;;
            -S?*) S=${1#-S}; hasS=1; shift;;
            -c|-f|-T) shift; [ $# -gt 0 ] && shift;;
            -V) return 0;;
            -*) shift;;
            *) break;;
        esac
    done
    if [ -n "$hasL" ]; then
        case "$L" in dcbot-test-?*|dctest?*) ;; *) return 1;; esac
    fi
    if [ -n "$hasS" ]; then
        d=$(cd "$(dirname -- "$S")" 2>/dev/null && pwd -P) || d=$(dirname -- "$S")
        if [ "$d" = "$realdir" ]; then
            case "$(basename -- "$S")" in dcbot-test-?*|dctest?*) ;; *) return 1;; esac
        fi
    fi
    [ -n "$hasL$hasS" ] || return 1
    return 0
}
if check "$@"; then
    exec "$real" "$@"
fi
echo "tmux $*" >> "$log"
echo "tests/tmuxguard: refused, it would reach a real tmux server: tmux $*" >&2
exit 97
"""


def real_socket_dir(environ=None):
    """This user's real socket folder, links resolved (as `pwd -P` gives it)."""
    environ = os.environ if environ is None else environ
    return os.path.realpath(os.path.join(environ.get("TMUX_TMPDIR") or "/tmp", "tmux-%d" % os.getuid()))


def text(real, realdir, log):
    quote = lambda s: "'" + str(s).replace("'", "'\\''") + "'"  # noqa: E731
    return SCRIPT % {"real": quote(real), "realdir": quote(realdir), "log": quote(log)}


def write(folder, real, realdir, log):
    """The stand-in, written into `folder` as `tmux` (put in place whole: another process may be running it).
    `log`: where it writes what it refuses when DCBOT_TMUXGUARD does not say. Returns its path."""
    path = os.path.join(folder, "tmux")
    part = "%s.%d" % (path, os.getpid())
    with open(part, "w") as f:
        f.write(text(real, realdir, log))
    os.chmod(part, 0o755)
    os.replace(part, path)
    return path


def real_tmux():
    """The real tmux: the first in PATH that is not a stand-in of ours."""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if not os.path.basename(d.rstrip("/")).startswith("dcbot-tmuxguard-")]
    return shutil.which("tmux", path=os.pathsep.join(dirs))


def standing(real, realdir):
    """The folder the stand-in for this real tmux and this real socket folder lives in, made if need be: one for
    a user, kept. Somebody else's folder of that name, or one others can write: a folder of this run's own."""
    name = "dcbot-tmuxguard-%d-%s" % (os.getuid(), hashlib.sha256(("%s\n%s\n%s" % (real, realdir, SCRIPT)).encode()).hexdigest()[:10])
    folder = os.path.join(tempfile.gettempdir(), name)
    try:
        os.mkdir(folder, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(folder)
    if not os.path.isdir(folder) or os.path.islink(folder) or st.st_uid != os.getuid() or st.st_mode & 0o022:
        folder = tempfile.mkdtemp(prefix="dcbot-tmuxguard-")
    fallback = os.path.join(folder, "refused-by-what-outlived-its-test")
    path = os.path.join(folder, "tmux")
    try:
        with open(path) as f:
            same = f.read() == text(real, realdir, fallback)
    except OSError:
        same = False
    if not same or not os.access(path, os.X_OK):
        write(folder, real, realdir, fallback)
    return folder


def refused():
    """How much has been written down so far (bytes), and the lines."""
    try:
        with open(LOG) as f:
            text = f.read()
    except (OSError, NameError):
        return 0, []
    return len(text), text.splitlines()


def _run(self, result=None):
    before = refused()[0]
    out = _real_run(self, result)
    size, lines = refused()
    if size > before and result is not None:
        try:
            raise AssertionError("this test reached for a real tmux server (refused, nothing ran): %s"
                                 % "; ".join(lines[-3:]))
        except AssertionError:
            result.addFailure(self, sys.exc_info())
    return out


def _forget(path):
    try:
        os.remove(path)
    except OSError:
        pass


def install():
    """Once for a process: a process a test starts inherits PATH and the place refusals are written, and is not
    given a stand-in of its own."""
    global LOG, _real_run
    if os.environ.get("DCBOT_TMUXGUARD"):
        LOG = os.environ["DCBOT_TMUXGUARD"]
    else:
        real = real_tmux()
        handle, LOG = tempfile.mkstemp(prefix="dcbot-tmuxguard-refused-")
        os.close(handle)
        atexit.register(_forget, LOG)  # this run's list; the stand-in itself stays
        if real:  # no tmux on this machine: nothing to stand in front of
            folder = standing(real, real_socket_dir())
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        os.environ["DCBOT_TMUXGUARD"] = LOG
    _real_run = unittest.TestCase.run
    unittest.TestCase.run = _run


if "_real_run" not in globals():
    install()
