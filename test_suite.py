import sys, os, sqlite3, tempfile
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
# изоляция от продакшн-БД: временный каталог (как в test_orchestra)
_TMP = tempfile.mkdtemp(prefix="ubb_suite_")
os.environ["USERBOT_DB"] = os.path.join(_TMP, "test2.db")
os.environ["USERBOT_SESSIONS"] = os.path.join(_TMP, "sessions")
os.environ["USERBOT_MEDIA"] = os.path.join(_TMP, "media_cache")
import bot

conn = bot.connect()

# indexes present
names = {r["name"] for r in conn.execute("PRAGMA index_list(member_sources)")}
assert any(n.startswith("idx_sources") for n in names), names
jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
assert jm == "wal", jm

conn.execute("INSERT INTO groups(id,title,username,gtype,added_at) VALUES(101,'G1','g1','megagroup',?)", (bot.now_iso(),))
conn.execute("INSERT INTO groups(id,title,username,gtype,added_at) VALUES(102,'G2','','group',?)", (bot.now_iso(),))
for uid, un, botf in [(1,'a',0),(2,'b',0),(3,'c',1),(4,'d',0),(5,'e',0)]:
    conn.execute("INSERT INTO members(user_id,username,first_name,last_name,is_bot,collected_at,source_group)"
                 " VALUES(?,?,?,?,?,?,?)", (uid, un, un, '', botf, bot.now_iso(), 101))
    conn.execute("INSERT INTO member_sources(user_id,group_id) VALUES(?,101)", (uid,))
conn.execute("INSERT INTO campaigns(id,kind,text,created_at) VALUES(9,'dm','old',?)", (bot.now_iso(),))
conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(9,'5','ok',?)", (bot.now_iso(),))
conn.execute("INSERT INTO sent(campaign,target,status,ts) VALUES(9,'4','fail:X',?)", (bot.now_iso(),))
conn.commit()

t = bot.dm_targets(conn)
assert {r['user_id'] for r in t} == {1,2,4}, [r['user_id'] for r in t]  # 3 bot, 5 sent-ok
tg = bot.dm_targets(conn, group_id=999)
assert tg == []

# blacklist by nick resolves to real id
class A: pass
a = A(); a.cmd, a.value, a.reason = "add", "@D", "web"
bot.cmd_blacklist(a)
bl = [dict(r) for r in conn.execute("SELECT * FROM blacklist")]
assert len(bl)==1 and bl[0]["user_id"]==4 and bl[0]["username"]=="d", bl  # uid 4 was 'd'
t2 = {r['user_id'] for r in bot.dm_targets(conn)}
assert t2 == {1,2}, t2  # 4 now blacklisted

# unknown nick -> SystemExit
try:
    a.value = "@ghost"; bot.cmd_blacklist(a); raise AssertionError("no exit")
except SystemExit as e:
    assert "не найден" in str(e), e

# pick_target: entity from dialogs wins; fallback peers by gtype
class Dummy: pass
d = Dummy(); d.id = 101
obj = bot.pick_target({101: d}, {"id":101,"gtype":"megagroup"})
assert obj is d
p = bot.pick_target({}, {"id":101,"gtype":"megagroup"})
from telethon.tl.types import PeerChannel, PeerChat
assert isinstance(p, PeerChannel) and p.channel_id == 101, p
p2 = bot.pick_target({}, {"id":102,"gtype":"group"})
assert isinstance(p2, PeerChat) and p2.chat_id == 102, p2

# build_args hardening (возвращает (argv, slot, account))
import web as w
def expect_err(payload, needle):
    try:
        w.build_args(payload); raise AssertionError("no error: %r" % payload)
    except ValueError as e:
        assert needle in str(e), (e, needle)
expect_err({"action":"rm -rf"}, "неизвестное действие")
expect_err({"action":"broadcast","dry":False,"text":"  "}, "текст или медиа")
expect_err({"action":"broadcast","dry":True,"text":""}, "текст или медиа")
expect_err({"action":"blacklist_add","value":"-rf /"}, "только @ник")
expect_err({"action":"dmsend","text":"x","delay_min":"abc"}, "задержки")
r, slot, _ = w.build_args({"action":"blacklist_add","value":"@some_nick","reason":"-x"})
assert r == ["blacklist","add","@some_nick","--reason"," -x"] and slot == "db", r
r, slot, _ = w.build_args({"action":"dmsend","text":"привет","dry":True,"limit":5,"group":"101",
                           "repeat_ok":True,"delay_min":1,"delay_max":2,"account":"work2"})
assert r[:2]==["dmsend","--text"] and "--dry-run" in r and "--group" in r and "--repeat-ok" in r \
    and r[-2:]==["--account","work2"] and slot=="work2", (r, slot)
r, slot, _ = w.build_args({"action":"broadcast","text":"t"*4000,"dry":False})
assert len(r[r.index("--text")+1]) == w.MAX_TEXT
# JSON body must not be executable as shell: verify argv is a plain list of strings
assert all(isinstance(x, str) for x in r)

print("ALL LOGIC TESTS OK")
