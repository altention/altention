#!/usr/bin/env python3
"""The outside dead man's switch (C-163 d3b, design section 6): healthchecks.io, or one the owner runs.

  python3 altention/health.py ping      the core's healthchecks schedule (every 5 minutes, on the bot's machine):
                                        when the bot's heartbeat (dcbot/state/heartbeat) was written in the last
                                        2 minutes, one GET to the ping address (the core's secret healthchecks-url);
                                        nothing else is sent: no body, User-Agent "altention". No address: nothing.
  python3 altention/health.py check     whether the address stored is one this sends to (https only), as one line

A bot stuck with its process alive stops the pings too (its heartbeat goes old), and healthchecks tells the owner
when they stop. Only https: an http or other address is refused and said once in the log. Exit status: 0 (pinged,
nothing to do, or refused and said), 2 wrong usage. Standard library only, Python 3.8.
"""
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import secret_store  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRESH_SECS = 120


def address():
    """The stored ping address, None without one (never printed)."""
    try:
        with open(secret_store.path("core", "healthchecks-url"), encoding="utf-8") as f:
            url = f.read().strip()
    except (OSError, secret_store.Refused):
        return None
    return url or None


def allowed(url):
    """Only https, with a host: healthchecks.io's https://hc-ping.com/… or the owner's own https server (manager)."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return u.scheme == "https" and bool(u.hostname)


def heartbeat_age(now=None):
    try:
        with open(os.path.join(ENGINE, "dcbot", "state", "heartbeat")) as f:
            return (now or time.time()) - int(f.read().strip())
    except (OSError, ValueError):
        return None


def ping(now=None, opener=None):
    """One round: "pinged", "no address", "refused", "bot not fresh" or "failed: …" (said in the log)."""
    url = address()
    if url is None:
        return "no address"
    if not allowed(url):
        return "refused"
    age = heartbeat_age(now)
    if age is None or age > FRESH_SECS:
        return "bot not fresh"
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "altention"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=10):
            pass
    except Exception as e:  # (healthchecks tells the owner when pings stop; nothing to do here but say it)
        return "failed: %s" % type(e).__name__
    return "pinged"


def main(argv):
    from altention import text
    if argv[:1] == ["ping"]:
        got = ping()
        said = os.path.expanduser("~/.local/state/altention/healthchecks-said")
        try:
            with open(said) as f:
                last = f.read()
        except OSError:
            last = ""
        if got in ("pinged", "no address", "bot not fresh"):
            got = ""
        if got != last:  # (the log gets a trouble once, not every 5 minutes)
            if got:
                print("%s healthchecks: %s" % (time.strftime("%Y-%m-%d %H:%M"), text.t("health.refused") if got == "refused" else got))
            try:
                os.makedirs(os.path.dirname(said), exist_ok=True)
                with open(said, "w") as f:
                    f.write(got)
            except OSError:
                pass
        return 0
    if argv[:1] == ["check"]:
        url = address()
        print(text.t("health.none") if url is None else text.t("health.ok") if allowed(url) else text.t("health.refused"))
        return 0
    print(__doc__.split("\n\n")[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
