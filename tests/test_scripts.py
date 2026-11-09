"""Скрипты обслуживания работают с временной SQLite и не трогают боевую базу."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run_script(name, args, env=None):
    merged = os.environ.copy()
    merged.pop("USERBOT_DB", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [PY, str(ROOT / "scripts" / name), *args],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_healthcheck_missing_db(tmp_path):
    missing = tmp_path / "missing.db"
    result = run_script("healthcheck.py", ["--db", str(missing)])
    assert result.returncode == 1, result.stderr
    assert "нет файла" in result.stderr


def test_healthcheck_and_migrate_roundtrip(tmp_path):
    db = tmp_path / "fresh.db"
    upgraded = run_script("db-migrate.py", ["upgrade", "--db", str(db)])
    assert upgraded.returncode == 0, upgraded.stderr
    assert db.exists()
    current = run_script("db-migrate.py", ["current", "--db", str(db)])
    assert current.returncode == 0, current.stderr
    assert "members" in current.stdout
    healthy = run_script("healthcheck.py", ["--db", str(db)])
    assert healthy.returncode == 0, healthy.stderr
    down = run_script("db-migrate.py", ["downgrade", "--db", str(db)])
    assert down.returncode == 2
    assert "не поддерживается" in down.stderr


def test_backup_roundtrip_and_rotate(tmp_path):
    db = tmp_path / "source.db"
    upgraded = run_script("db-migrate.py", ["upgrade", "--db", str(db)])
    assert upgraded.returncode == 0, upgraded.stderr
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO blacklist(user_id,username,reason,ts) VALUES(1,'','t','2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    out = tmp_path / "backups"
    copied = run_script(
        "backup.py",
        ["--db", "--database", str(db), "--dir", str(out)],
    )
    assert copied.returncode == 0, copied.stderr
    files = list(out.glob("db_*.db"))
    assert len(files) == 1
    check = sqlite3.connect(files[0])
    assert check.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0] == 1
    check.close()

    old = out / "db_20000101_000000.db"
    old.write_bytes(b"not-a-real-backup")
    note = out / "notes.txt"
    note.write_text("keep me", encoding="utf-8")
    rotated = run_script("backup.py", ["--rotate", "--keep", "1", "--dir", str(out)])
    assert rotated.returncode == 0, rotated.stderr
    assert not old.exists()
    assert note.exists()
    assert list(out.glob("db_*.db"))


def test_seed_refuses_live_db_and_fills_a_new_file(tmp_path):
    live = tmp_path / "userbot.db"
    live.write_bytes(b"")
    refused = run_script(
        "db-seed.py",
        ["--db", str(live), "--count", "3"],
        env={"USERBOT_DB": str(live)},
    )
    assert refused.returncode == 2
    assert "боевую" in refused.stderr
    assert live.read_bytes() == b""

    fresh = tmp_path / "fixture.db"
    seeded = run_script("db-seed.py", ["--db", str(fresh), "--count", "3"])
    assert seeded.returncode == 0, seeded.stderr
    conn = sqlite3.connect(fresh)
    try:
        rows = conn.execute("SELECT username FROM members ORDER BY user_id").fetchall()
    finally:
        conn.close()
    assert [row[0] for row in rows] == ["seed_0001", "seed_0002", "seed_0003"]
