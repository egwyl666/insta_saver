"""Instagram.

gallery-dl основной: лучше живёт с cookies и нормально разбирает карусели
(несколько фото/видео в одном посте). yt-dlp — запасной для рилсов, когда
gallery-dl спотыкается на конкретном посте.

Без cookies инста отдаёт крайне мало, поэтому аккаунт тут почти обязателен.
"""

from __future__ import annotations

from pathlib import Path

from downloaders.base import (
    E_LOGIN,
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

    if cookies is None:
        # Технически иногда проходит, но в 9 случаях из 10 упрёмся в логин.
        # Лучше сказать честно сразу, чем ждать таймаут.
        raise DownloadError(E_LOGIN, "нет живого аккаунта в пуле")

    # --- попытка 1: gallery-dl ---
    cmd = [
        tool_path("gallery-dl"),
        "--quiet", "--no-mtime",
        "--dest", str(work_dir),
        "-o", "directory=[]",
        "-o", "filename={num:>02}_{shortcode|media_id}.{extension}",
        "--cookies", str(cookies),
        url,
    ]
    code, out = await run(cmd, timeout=timeout)
    items = collect(work_dir)
    if items:
        return DownloadResult(items=items)

    first_error = classify(out)

    # Если дело в доступе — второй утилитой ничего не изменится, не мучаем инсту.
    if first_error in ("PRIVATE", "LOGIN_REQUIRED", "AUTH_EXPIRED", "ACCOUNT_BLOCKED",
                       "RATE_LIMITED", "NOT_FOUND"):
        raise DownloadError(first_error, out[-600:])

    # --- попытка 2: yt-dlp (обычно спасает рилсы) ---
    cmd = [
        tool_path("yt-dlp"),
        "--no-warnings", "--no-progress", "--no-part",
        "--retries", "3", "--socket-timeout", "20",
        "--cookies", str(cookies),
        "-o", str(work_dir / "%(playlist_index|0)02d_%(id)s.%(ext)s"),
        url,
    ]
    code2, out2 = await run(cmd, timeout=timeout)
    items = collect(work_dir)
    if items:
        return DownloadResult(items=items)

    second_error = classify(out2)
    raise DownloadError(second_error if second_error != "UNKNOWN" else first_error,
                        (out2 or out)[-600:])
