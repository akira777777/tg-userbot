import sys, os, sqlite3, tempfile
from pathlib import Path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
# полная изоляция от продакшн-файлов: своя БД, свои sessions/ и media_cache/
_TMP = tempfile.mkdtemp(prefix="ubb_test_")
os.environ["USERBOT_DB"] = os.path.join(_TMP, "test3.db")
os.environ["USERBOT_SESSIONS"] = os.path.join(_TMP, "sessions")
os.environ["USERBOT_MEDIA"] = os.path.join(_TMP, "media_cache")
import bot, web as W
from datetime import datetime, timezone, timedelta

# самодостаточный dummy session-файл
DUMMY = os.path.join(_TMP, "dummy.session")
with open(DUMMY, "wb") as fh:
    fh.write(os.urandom(200))

conn = bot.connect()

# --- миграция реестра
cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns)")}
assert "account" in cols and "account" in {r["name"] for r in conn.execute("PRAGMA table_info(sent)")}
main = conn.execute("SELECT * FROM accounts WHERE name='main'").fetchone()
assert main and main["session"], main

# --- import аккаунта (ботовская функция через CLI-namespace)
class A: pass
imp = A(); imp.op, imp.name, imp.value, imp.account, imp.purge = \
    "import", "work2", DUMMY, "main", False
bot.cmd_accounts(imp)
row = conn.execute("SELECT * FROM accounts WHERE name='work2'").fetchone()
session = Path(row["session"])
assert row and session.name == "work2.session" and session.parent.name == "sessions", row
import os.path
assert os.path.exists(row["session"])

# повторный import того же файла в ДРУГОЕ имя — должен отказать (1 session = 1 client)
imp2 = A(); imp2.op, imp2.name, imp2.value, imp2.account, imp2.purge = \
    "import", "work3", row["session"], "main", False
try:
    bot.cmd_accounts(imp2); raise AssertionError("clash not detected")
except SystemExit as e:
    assert "уже привязан" in str(e) or "копия" in str(e), e

# --- flood_guard
future = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec="seconds")
conn.execute("UPDATE accounts SET flood_until=? WHERE name='work2'", (future,))
conn.commit()
try:
    bot.flood_guard(conn, "work2"); raise AssertionError("no guard")
except SystemExit as e:
    assert "cooldown" in str(e), e
conn.execute("UPDATE accounts SET flood_until=NULL WHERE name='work2'")

# --- disable -> get_client отказывает (без сети: SystemExit до коннекта)
conn.execute("UPDATE accounts SET enabled=0 WHERE name='work2'"); conn.commit()
try:
    bot.get_client("work2"); raise AssertionError("disabled allowed")
except SystemExit as e:
    assert "отключён" in str(e)
conn.execute("UPDATE accounts SET enabled=1 WHERE name='work2'")

# --- бюджет DM: today_dm_sent + кросс-аккаунтный дедуп
conn.execute("INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(41,'dm','x',?,'work2')",
             (bot.now_iso(),))
conn.execute("INSERT INTO sent(campaign,target,status,ts,account) VALUES(41,'7','ok',?,'work2')",
             (bot.now_iso(),))
conn.execute("INSERT INTO members(user_id,username,is_bot,collected_at,source_group) VALUES(7,'g',0,?,101)",
             (bot.now_iso(),))
conn.execute("INSERT OR IGNORE INTO member_sources(user_id,group_id) VALUES(7,101)")
conn.commit()
assert bot.today_dm_sent(conn, "work2") == 1
assert bot.today_dm_sent(conn, "main") == 0
targets = {r["user_id"] for r in bot.dm_targets(conn)}
assert 7 not in targets, targets  # писал work2 — main не повторит (кросс-дедуп)

# --- recent_ok_targets (broadcast кросс-дедуп 24ч)
conn.execute("INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(42,'broadcast','y',?,'work2')",
             (bot.now_iso(),))
conn.execute("INSERT INTO sent(campaign,target,status,ts,account) VALUES(42,'555','ok',?,'work2')",
             (bot.now_iso(),))
conn.commit()
assert "555" in bot.recent_ok_targets(conn, "broadcast", 24)
old = (datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(timespec="seconds")
conn.execute("INSERT INTO campaigns(id,kind,text,created_at,account) VALUES(43,'broadcast','z',?,'main')", (old,))
conn.execute("INSERT INTO sent(campaign,target,status,ts,account) VALUES(43,'666','ok',?,?)" , (old, old))
conn.commit()
assert "666" not in bot.recent_ok_targets(conn, "broadcast", 24)

# --- web build_args: слоты и валидация аккаунтов
args, slot, acc = W.build_args({"action":"sync","account":"work2"})
assert args == ["groups","--sync","--account","work2"] and slot == "work2"
args, slot, _ = W.build_args({"action":"export"})
assert slot == "db"
try:
    W.build_args({"action":"sync","account":"BAD NAME"}); raise AssertionError
except ValueError as e:
    assert "аккаунт" in str(e)
try:
    W.build_args({"action":"dmsend","text":"t","account":"оскест"}); raise AssertionError
except ValueError as e:
    assert "аккаунт" in str(e)
args, slot, _ = W.build_args({"action":"accounts","op":"import","name":"w9","value":"C:\\x\\a.session"})
assert args == ["accounts","import","w9","C:\\x\\a.session"] and slot == "db"
try:
    W.build_args({"action":"accounts","op":"budget","name":"w9","value":"9999"}); raise AssertionError
except ValueError as e:
    assert "0..500" in str(e)
args, slot, acc = W.build_args({"action":"broadcast","text":"т","dry":True,"account":"main","repeat_ok":True})
assert "--repeat-ok" in args and args[-2:] == ["--account","main"]

st = W.read_state()
assert "error" not in st, st
names = {a["name"] for a in st["accounts"]}
assert {"main","work2"} <= names
w2 = next(a for a in st["accounts"] if a["name"]=="work2")
assert w2["dm_today"] == 1, w2

# cleanup реальных файлов, созданных прогоном
for p in (row["session"], DUMMY):
    try: os.remove(p)
    except OSError: pass
import shutil
shutil.rmtree(_TMP, ignore_errors=True)

print("ALL ORCHESTRA TESTS OK")
