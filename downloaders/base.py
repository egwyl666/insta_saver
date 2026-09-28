"""Общий слой для yt-dlp и gallery-dl.

Обе утилиты запускаются как внешние процессы, а не как библиотеки: так их
можно обновлять через pip без рестарта бота, и падение парсера не уносит
весь процесс.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}
ANIM_EXT = {".gif"}
JUNK_EXT = {".json", ".txt", ".description", ".part", ".ytdl", ".info"}

# Коды ошибок. Их видит и очередь (решает, ставить ли в ожидание),
# и пользователь (через bot/texts.py).
E_PRIVATE = "PRIVATE"            # приватный профиль, доступа нет
E_LOGIN = "LOGIN_REQUIRED"       # нужен вход (возрастные ограничения и т.п.)
E_AUTH_EXPIRED = "AUTH_EXPIRED"  # cookies протухли
E_BLOCKED = "ACCOUNT_BLOCKED"    # инста прикрыла аккаунт
E_NOT_FOUND = "NOT_FOUND"        # пост удалён / ссылка битая
E_RATE = "RATE_LIMITED"
E_TOO_LARGE = "TOO_LARGE"
E_EMPTY = "NO_MEDIA"
E_TIMEOUT = "TIMEOUT"
E_UNKNOWN = "UNKNOWN"

# Ошибки, при которых аккаунт надо вывести из пула.
ACCOUNT_FATAL = {E_AUTH_EXPIRED, E_BLOCKED}


class DownloadError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}"[:500])
        self.code = code
        self.detail = detail[:1000]


@dataclass
class MediaItem:
    path: Path
    kind: str          # photo | video | animation | document
    size: int


@dataclass
class DownloadResult:
    items: list[MediaItem] = field(default_factory=list)
    uploader: str | None = None
    title: str | None = None


def classify(output: str) -> str:
    """Разбор stderr утилит. Сообщения меняются от версии к версии,
    поэтому ловим по устойчивым кускам, а не по точным строкам."""
    low = (output or "").lower()

    if "login required" in low or "requires authentication" in low or "sign in" in low:
        return E_LOGIN
    if "age" in low and ("restrict" in low or "confirm" in low):
        return E_LOGIN
    if "private" in low or "not authorized to view" in low or "follow this account" in low:
        return E_PRIVATE
    if "checkpoint" in low or "challenge_required" in low or "suspicious" in low:
        return E_BLOCKED
    if "csrf" in low or "session" in low and "expired" in low:
        return E_AUTH_EXPIRED
    if "login_required" in low or "authentication cookies" in low or "cookies are no longer valid" in low:
        return E_AUTH_EXPIRED
    if "429" in low or "rate limit" in low or "too many requests" in low or "please wait a few minutes" in low:
        return E_RATE
    if "404" in low or "not found" in low or "unavailable" in low or "has been removed" in low:
        return E_NOT_FOUND
    if "no video could be found" in low or "unsupported url" in low or "no results" in low:
        return E_EMPTY
    return E_UNKNOWN


def kind_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    if ext in ANIM_EXT:
        return "animation"
    return None


def collect(work_dir: Path) -> list[MediaItem]:
    """Собирает скачанное из рабочей папки, мусор игнорирует.

    Сортировка по имени — так порядок карусели сохраняется: обе утилиты
    нумеруют файлы по порядку в посте.
    """
    items: list[MediaItem] = []
    for path in sorted(work_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() in JUNK_EXT:
            continue
        kind = kind_of(path)
        if not kind:
            continue
        size = path.stat().st_size
        if size == 0:
            continue
        items.append(MediaItem(path=path, kind=kind, size=size))
    return items


def tool_path(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise DownloadError(E_UNKNOWN, f"{name} не установлен (pip install {name})")
    return found


async def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    """Запуск утилиты. Возвращает (код, объединённый вывод)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise DownloadError(E_TIMEOUT, f"утилита не уложилась в {timeout} c")
    return proc.returncode, (out or b"").decode("utf-8", errors="replace")


def check_sizes(items: list[MediaItem], max_mb: int) -> None:
    limit = max_mb * 1024 * 1024
    too_big = [i for i in items if i.size > limit]
    if too_big and len(too_big) == len(items):
        mb = max(i.size for i in too_big) / 1024 / 1024
        raise DownloadError(E_TOO_LARGE, f"{mb:.1f} МБ при лимите {max_mb} МБ")
