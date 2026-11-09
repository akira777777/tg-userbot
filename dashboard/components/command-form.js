"use client";

import { useState } from "react";

const READ_KINDS = new Set(["STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST"]);
const DELIVERY_KINDS = new Set(["BROADCAST", "DMSEND", "CONDUCT"]);

function payloadFor(kind, text) {
  if (READ_KINDS.has(kind)) return {};
  if (kind === "BLACKLIST_ADD") return { value: text, reason: "mini-app" };
  if (kind === "STOP") return { account: "all" };
  const payload = { text, delay_min: 20, delay_max: 45 };
  if (kind === "DMSEND") payload.limit = 20;
  if (kind === "CONDUCT") payload.kind = "broadcast";
  return payload;
}

export default function CommandForm() {
  const [kind, setKind] = useState("STATUS");
  const [text, setText] = useState("");
  const [live, setLive] = useState(false);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const delivery = DELIVERY_KINDS.has(kind);

  async function submit(event) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    try {
      const mode = READ_KINDS.has(kind) ? "READ" : live ? "LIVE" : "DRY_RUN";
      const payload = payloadFor(kind, text);
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
      setMessage(body.idempotent ? "Такая команда уже есть в очереди" : `Команда ${body.command_id} поставлена в очередь`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Команда не принята");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Управление</h2>
      <label>
        Команда
        <select value={kind} onChange={(event) => setKind(event.target.value)}>
          {["STATUS", "GROUPS", "CAMPAIGNS", "MEDIA_LIST", "BROADCAST", "DMSEND", "CONDUCT", "BLACKLIST_ADD", "STOP"].map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
      </label>
      {!READ_KINDS.has(kind) && kind !== "STOP" && (
        <label>
          {kind === "BLACKLIST_ADD" ? "ID или ник" : "Текст"}
          <textarea value={text} onChange={(event) => setText(event.target.value)} rows={3} />
        </label>
      )}
      {delivery && (
        <label>
          <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} />
          LIVE: реальная отправка после подтверждения
        </label>
      )}
      <button type="submit" disabled={pending}>{pending ? "Отправка…" : live && delivery ? "Подтвердить и запустить" : "Поставить в очередь"}</button>
      {message && <p role="status">{message}</p>}
    </form>
  );
}
