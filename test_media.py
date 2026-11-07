import json, time, urllib.request, urllib.error, struct, zlib, os

B = os.environ.get("USERBOT_PANEL", "http://127.0.0.1:8642")
IMG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_test.png")

def png(w, h, rgb):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * w
    idat = zlib.compress(row * h, 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))

with open(IMG, "wb") as f:
    f.write(png(400, 240, (26, 156, 249)))
print("test png:", os.path.getsize(IMG), "байт")

def post(path, obj=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else json.dumps(obj).encode()
    ct = ctype if raw is not None else "application/json"
    req = urllib.request.Request(B + path, data=data, headers={"Content-Type": ct})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def get(path, headers=None):
    req = urllib.request.Request(B + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

def state():
    _, _, body = get("/api/state")
    return json.loads(body)

# 1. media add через web API (main -> Избранное)
st, body = post("/api/run", {"action": "media", "op": "add", "value": IMG, "account": "main"})
print("run media add:", st, body.decode()[:150])

for _ in range(40):
    time.sleep(2)
    s = state()
    if not s["jobs"].get("main"):
        break
s = state()
print("media в базе:", [(m["id"], m["kind"], m["title"], m["size"], m["ok"]) for m in s["media"]])
assert s["media"], "после add база пуста"
mid = s["media"][0]["id"]

# 2. раздача файла + Range (веб-плеер)
st, hdr, body = get(f"/api/media/{mid}/file")
print("GET file:", st, hdr.get("Content-Type"), len(body), "байт | Accept-Ranges:", hdr.get("Accept-Ranges"))
assert st == 200 and body[:4] == b"\x89PNG"
st, hdr, body = get(f"/api/media/{mid}/file", {"Range": "bytes=10-19"})
print("RANGE 10-19:", st, "len:", len(body))
assert st == 206 and len(body) == 10

# 3. dry-run broadcast с медиа (без отправки в чаты)
st, body = post("/api/run", {"action": "broadcast", "account": "main", "text": "медиа-тест", "dry": True, "media": str(mid)})
print("broadcast --media dry:", st, body.decode()[:200])
assert st == 200
for _ in range(40):
    time.sleep(2)
    s = state()
    if not s["jobs"].get("main"):
        break
print("done. последняя кампания:", s["last_campaigns"][:1])

# 4. невалидный медиа-id
st, body = post("/api/run", {"action": "broadcast", "account": "main", "text": "x", "dry": True, "media": "9999"})
print("bad media id:", st, body.decode()[:120])

# 5. bad media op
st, body = post("/api/run", {"action": "media", "op": "drop table"})
print("bad op:", st, body.decode()[:120])

# 6. del записи (файл остаётся на диске)
st, body = post("/api/run", {"action": "media", "op": "del", "value": str(mid)})
print("del:", st, body.decode()[:80])
for _ in range(10):
    time.sleep(1.5)
    s = state()
    if not s["jobs"].get("db"):
        break
print("media после del:", s["media"])
print("CYCLE OK")
