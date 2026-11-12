"""工具层：抓网页。"""

from __future__ import annotations

import re
import urllib.error
import urllib.request

from ._util import ERR, truncate

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def http_get(url: str, max_chars: int = 6000) -> str:
    """抓网页或接口文本，简单去掉标签。"""
    if not url.startswith(("http://", "https://")):
        return f"{ERR}只支持 http/https URL"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; mini-agent/1.0)",
            "Accept": "text/html,application/json,text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        return f"{ERR}请求失败 HTTP {exc.code}：{url}"
    except (urllib.error.URLError, OSError) as exc:
        return f"{ERR}请求失败：{exc}"

    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return truncate(text, max_chars)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "抓取网页或 HTTP 接口的文本内容（自动去标签）。查资料、读在线文档、调 API 时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 http/https 地址"},
                    "max_chars": {"type": "integer", "description": "最多返回字符数，默认 6000"},
                },
                "required": ["url"],
            },
        },
    },
]

HANDLERS = {"http_get": http_get}
DANGEROUS: set[str] = set()
