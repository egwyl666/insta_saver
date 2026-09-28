"""Instagram.

gallery-dl основной: лучше живёт с cookies и нормально разбирает карусели
(несколько фото/видео в одном посте). yt-dlp — запасной для рилсов, когда
gallery-dl спотыкается на конкретном посте.

Без cookies публичные посты и рилсы часто отдаются, поэтому очередь сначала
пробует анонимно, а аккаунт тратит только на закрытое, 18+ и сторис.
"""

from __future__ import annotations

from pathlib import Path

from downloaders.base import (
    E_AUTH_EXPIRED,
    E_BLOCKED,
    E_LOGIN,
    E_NOT_FOUND,
    E_PRIVATE,
    E_RATE,
    E_UNKNOWN,
    DownloadError,
    DownloadResult,
    classify,
    collect,
    run,
    tool_path,
)


async def download(url: str, work_dir: Path, cookies: Path | None = None,
                   timeout: int = 420) -> DownloadResult:
    work_dir.mkdir(parents=True, exist_ok=True)

    # --- попытка 1: gallery-dl ---
    cmd = [
        tool_path("gallery-dl"),
        "--quiet", "--no-mtime",
        "--dest", str(work_dir),
        "-o", "directory=[]",
        "-o", "filename={num:>02}_{shortcode|media_id}.{extension}",
        *(["--cookies", str(cookies)] if cookies else []),
        url,
    ]
    code, out = await run(cmd, timeout=timeout)
    items = collect(work_dir)
    if items:
        return DownloadResult(items=items)

    first_error = classify(out)

    # Если дело в доступе — второй утилитой ничего не изменится, не мучаем инсту.
    # Без cookies gallery-dl на логин упирается часто, а yt-dlp рилсы
    # всё равно отдаёт, так что анонимно пропускаем только однозначное.
    final = ({E_PRIVATE, E_NOT_FOUND, E_RATE} if cookies is None else
             {E_PRIVATE, E_LOGIN, E_AUTH_EXPIRED, E_BLOCKED, E_RATE, E_NOT_FOUND})
    if first_error in final:
        raise DownloadError(_anon(first_error, cookies), out[-600:])

    # --- попытка 2: yt-dlp (обычно спасает рилсы) ---
    cmd = [
        tool_path("yt-dlp"),
        "--no-warnings", "--no-progress", "--no-part",
        "--retries", "3", "--socket-timeout", "20",
        *(["--cookies", str(cookies)] if cookies else []),
        "-o", str(work_dir / "%(playlist_index|0)02d_%(id)s.%(ext)s"),
        url,
    ]
    code2, out2 = await run(cmd, timeout=timeout)
    items = collect(work_dir)
    if items:
        return DownloadResult(items=items)

    second_error = classify(out2)
    chosen = second_error if second_error != E_UNKNOWN else first_error
    raise DownloadError(_anon(chosen, cookies), (out2 or out)[-600:])


def _anon(code: str, cookies: Path | None) -> str:
    """Без cookies «редирект на логин» — не протухшая сессия, а просто нужен вход."""
    if cookies is None and code in (E_AUTH_EXPIRED, E_BLOCKED):
        return E_LOGIN
    return code
