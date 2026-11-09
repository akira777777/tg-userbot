"""Копия SQLite-базы и каталога медиа.

Сессии Telegram и .env скрипт не копирует. Postgres и Redis здесь нет:
база — один файл SQLite, копия снимается через API backup (безопасно при WAL).

    .\\.venv\\Scripts\\python.exe scripts\\backup.py --db
    .\\.venv\\Scripts\\python.exe scripts\\backup.py --db --media --gzip
    .\\.venv\\Scripts\\python.exe scripts\\backup.py --rotate --keep 14
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_NAME = re.compile(r"^(db_\d{8}_\d{6}\.db(\.gz)?|media_\d{8}_\d{6}\.zip)$")


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def database_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("USERBOT_DB")
    if env:
        return Path(env).expanduser().resolve()
    return (ROOT / "userbot.db").resolve()


def media_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("USERBOT_MEDIA")
    if env:
        return Path(env).expanduser().resolve()
    return (ROOT / "media_cache").resolve()


def backup_sqlite(src: Path, dest: Path) -> None:
    if not src.exists():
        raise SystemExit(f"нет базы: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dest)
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
        check = dst_conn.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise SystemExit(f"копия не прошла integrity_check: {dest}")
    finally:
        dst_conn.close()
        src_conn.close()
    if not dest.exists() or dest.stat().st_size <= 0:
        raise SystemExit(f"файл копии пуст: {dest}")


def gzip_file(path: Path) -> Path:
    gz_path = Path(str(path) + ".gz")
    with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return gz_path


def backup_media(src: Path, dest: Path) -> int:
    if not src.exists():
        raise SystemExit(f"нет каталога медиа: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in src.rglob("*"):
            if not file.is_file():
                continue
            if file.suffix == ".session" or file.name.endswith(".session-journal"):
                continue
            if file.name == ".env":
                continue
            archive.write(file, file.relative_to(src).as_posix())
            count += 1
    return count


def rotate_backups(directory: Path, keep: int) -> int:
    if keep < 1:
        raise SystemExit("--keep должен быть >= 1")
    if not directory.exists():
        print(f"каталог копий ещё не создан: {directory}")
        return 0
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and BACKUP_NAME.match(path.name)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        old.unlink()
        print(f"удалён старый файл: {old.name}")
        removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Копия SQLite и медиа tg-userbot.")
    parser.add_argument("--db", action="store_true", help="снять копию SQLite")
    parser.add_argument("--database", help="какой файл базы копировать")
    parser.add_argument("--media", action="store_true", help="zip каталога медиа, без сессий")
    parser.add_argument("--media-dir", help="каталог медиа; по умолчанию USERBOT_MEDIA")
    parser.add_argument("--gzip", action="store_true", help="сжать копию базы gzip")
    parser.add_argument("--dir", default=str(ROOT / "backups"), help="куда класть копии")
    parser.add_argument("--rotate", action="store_true", help="оставить только --keep свежих копий")
    parser.add_argument("--keep", type=int, default=14, help="сколько копий хранить при --rotate")
    args = parser.parse_args(argv)
    if not (args.db or args.media or args.rotate):
        parser.error("укажите --db, --media или --rotate")

    out = Path(args.dir).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.db:
        src = database_path(args.database)
        dest = out / f"db_{stamp}.db"
        backup_sqlite(src, dest)
        if args.gzip:
            dest = gzip_file(dest)
        print(f"база: {dest}")
    if args.media:
        dest = out / f"media_{stamp}.zip"
        count = backup_media(media_path(args.media_dir), dest)
        print(f"медиа: {dest} ({count} файлов)")
    if args.rotate:
        removed = rotate_backups(out, args.keep)
        print(f"ротация: удалено {removed}, хранить {args.keep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
