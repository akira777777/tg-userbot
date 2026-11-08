"use client";
import { useCallback, useEffect, useState } from "react";

const REFRESH_MS = 15000;

function Chip({ kind, children }) {
  return <span className={"chip " + kind}>{children}</span>;
}

function statValue(stats, key) {
  const s = (stats || []).find((x) => x.key === key);
  return s ? s.value : "—";
}

export default function Home() {
  const [token, setToken] = useState(null);
  const [input, setInput] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");

  useEffect(() => {
    const t = typeof window !== "undefined" ? localStorage.getItem("dash_token") : null;
    if (t) setToken(t);
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const r = await fetch("/api/data", { headers: { Authorization: "Bearer " + token } });
      const j = await r.json();
      if (!r.ok || !j.ok) {
        if (r.status === 401) {
          localStorage.removeItem("dash_token");
          setToken(null);
        }
        setError(j.error || ("HTTP " + r.status));
        return;
      }
      setError("");
      setData(j);
      setUpdatedAt(new Date().toLocaleTimeString());
    } catch (e) {
      setError(String(e));
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [token, load]);

  if (!token) {
    return (
      <div className="login card">
        <h1>tg-userbot · оркестр</h1>
        <p className="meta">Облачное зеркало. Нужен токен доступа (DASH_TOKEN из .env).</p>
        <input value={input} onChange={(e) => setInput(e.target.value)}
               placeholder="токен" type="password"
               onKeyDown={(e) => {
                 if (e.key === "Enter" && input) {
                   localStorage.setItem("dash_token", input);
                   setToken(input);
                 }
               }} />
        <button onClick={() => { if (input) { localStorage.setItem("dash_token", input); setToken(input); } }}>
          Войти
        </button>
        {error && <p className="meta" style={{ color: "#ff8fa3" }}>{error}</p>}
      </div>
    );
  }

  const d = data && data.data;
  const accs = (d && d.accounts) || [];
  const groups = (d && d.groups) || [];
  const campaigns = (d && d.campaigns) || [];
  const sent = (d && d.sent) || [];
  const stats = (d && d.stats) || [];
  const pushedAt = statValue(stats, "pushed_at");
  const stale = pushedAt !== "—" &&
    (Date.now() - Date.parse(pushedAt)) > 6 * 3600 * 1000;

  return (
    <div className="wrap">
      <h1>tg-userbot · оркестр {data &&
        <span className="meta" style={{ fontWeight: 400 }}> — обновлено {updatedAt}
          {pushedAt !== "—" && <> · push {pushedAt.replace("T", " ").slice(0, 16)}</>}</span>}
      </h1>

      {data && data.schema_missing &&
        <div className="banner">Таблицы <b>tb_*</b> ещё не созданы в Supabase — выполни <span className="mono">supabase/schema.sql</span> в SQL Editor, затем <span className="mono">bot.py cloud push</span>.</div>}
      {data && !data.schema_missing && accs.length === 0 &&
        <div className="banner">Зеркало пустое — выполни <span className="mono">bot.py cloud push</span>.</div>}
      {error && <div className="banner">{error}</div>}
      {stale && <div className="banner">Последний push старше 6 часов — данные могли устареть.</div>}

      {d && (
        <>
          <h2>Обзор</h2>
          <div className="grid">
            <div className="card"><div className="n">{statValue(stats, "members_total")}</div><div className="l">участников собрано</div></div>
            <div className="card"><div className="n">{statValue(stats, "groups_total")}</div><div className="l">групп</div></div>
            <div className="card"><div className="n">{statValue(stats, "groups_broadcast")}</div><div className="l">в рассылке (активные)</div></div>
            <div className="card"><div className="n">{statValue(stats, "sent_ok_total")}</div><div className="l">отправлено ok</div></div>
            <div className="card"><div className="n">{statValue(stats, "sent_fail_total")}</div><div className="l">ошибок доставки</div></div>
            <div className="card"><div className="n">{statValue(stats, "blacklist_n")}</div><div className="l">в blacklist</div></div>
            <div className="card"><div className="n">{statValue(stats, "media_n")}</div><div className="l">медиа в библиотеке</div></div>
          </div>

          <h2>Аккаунты</h2>
          <div className="card"><table>
            <thead><tr><th>имя</th><th>покрывает</th><th>пишут</th><th>DM сегодня</th><th>бюджет</th><th>flood до</th><th>статус</th></tr></thead>
            <tbody>{accs.map((a) => (
              <tr key={a.name}>
                <td className="mono">{a.name}</td>
                <td>{a.groups_covered}</td><td>{a.writable_groups}</td>
                <td>{a.dm_today} / {a.daily_budget}</td>
                <td>{a.daily_budget}</td>
                <td className="meta">{a.flood_until || "—"}</td>
                <td>{a.enabled ? <Chip kind="ok">активен</Chip> : <Chip kind="dim">выключен</Chip>}</td>
              </tr>))}
              {!accs.length && <tr><td colSpan="7" className="meta">нет данных</td></tr>}
            </tbody>
          </table></div>

          <h2>Кампании</h2>
          <div className="card"><table>
            <thead><tr><th>#</th><th>тип</th><th>аккаунт</th><th>текст</th><th>ok</th><th>fail</th><th>когда</th></tr></thead>
            <tbody>{campaigns.map((c) => (
              <tr key={c.id}>
                <td>{c.id}</td><td>{c.kind}</td><td className="mono">{c.account}</td>
                <td>{(c.text || "").slice(0, 60)}</td>
                <td><Chip kind="ok">{c.ok}</Chip></td>
                <td>{c.fail ? <Chip kind="err">{c.fail}</Chip> : "0"}</td>
                <td className="meta">{(c.created_at || "").replace("T", " ").slice(0, 16)}</td>
              </tr>))}
              {!campaigns.length && <tr><td colSpan="7" className="meta">пусто</td></tr>}
            </tbody>
          </table></div>

          <h2>Живой журнал (последние {sent.length})</h2>
          <div className="card"><table>
            <thead><tr><th>кампания</th><th>цель</th><th>аккаунт</th><th>статус</th><th>время</th></tr></thead>
            <tbody>{sent.map((s, i) => (
              <tr key={i}>
                <td>{s.campaign}</td><td>{s.target}</td><td className="mono">{s.account}</td>
                <td>{s.status === "ok" ? <Chip kind="ok">ok</Chip> : <Chip kind="warn">{s.status}</Chip>}</td>
                <td className="meta">{(s.ts || "").replace("T", " ").slice(5, 16)}</td>
              </tr>))}
              {!sent.length && <tr><td colSpan="5" className="meta">пусто</td></tr>}
            </tbody>
          </table></div>

          <h2>Группы ({groups.length})</h2>
          <div className="card"><table>
            <thead><tr><th>id</th><th>название</th><th>тип</th><th>участников</th><th>права</th><th>в рассылке</th></tr></thead>
            <tbody>{groups.map((g) => (
              <tr key={g.id}>
                <td className="meta">{g.id}</td>
                <td>{g.title}{g.username ? <span className="meta"> @{g.username}</span> : null}</td>
                <td>{g.gtype}</td>
                <td>{g.members_collected}</td>
                <td>{g.can_write_agg === "yes" ? <Chip kind="ok">пишут</Chip>
                  : g.can_write_agg === "mixed" ? <Chip kind="warn">частично</Chip>
                  : <Chip kind="dim">нет</Chip>}</td>
                <td>{g.in_broadcast ? "✅" : "—"}</td>
              </tr>))}
              {!groups.length && <tr><td colSpan="6" className="meta">пусто</td></tr>}
            </tbody>
          </table></div>
        </>
      )}
    </div>
  );
}
