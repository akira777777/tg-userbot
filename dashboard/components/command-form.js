"use client";

import { useMemo, useState } from "react";
import { COMMANDS, DELIVERY_KINDS, READ_KINDS, payloadFor } from "../lib/command-kinds";

export default function CommandForm({ accounts = [], groups = [], onQueued }) {
  const [kind, setKind] = useState("STATUS");
  const [text, setText] = useState("");
  const [live, setLive] = useState(false);
  const [delayMin, setDelayMin] = useState(20);
  const [delayMax, setDelayMax] = useState(45);
  const [limit, setLimit] = useState(20);
  const [account, setAccount] = useState("main");
  const [conductKind, setConductKind] = useState("broadcast");
  const [groupId, setGroupId] = useState("");
  const [message, setMessage] = useState("");
  const [ok, setOk] = useState(false);
  const [pending, setPending] = useState(false);

  const delivery = DELIVERY_KINDS.has(kind);
  const selected = useMemo(() => COMMANDS.find((item) => item.kind === kind), [kind]);
  const needsText = !READ_KINDS.has(kind) && kind !== "STOP";
  const realAccounts = useMemo(
    () => (accounts || []).filter((item) => item?.name),
    [accounts],
  );
  const accountOptions = useMemo(() => {
    const options = [];
    if (kind === "STOP" || kind === "CONDUCT") options.push({ value: "all", label: "Все аккаунты" });
    realAccounts.forEach((item, index) => options.push({ value: item.name, label: `Аккаунт ${index + 1}` }));
    if (!options.length) options.push({ value: "main", label: "Аккаунт 1" });
    return options;
  }, [kind, realAccounts]);
  const selectedAccount = accountOptions.some((item) => item.value === account)
    ? account
    : accountOptions[0].value;
  const showLimit = kind === "DMSEND" || (kind === "CONDUCT" && conductKind === "dm");
  const showGroup = delivery && groups.length > 0;

  async function submit(event) {
    event.preventDefault();
    setPending(true);
    setMessage("");
    try {
      if (needsText && !text.trim()) throw new Error("Заполни текст или идентификатор.");
      const mode = READ_KINDS.has(kind) ? "READ" : live ? "LIVE" : "DRY_RUN";
      const payload = payloadFor(kind, { text, delayMin, delayMax, limit, account: selectedAccount, conductKind, groupId });
      const requestAccount = kind === "CONDUCT" && selectedAccount === "all"
        ? "all"
        : selectedAccount === "all"
          ? (realAccounts[0]?.name || "main")
          : selectedAccount;
      let confirmationNonce;
      if (mode === "LIVE") {
        const confirmation = await fetch("/api/control/confirmations", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ kind, mode, account: requestAccount, payload }),
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
          account: requestAccount,
          payload,
          idempotency_key: crypto.randomUUID(),
          confirmation_nonce: confirmationNonce,
        }),
      });
      const body = await response.json();
      if (!response.ok || !body.ok) throw new Error(body.error || "Команда не принята");
      setOk(true);
      setMessage(body.idempotent ? "Такая команда уже есть в очереди" : "Команда поставлена в очередь");
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
        <label className="field">
          Аккаунт
          <select value={selectedAccount} onChange={(event) => setAccount(event.target.value)}>
            {accountOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        {selected && <p className="hint">{selected.hint}</p>}
        {kind === "CONDUCT" && (
          <label className="field">
            Тип кампании
            <select value={conductKind} onChange={(event) => setConductKind(event.target.value)}>
              <option value="broadcast">Рассылка в группы</option>
              <option value="dm">Личные сообщения</option>
            </select>
          </label>
        )}
        {needsText && (
          <label className="field">
            {kind === "BLACKLIST_ADD" ? "ID или ник" : "Текст"}
            <textarea value={text} onChange={(event) => setText(event.target.value)} rows={3} placeholder={kind === "BLACKLIST_ADD" ? "@user или 123456" : "Сообщение для отправки"} />
          </label>
        )}
        {showGroup && (
          <label className="field">
            Группа
            <select value={groupId} onChange={(event) => setGroupId(event.target.value)}>
              <option value="">Все подходящие</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>{group.title || group.id}</option>
              ))}
            </select>
          </label>
        )}
        {delivery && kind !== "CONDUCT" && (
          <div className="row-2">
            <label className="field">Пауза от, сек<input type="number" min="1" value={delayMin} onChange={(event) => setDelayMin(event.target.value)} /></label>
            <label className="field">Пауза до, сек<input type="number" min="1" value={delayMax} onChange={(event) => setDelayMax(event.target.value)} /></label>
          </div>
        )}
        {showLimit && (
          <label className="field">Лимит получателей<input type="number" min="1" max="200" value={limit} onChange={(event) => setLimit(event.target.value)} /></label>
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
