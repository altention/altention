"""install.sh discord: what the installer asks Discord with the bot's token, so that the user only has to paste the
token and click one link (C-20, C-21). Standard library only; the token goes into the Authorization header and
nowhere else (no argument, no log, no message).

Checked against Discord's documentation (docs.discord.com/developers, 2026-10-16):
- GET /oauth2/applications/@me "Returns the bot's application object": id, name, bot_public ("When false, only the
  app owner can add the app to guilds"), bot_require_code_grant, flags (GATEWAY_MESSAGE_CONTENT 1 << 18,
  GATEWAY_MESSAGE_CONTENT_LIMITED 1 << 19: the latter is the Bot page's switch for bots in under 100 servers),
  owner (partial user), team (null, or a team object: owner_user_id, members[] of {membership_state (1 invited,
  2 accepted), user, role}), integration_types_config ("0" guild install, "1" user install).
- GET /users/@me/guilds: partial guilds {id, name, ...}, 200 at most per call (a bot just made is in a few).
- The bot authorization flow: https://discord.com/oauth2/authorize?client_id=…&scope=bot…&permissions=…
- Every call needs "User-Agent: DiscordBot ($url, $versionNumber)" or Cloudflare may refuse it; 401, 403 and 429
  answers count towards a ban at 10,000 in 10 minutes, so the polling here is every few seconds.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://discord.com/api/v10"
AGENT = "DiscordBot (altention, 1) install"
MESSAGE_CONTENT = (1 << 18) | (1 << 19)
ACCEPTED = 2
# The 14 permissions the bot asks for (docs.discord.com/developers/topics/permissions), by Discord's names.
PERMISSIONS = (
    ("VIEW_CHANNEL", 10), ("MANAGE_CHANNELS", 4), ("VIEW_AUDIT_LOG", 7), ("SEND_MESSAGES", 11),
    ("SEND_MESSAGES_IN_THREADS", 38), ("CREATE_PUBLIC_THREADS", 35), ("MANAGE_THREADS", 34), ("MANAGE_MESSAGES", 13),
    ("PIN_MESSAGES", 51), ("EMBED_LINKS", 14), ("ATTACH_FILES", 15), ("READ_MESSAGE_HISTORY", 16),
    ("ADD_REACTIONS", 6), ("USE_APPLICATION_COMMANDS", 31),
)
PERMISSION_BITS = sum(1 << bit for _, bit in PERMISSIONS)
POLL = 3      # seconds between two looks at the bot's servers
WAIT = 600    # seconds of looking before asking whether to go on
LONGEST = 600  # seconds: a 429 asking to wait longer than this ends the command instead


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is never followed: urllib would send the Authorization header on to wherever it points, another
    host included (#code-review-2 2026-10-16). Discord's API does not redirect these calls; a 3xx is an error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class BadToken(Exception):
    """Discord answered 401: the token is not (or no longer) a bot token."""


class Refused(Exception):
    """An answer that waiting will not change: 403, a redirect (never followed), or a 429 asking to wait longer
    than LONGEST. The text says which, in the user's words."""


class Unreachable(Exception):
    """No answer Discord could be said to have given: the network, a timeout, an answer that is not JSON, a status
    other than 401. The text never holds the token (urllib's reasons do not carry the request's headers)."""


def get(token, path, base=API, sleep=time.sleep, timeout=15):
    """GET base+path as the bot; 429 waited out (at most three times)."""
    req = urllib.request.Request(base + path, headers={"Authorization": "Bot " + token, "User-Agent": AGENT})
    for _ in range(4):
        try:
            with OPENER.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise BadToken() from None
            if e.code == 403:
                raise Refused("Discord 回了 HTTP 403（不准）") from None
            if 300 <= e.code < 400:
                raise Refused("Discord 回了轉址（HTTP %d），沒有跟過去" % e.code) from None
            if e.code == 429:
                try:
                    wait = float(json.loads(e.read().decode("utf-8")).get("retry_after", 1))
                except Exception:
                    wait = 1.0
                if wait > LONGEST:
                    raise Refused("Discord 說請求太多，要等 %d 分鐘才能再查（429）" % (wait // 60 + 1)) from None
                sleep(max(wait, 0.5))
                continue
            raise Unreachable("Discord 回了 HTTP %d" % e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise Unreachable(str(getattr(e, "reason", None) or e)) from None
        except ValueError:
            raise Unreachable("Discord 的回答不是 JSON") from None
    raise Unreachable("Discord 一直說請求太多（429）")


def invite_url(app_id):
    """The link that adds the bot to a server the user picks: both scopes, the 14 permissions, a server install."""
    return "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(
        [("client_id", app_id), ("scope", "bot applications.commands"), ("permissions", str(PERMISSION_BITS)),
         ("integration_type", "0")])


def problems(app):
    """What on the application's settings pages still has to change before the bot can work, in the user's words."""
    out = []
    if not (int(app.get("flags") or 0) & MESSAGE_CONTENT):
        out.append("機器人（Bot）頁的 Message Content Intent 還沒打開（打開後按 儲存變更（Save Changes））")
    if app.get("bot_public"):
        out.append("機器人（Bot）頁的 公開機器人（Public Bot）還開著：關掉，按 儲存變更（Save Changes）（Discord 不讓關的話，"
                   "先到 安裝（Installation）頁把 安裝連結（Install Link）改成 無（None）、存檔，再回來關）")
    if app.get("bot_require_code_grant"):
        out.append("機器人（Bot）頁的 需要 OAuth2 代碼授權（Requires OAuth2 Code Grant）開著：關掉，按 儲存變更（Save Changes）"
                   "（開著的話邀請連結會失敗）")
    kinds = app.get("integration_types_config")
    if isinstance(kinds, dict) and kinds and "0" not in kinds:
        out.append("安裝（Installation）頁的 安裝背景（Installation Contexts）沒勾 公會安裝（Guild Install）：勾起來，"
                   "按 儲存變更（Save Changes）")
    return out


def owner_choices(app):
    """Who the owner may be: [(id, name, is_the_team_owner)]. One entry when a person owns the app; the team's
    accepted members (its owner first) when a team does, since the app's own `owner` is then the team, not a person."""
    team = app.get("team")
    if not team:
        o = app.get("owner") or {}
        return [(str(o.get("id", "")), o.get("global_name") or o.get("username") or "", True)]
    top = str(team.get("owner_user_id", ""))
    out = []
    for m in team.get("members") or []:
        u = m.get("user") or {}
        if m.get("membership_state") != ACCEPTED or not u.get("id"):
            continue
        out.append((str(u["id"]), u.get("global_name") or u.get("username") or "", str(u["id"]) == top))
    return sorted(out, key=lambda x: not x[2])


def guilds(token, base=API, sleep=time.sleep):
    return [(str(g.get("id", "")), g.get("name") or "") for g in get(token, "/users/@me/guilds?limit=200", base, sleep)
            if isinstance(g, dict)]
