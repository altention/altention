"""Secrets taken out of text before it leaves this machine: chatlog before pushing to the logs repo, the bot before
posting a transcript's tool output to Discord (backlog B-12). One pattern for both, so that what one hides the other
does not show.

redact(text): `text` with every match of PATTERN replaced by MARK. Takes a str only. Standard library only, Python
3.8, nothing read at import time.

Hidden: API keys (sk-…, Google AIza…), GitHub tokens, AWS access key ids, Slack tokens, Discord bot/user tokens,
Discord webhook URLs (also cut short), PEM private keys. Not hidden: hex hashes (git, sha256), UUIDs, numeric ids.
"""
import re

MARK = "[REDACTED]"

PATTERN = re.compile(
    r"(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}"
    r"|[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}"  # Discord bot/user token
    r"|discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"  # any length: a cut-off one is still part of it
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)"
)


def redact(text):
    return PATTERN.sub(MARK, text)
