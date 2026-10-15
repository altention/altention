"""Where secrets lie, so that no agent is let read them (backlog K-03; the hook that refuses: dcbot/tools/dchook
--guard). Standard library only, Python 3.8: the guard runs on every machine, before every tool call of every
Claude Code there, so this reads one small file at most and starts nothing.

The list: DEFAULT (the bot's token, private SSH keys) and the instance's own additions (a plugin's key files)
(setting "core.secret_paths", a list of paths; "~" and "*" as in DEFAULT, "**" for any depth). A path ending in
".pub" is a public key and never secret. ~/.ssh/config and known_hosts are not secret: the machines' settings rest
on them and the owner has Claude edit them; nor authorized_keys (public keys, though its name has "key").

What it covers and what not (written for the owner, docs and GUIDE say the same): a tool asked to read, write or
search a listed path, or a shell command that names one, is refused. A path built at run time (a variable, a
script that opens the file) is not seen: the token file is also mode 600, and a separate system user for the
bot is the stronger guard (stage 6).
"""
import fnmatch
import glob
import json
import os
import re
import shlex
import sys

DEFAULT = ("~/.config/dcbot/**", "~/.ssh/id_*", "~/.ssh/*key*")  # (an installation adds its own: core.secret_paths)
PUBLIC = ".pub"
OPEN = ("authorized_keys", "authorized_keys2")  # public keys under a name "*key*" takes in: never secret
SETTING = "secret_paths"  # under "core" in the instance's plugins.json
SWITCH = "secret_guard"   # under "core": false turns the guard off (it is on when the settings cannot be read)


def core():
    """The instance's "core" settings, read straight from its plugins.json: no retries, no waiting (a broken file is
    not this module's to wait for). -> (dict, problem): problem is None, or why they could not be read."""
    try:
        from . import config
        inst = config.instance_dir()
    except Exception as e:
        return {}, "%s: %s" % (type(e).__name__, e)
    path = os.path.join(inst, "plugins.json") if inst else None
    if not path or not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        said = data.get("core") if isinstance(data, dict) else None
    except Exception as e:
        return {}, "%s: %s" % (type(e).__name__, e)
    return (said if isinstance(said, dict) else {}), None


def guard_on():
    """Is the guard on? Only an explicit false in the instance's settings turns it off."""
    return core()[0].get(SWITCH) is not False


def extra():
    """The instance's own additions. -> (paths, problem): problem is None, or why they could not be read."""
    settings, problem = core()
    if problem:
        return [], problem
    said = settings.get(SETTING)
    if said is None:
        return [], None
    if not isinstance(said, list):
        return [], "core.%s is not a list" % SETTING
    return [p for p in said if isinstance(p, str) and p.strip()], None


def patterns(home=None):
    """Every listed path as an absolute pattern. -> (patterns, problem) as extra() says."""
    home = home or os.path.expanduser("~")
    more, problem = extra()
    out = []
    for p in list(DEFAULT) + more:
        p = p.strip()
        p = home + p[1:] if p == "~" or p.startswith("~/") else p
        out.append(p if os.path.isabs(p) else os.path.join(home, p))
    return out, problem


def deny_rules(home=None):
    """Claude Code's own deny rules for the listed paths (for the conversations the bot opens: --settings), in its
    syntax: "~/…" under the home folder, "//…" for another absolute path ("/…" alone would be the settings file's
    folder). Read and Edit. Not for a pattern whose last name has a "*" in it (~/.ssh/id_*): Claude Code's rules have
    no "but not *.pub", so those are left to the guard, which knows the exceptions."""
    home = home or os.path.expanduser("~")
    out = []
    for p in patterns(home)[0]:
        if "*" in os.path.basename(p) and not p.endswith("/**"):
            continue
        rule = "~" + p[len(home):] if p.startswith(home + "/") else "/" + p
        out += ["Read(%s)" % rule, "Edit(%s)" % rule]
    return out


FOLD = sys.platform == "darwin"  # a file system that does not tell .ssh from .SSH (macOS's APFS by default): compare casefolded


def _fold(text, fold=None):
    return text.casefold() if (FOLD if fold is None else fold) else text


def matches(path, pattern, fold=None):
    """Does an absolute `path` fall under `pattern`? "**" any depth, "*" within one name. On a file system that does
    not tell case apart (`fold`, by default macOS) ~/.SSH/ID_RSA is ~/.ssh/id_rsa."""
    path, pattern = _fold(path, fold), _fold(pattern, fold)
    if path.endswith(PUBLIC) or os.path.basename(path) in OPEN:
        return False
    if pattern.endswith("/**"):
        top = pattern[:-3]
        return path == top or path.startswith(top + "/")
    head, name = os.path.split(pattern)
    return os.path.dirname(path) == head and fnmatch.fnmatchcase(os.path.basename(path), name)


def is_secret(path, listed=None, cwd=None, fold=None):
    """Is `path` (as a tool was handed it: "~", relative to `cwd`, through links) a listed secret?"""
    if not isinstance(path, str) or not path:
        return False
    listed = listed if listed is not None else patterns()[0]
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    seen = {os.path.normpath(p)}
    try:
        seen.add(os.path.realpath(p))
    except OSError:
        pass
    return any(matches(q, pat, fold) for q in seen for pat in listed)


def holds(folder, listed=None, home=None, fold=None, cwd=None):
    """Does a search of `folder` reach into a listed secret: is it a folder above one, inside the home folder but
    not the home folder itself (~/.config, ~/.ssh, ~/.config/dcbot)? A search of the home folder itself, of the
    root, or of a folder outside home is not judged here (manager 2026-10-14: a known limit). A relative `folder` is
    taken from `cwd` (the tool call's)."""
    home = home or os.path.expanduser("~")
    if not isinstance(folder, str) or not folder:
        return False
    f = _normal(folder, home, cwd)
    if not f or not os.path.isabs(f):
        return False
    hf, ff = _fold(home, fold), _fold(f.rstrip("/") or "/", fold)
    if not ff.startswith(hf + "/"):
        return False
    listed = listed if listed is not None else patterns(home)[0]
    return any(_fold(p, fold).startswith(ff + "/") or (p.endswith("/**") and _fold(p[:-3], fold) == ff) for p in listed)


def _normal(word, home, cwd=None):
    """A path as a command line writes it, as the shell would see it before running: quotes are gone already
    (shlex); "$HOME", "${HOME}", "~" and "~<this user>" at its start are the home folder; "//" and "/./" and ".."
    are folded. Any other word is taken from `cwd` when one is given (the folder the command starts in: what a cd
    in the command changes is not followed), else given back as it is; an option (-x, --x=…) is never a path."""
    w = word
    for lead in ("${HOME}", "$HOME"):
        if w == lead or w.startswith(lead + "/"):
            w = home + w[len(lead):]
    if w.startswith("~"):
        user, _, rest = w[1:].partition("/")
        if user in ("", _user()):
            w = home + ("/" + rest if rest or w.endswith("/") else "")
    if not w.startswith("/"):
        if not cwd or not os.path.isabs(cwd) or not w or w.startswith("-"):
            return w
        w = os.path.join(cwd, w)
    return os.path.normpath(re.sub(r"/+", "/", w))


def _user():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER") or os.environ.get("LOGNAME") or ""


def _braces(word):
    """Brace expansion as the shell does it, for one word ("a{b,c}d" -> ["abd", "acd"]); nested ones too."""
    m = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not m:
        return [word]
    out = []
    for part in m.group(1).split(","):
        out += _braces(word[:m.start()] + part + word[m.end():])
    return out


GLOB = re.compile(r"[*?\[]")
RECURSIVE = {"grep": ("-r", "-R", "--recursive", "-d"), "egrep": ("-r", "-R"), "fgrep": ("-r", "-R"), "rg": None, "ag": None,
             "ack": None, "find": None, "tar": None, "zip": None, "7z": None, "rsync": None, "scp": ("-r",), "cp": ("-r", "-R", "-a"),
             "ls": ("-R",), "tree": None, "base64": None, "xxd": None}
# a command that, given a folder, reads what is inside it (None: whatever its options); du only sizes, ls only names


def _reads_inside(words):
    """Does this command (its words, the command first) read into a folder it is given?"""
    name = os.path.basename(words[0]) if words else ""
    if name not in RECURSIVE:
        return False
    flags = RECURSIVE[name]
    if flags is None:
        return True
    for w in words[1:]:
        if w.startswith("--") and w in flags:
            return True
        if w.startswith("-") and not w.startswith("--") and any(("-" + c) in flags for c in w[1:]):
            return True
    return False


KEY_USERS = ("ssh", "scp", "sftp")  # take a private key to log in with as -i's argument: they use it, not show it


def _key_only_used(words):
    """The places (indices into `words`, the command first) where a key is handed over to be used, not read out
    (manager 2026-10-14): -i's argument of ssh, scp, sftp; the keys ssh-add loads; -f's argument of ssh-keygen -y
    (prints the public key). Anywhere else in the same command (a command ssh runs over there) is judged as usual."""
    name = os.path.basename(words[0]) if words else ""
    out = set()
    if name in KEY_USERS:
        out = set(i + 1 for i, w in enumerate(words[:-1]) if w == "-i" and i > 0)
    elif name == "ssh-add":
        out = set(i for i, w in enumerate(words) if i > 0 and not w.startswith("-"))
        out -= set(i + 1 for i, w in enumerate(words) if w in ("-t", "-E", "-S", "-H", "-h"))  # options taking a value
    elif name == "ssh-keygen" and "-y" in words:
        out = set(i + 1 for i, w in enumerate(words[:-1]) if w == "-f" and i > 0)
    return out


def _segments(command):
    """The simple commands of a command line, each as its words (quotes taken off), split at ; && || | and new
    lines; leading VAR=value words, sudo, env, time, nohup and command left out. A line shlex cannot read (an
    unclosed quote) is split on blanks."""
    out = []
    for part in re.split(r"\|\||&&|[;|\n]", command):
        try:
            words = shlex.split(part, comments=False, posix=True)
        except ValueError:
            words = part.split()
        while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] in ("sudo", "env", "time", "nohup", "command", "exec")):
            words = words[1:]
        if words:
            out.append(words)
    return out


def _pieces(word):
    """The paths inside one word: the word, and what follows "=", "<", ">" or ":" in it (--file=~/x, <~/x, host:~/x)."""
    out = [word]
    for sep in ("=", "<", ">", ":"):
        if sep in word:
            out += [p for p in word.split(sep)[1:] if p]
    return out


def mentions(command, listed=None, home=None, fold=None, cwd=None):
    """The listed secrets a shell command names, or []: a path written in it (quotes, "$HOME", "${HOME}", "~",
    "~<user>", "//", "/./" as the shell reads them) that is a listed secret; a pattern in it (* ? [ { ) that the shell
    would expand to one, judged against the files there are and against the list itself; a folder above a listed
    secret, inside home but not home itself, given to a command that reads into folders (grep -r, rg, find, tar, zip,
    rsync, cp -r, ls -R: not du, not plain ls). A relative path is taken from `cwd` (the folder the tool call starts
    in). Not seen: a relative path after a cd in the command, a search of the whole home
    folder, a path put together at run time, a program that opens the file itself (a known limit: GUIDE, SECURITY)."""
    if not isinstance(command, str) or not command:
        return []
    home = home or os.path.expanduser("~")
    listed = listed if listed is not None else patterns(home)[0]
    found = []
    for words in _segments(command):
        inside, used = _reads_inside(words), _key_only_used(words)
        for at, word in enumerate(words):
            if (at == 0 and len(words) > 1) or at in used:
                continue
            for piece in _pieces(word):
                for shape in _braces(piece):
                    path = _normal(shape, home, cwd)
                    if not path.startswith("/"):
                        continue
                    if GLOB.search(path):
                        if _could_be(path, listed, fold) or any(is_secret(p, listed, fold=fold) for p in glob.glob(path)[:200]):
                            found.append(piece)
                    elif is_secret(path, listed, fold=fold) or (inside and holds(path, listed, home, fold)):  # (absolute by now)
                        found.append(piece)
    return list(dict.fromkeys(found))


def _could_be(pattern, listed, fold=None):
    """Would the shell pattern `pattern` match a listed secret, by the list alone (whether or not the file is there):
    tried against each listed path with its own "*" and "**" stood in by a name."""
    pattern = _fold(pattern, fold)
    parts = pattern.split("/")
    for p in listed:
        p = _fold(p, fold)
        if p.endswith("/**"):  # a pattern reaching the folder, or anything in it (~/.config/dcbo?/env)
            top = p[:-3]
            if any(fnmatch.fnmatchcase(top, "/".join(parts[:k])) for k in range(2, len(parts) + 1)):
                return True
            continue
        sample = p.replace("*", "x")  # the listed one written out, against the command's pattern
        if fnmatch.fnmatchcase(sample, pattern) and matches(sample, p, fold=False):
            return True
        # and the command's pattern written out (a class by its first letter, * by nothing), against the listed one
        written = re.sub(r"\[!?\^?(.)[^\]]*\]", r"\1", pattern).replace("*", "").replace("?", "x")
        if matches(written, p, fold=False):
            return True
    return False
