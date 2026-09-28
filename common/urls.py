"""Нормализация ссылок и определение источника.

Живёт отдельно от роутера бота, потому что нужна и storage (url_hash),
и админке (показать нормализованный url в очереди).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

# Зеркала, которые люди кидают из шаринга — приводим к оригиналу.
MIRRORS = {
    "vxtwitter.com": "twitter.com",
    "fixvx.com": "twitter.com",
    "fxtwitter.com": "twitter.com",
    "fixupx.com": "twitter.com",
    "twittpr.com": "twitter.com",
    "nitter.net": "twitter.com",
    "ddinstagram.com": "instagram.com",
    "instagramez.com": "instagram.com",
    "kkinstagram.com": "instagram.com",
}

TWITTER_HOSTS = {"twitter.com", "x.com", "mobile.twitter.com"}
INSTAGRAM_HOSTS = {"instagram.com", "instagr.am"}

# Мусорные query-параметры из шаринга. Без их вырезания кэш не попадает.
JUNK_PARAMS_PREFIXES = ("igsh", "igshid", "utm_", "s", "t", "ref", "ref_src", "ref_url", "si")

URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


@dataclass
class ParsedLink:
    url: str           # нормализованный
    source: str        # instagram | twitter
    media_kind: str    # post | reel | story | highlight | unknown
    target_user: str | None
    url_hash: str


def extract_urls(text: str) -> list[str]:
    """Вытаскивает все ссылки из сообщения."""
    return [m.group(0).rstrip(').,;') for m in URL_RE.finditer(text or "")]


def _clean_host(host: str) -> str:
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return MIRRORS.get(host, host)


def _clean_query(query: str) -> str:
    if not query:
        return ""
    keep = []
    for part in query.split("&"):
        if not part:
            continue
        key = part.split("=", 1)[0].lower()
        if key.startswith(JUNK_PARAMS_PREFIXES):
            continue
        keep.append(part)
    return "&".join(keep)


def normalize(raw_url: str) -> str | None:
    """Приводит ссылку к каноническому виду. None — если это не наш источник."""
    try:
        p = urlparse(raw_url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None

    host = _clean_host(p.netloc.split(":")[0])
    if host in TWITTER_HOSTS:
        host = "twitter.com"
    elif host in INSTAGRAM_HOSTS:
        host = "instagram.com"
    else:
        return None

    path = p.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", _clean_query(p.query), ""))


def hash_url(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


def parse(raw_url: str) -> ParsedLink | None:
    """Полный разбор: источник, тип медиа, чей профиль."""
    url = normalize(raw_url)
    if not url:
        return None

    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    source = "twitter" if p.netloc == "twitter.com" else "instagram"
    media_kind = "unknown"
    target_user = None

    if source == "twitter":
        # /user/status/12345
        if len(parts) >= 3 and parts[1] in ("status", "statuses"):
            media_kind = "post"
            target_user = parts[0]
        elif len(parts) == 1:
            target_user = parts[0]
    else:
        if not parts:
            return None
        head = parts[0].lower()
        if head in ("p", "tv"):
            media_kind = "post"
        elif head in ("reel", "reels"):
            media_kind = "reel"
        elif head == "stories":
            # /stories/username/123  |  /stories/highlights/123
            if len(parts) >= 2 and parts[1].lower() == "highlights":
                media_kind = "highlight"
            else:
                media_kind = "story"
                target_user = parts[1] if len(parts) >= 2 else None
        else:
            target_user = parts[0]
            media_kind = "post" if len(parts) > 1 else "unknown"

    return ParsedLink(
        url=url,
        source=source,
        media_kind=media_kind,
        target_user=target_user,
        url_hash=hash_url(url),
    )
