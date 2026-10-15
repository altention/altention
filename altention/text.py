"""Interface text in one place, so another language can be added later (DESIGN.md 12.3).

t("key", **fields): the text in the instance's language (setting "core.language", default zh-TW), falling back
to zh-TW, then to the key itself. Text written or changed from stage 2 on goes here; older text moves over
as it is touched.
"""
from . import config

DEFAULT = "zh-TW"
TEXT = {
    "zh-TW": {
        "post.offline": "（機器人目前離線{age}，訊息已排進佇列，上線後會送出，不用重送）",
        "post.offline_age": "（{seconds} 秒沒有心跳）",
        "bot.not_found": "找不到負責 Discord 機器人的機器：這台沒有機器人的設定（~/.config/dcbot/env），機器清單裡也沒有 dcbot 角色",
    },
}


def lang():
    try:
        return config.setting("core.language", DEFAULT) or DEFAULT
    except config.ConfigError:  # showing text must not fail because a settings file is broken
        return DEFAULT


def t(key, **fields):
    text = TEXT.get(lang(), {}).get(key) or TEXT[DEFAULT].get(key) or key
    try:
        return text.format(**fields) if fields else text
    except (KeyError, IndexError, ValueError):  # a field left out must not break the path that says it
        return text
