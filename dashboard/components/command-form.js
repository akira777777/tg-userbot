"use client";

import { useMemo, useState } from "react";
import { COMMANDS, DELIVERY_KINDS, READ_KINDS, payloadFor } from "../lib/command-kinds";

export default function CommandForm({ onQueued }) {
  const [kind, setKind] = useState("STATUS");
  const [text, setText] = useState("");
  const [live, setLive] = useState(false);
  const [delayMin, setDelayMin] = useState(20);
  const [delayMax, setDelayMax] = useState(45);
  const [limit, setLimit] = useState(20);
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const [pending, setPending] = useState(false);

  const delivery = DELIVERY_KINDS.has(kind);
  const selected = useMemo(() => COMMANDS.find((item) => item.kind === kind), [kind]);
  const needsText = !READ_KINDS.has(kind) && kind !== "STOP";

  async function submit(event) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    try {
      if (needsText && !text.trim()) throw new Error("Заполни текст или идентификатор.");
      const mode = READ_KINDS.has(kind) ? "READ" : live ? "LIVE" : "DRY_RUN";
      const payload = payloadFor(kind, { text, delayMin, delayMax, limit });
      let confirmationNonce;
      if (mode === "LIVE") {
        const confirmation = await fetch("/api/control/confirmations", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ kind, mode, account: "main", payload }),
        });
        const confirmationBody = await confirmation.json();
        if (!confirmation.ok || !confirmationBody.ok) throw new Error(confirmationBody.error || "Подтверждение отклонено");
        confirmationNonce = confirmationBody.nonce;
      }
      const response = await fetch("/api/control/commands", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          kind,
          mode,
          account: "main",
          payload,
          idempotency_key: crypto.randomUUID(),
          confirmation_nonce: confirmationNonce,
        }),
      });
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body.error || "Команда не принята");
      setOk(true);
      setMessage(body.idempotent ? "Такая команда уже есть в очереди" : `Команда поставлена в очередь`);
      setLive(false);
      onQueued?.();
    } catch (error) {
      setOk(false);
      setMessage(error instanceof Error ? error.message : "Команда не принята");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Управление</h2>
      <div className="form-grid">
        <label className="field">
          Команда
          <select value={kind} onChange={(event) => { setKind(event.target.value); setLive(false); setMessage(""); }}>
            {COMMANDS.map((item) => (
              <option key={item.kind} value={item.kind}>{item.label}</option>
            ))}
          </select>
        </label>
        {selected && <p className="hint">{selected.hint}</p>}
        {needsText && (
          <label className="field">
            {kind === "BLACKLIST_ADD" ? "ID или ник" : "Текст"}
            <textarea value={text} onChange={(event) => setText(event.target.value)} rows={3} placeholder={kind === "BLACKLIST_ADD" ? "@user или 123456" : "Сообщение для отправки"} />
          </label>
        )}
        {delivery && (
          <div className="row-2">
            <label className="field">Пауза от, сек<input type="number" min="1" value={delayMin} onChange={(event) => setDelayMin(event.target.value)} /></label>
            <label className="field">Пауза до, сек<input type="number" min="1" value={delayMax} onChange={(event) => setDelayMax(event.target.value)} /></label>
          </div>
        )}
        {kind === "DMSEND" && (
          <label className="field">Лимит получателей<input type="number" min="1" max="50" value={limit} onChange={(event) => setLimit(event.target.value)} /></label>
        )}
        {delivery && (
          <label className="check">
            <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} />
            <span>LIVE: реальная отправка после подтверждения. Без галочки команда уйдёт как dry-run.</span>
          </label>
        )}
      </div>
      <button className={live && delivery ? "primary danger" : "primary"} type="submit" disabled={pending}>
        {pending ? "Отправка…" : live && delivery ? "Подтвердить и запустить" : "Поставить в очередь"}
      </button>
      {message && <p className={ok ? "notice ok" : "notice error"} role="status">{message}</p>}
    </form>
  );
}
