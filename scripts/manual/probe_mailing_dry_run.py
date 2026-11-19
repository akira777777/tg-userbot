"""Ручная проверка dry-run рассылки: клиент с заглушками не должен подключаться."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio, argparse, tempfile, pathlib, io, contextlib
import bot  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp())
bot.DB_PATH = tmp / "t.db"
bot.LEGACY_SESSION = str(tmp / "legacy.session")
conn = bot.connect()

class C:
    async def connect(self): raise AssertionError("dry-run must not connect")
    async def send_message(self, *a): raise AssertionError("dry-run must not send")

rows = [{"user_id": i, "username": f"u{i}", "first_name": "Анна", "source_group": 1} for i in range(3)]
args = argparse.Namespace(dry_run=True, delay_min=0, delay_max=0, max_runs=0, opt_out=True)
buf = io.StringIO()
async def run():
    with contextlib.redirect_stdout(buf):
        await bot.send_run(C(), conn, "dm", rows, "Привет, {name}!", args,
            resolve=lambda r: (f"@{r['username']}", bot.dm_peer(r), str(r["user_id"]), bot.dm_context(r)))
asyncio.run(run())
print(buf.getvalue())
print("dry lines:", buf.getvalue().count("[dry]"))
conn.close()