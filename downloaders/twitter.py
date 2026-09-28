"""Twitter / X.

Публичные твиты тянутся без логина. Сначала пробуем yt-dlp (видео),
если видео в твите нет — отдаём картинки через gallery-dl.
"""

from __future__ import annotations

from pathlib import Path

from downloaders.base import (
    E_EMPTY,
    DownloadError,
    DownloadResult,
    classify,
    collect,
    run,
    tool_path,
)


async def download(url: str, work_dir: Path, cookies: Path | None = None,
                   timeout: int = 300) -> DownloadResult:
    work_dir.mkdir(parents=True, exist_ok=True)

    # --- попытка 1: видео ---
    cmd = [
        tool_path("yt-dlp"),
        "--no-warnings", "--no-progress", "--no-part",
        "--retries", "3", "--socket-timeout", "20",
        "-o", str(work_dir / "%(playlist_index|0)02d_%(id)s.%(ext)s"),
    ]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    cmd.append(url)

    code, out = await run(cmd, timeout=timeout)
    items = collect(work_dir)
    if items:
        return DownloadResult(items=items)

    video_error = classify(out) if code != 0 else E_EMPTY

    # --- попытка 2: картинки ---
    cmd = [
        tool_path("gallery-dl"),
        "--quiet", "--no-mtime",
        "--dest", str(work_dir),
        "-o", "directory=[]",
        "-o", "filename={num:>02}_{tweet_id}.{extension}",
    ]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    cmd.append(url)

    code2, out2 = await run(cmd, timeout=timeout)
    items = collect(work_dir)
    if items:
        return DownloadResult(items=items)

    # Обе пустые — сообщаем более информативную из двух ошибок.
    picture_error = classify(out2) if code2 != 0 else E_EMPTY
    chosen = picture_error if picture_error != E_EMPTY else video_error
    raise DownloadError(chosen, (out2 or out)[-600:])
