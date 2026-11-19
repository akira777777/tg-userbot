"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { commandLabel, payloadFor } from "../lib/command-kinds";
import { LIMITS, accountOptions, clampLimit, filterGroups } from "../lib/command-options";
import {
  CONDUCT_MODES,
  conductModeFor,
  delayPairLabel,
  formFields,
  previewText,
  primaryLabel,
  scenarioFor,
} from "../lib/command-scenarios";
import { validateCommand } from "../lib/command-validation";
import { useConfirmSheet, useQueueCommand } from "../lib/use-queue-command";
import { hapticImpact } from "../lib/telegram";
import ConfirmSheet from "./confirm-sheet";
import ScenarioPicker from "./scenario-picker";

const TEXT_LIMIT = 4000;

export default function CommandForm({ accounts = [], groups = [], onQueued, queuedCommandId = "" }) {
  const [kind, setKind] = useState("STATUS");
  const [text, setText] = useState("");
  const [delayMin, setDelayMin] = useState(LIMITS.delayMin.fallback);
  const [delayMax, setDelayMax] = useState(LIMITS.delayMax.fallback);
  const [limit, setLimit] = useState(LIMITS.dm.fallback);
  const [account, setAccount] = useState("");
  const [conductKind, setConductKind] = useState("broadcast");
  const [groupQuery, setGroupQuery] = useState("");
  const [groupId, setGroupId] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const formRef = useRef(null);
  const textRef = useRef(null);
  const { pending, message, setMessage, submit, lastQueuedAt } = useQueueCommand();
  const closeSheet = useMemo(() => () => setConfirmOpen(false), []);
  const dialogRef = useConfirmSheet(confirmOpen && !pending, closeSheet);

  const fields = useMemo(() => formFields(kind, conductKind), [kind, conductKind]);
  const hint = scenarioFor(kind)?.hint || "";
  const commandTitle = commandLabel(kind);

  const accountsForKind = useMemo(
    () => accountOptions(accounts, { allowAll: kind === "STOP" || kind === "CONDUCT" }),
    [accounts, kind],
  );
  const selectedAccount = accountsForKind.some((item) => item.value === account)
    ? account
    : accountsForKind[0]?.value;
  const accountLabel = accountsForKind.find((item) => item.value === selectedAccount)?.label || "Аккаунт 1";

  const visibleGroups = useMemo(() => filterGroups(groups, groupQuery), [groups, groupQuery]);
  const groupLabel = groups.find((group) => String(group.id) === String(groupId))?.title || "Все подходящие";

  const limitSpec = fields.showConduct
    ? (conductKind === "both" ? LIMITS.conductBoth : LIMITS.conductDm)
    : LIMITS.dm;

  const payload = useMemo(
    () => payloadFor(kind, { text, delayMin, delayMax, limit, account: selectedAccount, conductKind, groupId }),
    [kind, text, delayMin, delayMax, limit, selectedAccount, conductKind, groupId],
  );

  const summary = useMemo(() => summarize({
    kind, text, fields, conductKind, accountLabel, selectedAccount, groupLabel, limit, delayMin, delayMax,
  }), [kind, text, fields, conductKind, accountLabel, selectedAccount, groupLabel, limit, delayMin, delayMax]);

  const error = validateCommand({ kind, conductKind, text, limit, delayMin, delayMax, fields });

  function resetOnKindChange() {
    setMessage(null);
    setConfirmOpen(false);
    setGroupQuery("");
    setGroupId("");
  }

  function chooseScenario(nextKind) {
    if (nextKind === kind) return;
    setKind(nextKind);
    resetOnKindChange();
    if (nextKind === "DMSEND") setLimit(LIMITS.dm.fallback);
    if (nextKind === "CONDUCT") {
      setLimit(conductKind === "both" ? LIMITS.conductBoth.fallback : LIMITS.conductDm.fallback);
    }
    const nextFields = formFields(nextKind, conductKind);
    // Фокус на главное поле нового сценария: текст или группа.
    window.setTimeout(() => {
      if (nextKind === "BLACKLIST_ADD" || nextFields.needsText) textRef.current?.focus();
    }, 60);
  }

  function chooseConductMode(mode) {
    hapticImpact("light");
    setConductKind(mode);
    setMessage(null);
    setLimit(mode === "both" ? LIMITS.conductBoth.fallback : LIMITS.conductDm.fallback);
    if (mode === "dm" && !groups.length) setGroupId("");
  }

  async function send(mode) {
    const ok = await submit({ kind, mode, account: selectedAccount, payload, accounts });
    if (!ok) return;
    setConfirmOpen(false);
    if (mode !== "READ") {
      setText("");
      formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    onQueued?.();
  }

  function onSubmit(event) {
    event.preventDefault();
    if (error) {
      setMessage({ tone: "error", text: error });
      hapticImpact("rigid");
      textRef.current?.focus();
      return;
    }
    if (fields.isDangerous) {
      setConfirmOpen(true);
      return;
    }
    send(fields.isRead ? "READ" : "LIVE");
  }

  // «Ещё раз» из очереди: подвести пользователя к форме.
  useEffect(() => {
    if (!queuedCommandId) return;
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [queuedCommandId]);

  return (
    <section className="panel" aria-labelledby="control-title" ref={formRef}>
      <div className="panel-head">
        <h2 id="control-title">Управление</h2>
        <span className={`chip ${chipTone(fields, kind)}`}>{chipLabel(fields, kind)}</span>
      </div>

      <ScenarioPicker kind={kind} onChange={chooseScenario} />
      <p className="hint section-hint" id="scenario-hint">{hint}</p>

      {!accounts.length && (
        <p className="inline-note" role="note">
          В облаке пока нет аккаунтов — команды уйдут в воркер, но выполнить их нечем.
        </p>
      )}

      <form className="form-grid" onSubmit={onSubmit} noValidate aria-describedby="scenario-hint">
        {fields.showAccount && (
          <label className="field">
            Аккаунт
            <select value={selectedAccount || ""} onChange={(event) => setAccount(event.target.value)}>
              {accountsForKind.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                  {item.value !== "all" && !item.enabled ? ` · ${item.note || "недоступен"}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {fields.showConduct && (
          <fieldset className="segmented" aria-label="Тип кампании">
            {CONDUCT_MODES.map((mode) => (
              <button
                key={mode.id}
                type="button"
                className={`segment${conductKind === mode.id ? " active" : ""}`}
                aria-pressed={conductKind === mode.id}
                onClick={() => chooseConductMode(mode.id)}
                title={mode.hint}
              >
                <span className="seg-full">{mode.label}</span>
                <span className="seg-short" aria-hidden="true">{mode.short}</span>
              </button>
            ))}
          </fieldset>
        )}

        {fields.needsText && (
          <label className="field">
            {fields.isBlacklist ? "Кого исключить" : "Текст сообщения"}
            {fields.isBlacklist ? (
              <input
                type="text"
                ref={textRef}
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder="@username или 123456789"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={Boolean(text) && Boolean(error)}
              />
            ) : (
              <>
                <textarea
                  ref={textRef}
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  rows={4}
                  maxLength={TEXT_LIMIT}
                  placeholder="Что отправить получателям"
                />
                <span className="field-foot">
                  <span>{text.trim().length} из {TEXT_LIMIT}</span>
                </span>
              </>
            )}
          </label>
        )}

        {fields.showGroups && (
          <div className="field">
            <span className="field-label">Группа</span>
            {groups.length > 6 && (
              <input
                type="search"
                value={groupQuery}
                onChange={(event) => setGroupQuery(event.target.value)}
                placeholder="Поиск по названию"
                aria-label="Поиск группы"
              />
            )}
            <select value={groupId} onChange={(event) => setGroupId(event.target.value)}>
              <option value="">Все подходящие ({groups.length})</option>
              {visibleGroups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.title || group.id}
                  {group.members_collected ? ` · ${new Intl.NumberFormat("ru-RU").format(group.members_collected)}` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {fields.showDelay && (
          <div className="row-2">
            <label className="field">
              Пауза от, сек
              <input type="number" inputMode="numeric" min="1" max="3600" value={delayMin}
                onChange={(event) => setDelayMin(event.target.value)} />
            </label>
            <label className="field">
              Пауза до, сек
              <input type="number" inputMode="numeric" min="1" max="3600" value={delayMax}
                onChange={(event) => setDelayMax(event.target.value)} />
            </label>
          </div>
        )}

        {fields.showLimit && (
          <label className="field">
            Не больше получателей
            <input
              type="number"
              inputMode="numeric"
              min={limitSpec.min}
              max={limitSpec.max}
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
              onBlur={() => setLimit(clampLimit(limit, limitSpec.min, limitSpec.max, limitSpec.fallback))}
            />
            <span className="field-foot">
              <span>Допустимо {limitSpec.min}–{limitSpec.max}</span>
            </span>
          </label>
        )}

        <div className="preview" aria-live="polite">
          <p className="preview-title">Что произойдёт</p>
          <dl>
            {summary.map((row) => (
              <div key={row.label}>
                <dt>{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
        </div>

        {error && (kind !== "BLACKLIST_ADD" || Boolean(text.trim())) && (
          <p className="field-error" role="alert">{error}</p>
        )}

        <button
          className={`primary${fields.isDangerous ? " danger-outline" : ""}`}
          type="submit"
          disabled={Boolean(pending) || Boolean(error)}
          aria-busy={Boolean(pending)}
        >
          {pending ? "Отправляю…" : primaryLabel(kind)}
        </button>
        {fields.isDangerous && (
          <p className="hint">
            Сначала проверка без отправки — ничего не уходит в Telegram. Реальный запуск подтверждается на следующем шаге.
          </p>
        )}
      </form>

      {message && (
        <p className={`notice ${message.tone === "ok" ? "ok" : "error"}`} role="status" key={lastQueuedAt}>
          {message.text}
        </p>
      )}

      <ConfirmSheet
        open={confirmOpen}
        dialogRef={dialogRef}
        kind={kind}
        conductKind={conductKind}
        title={commandTitle}
        summary={summary}
        showDelay={fields.showDelay}
        delayMin={delayMin}
        delayMax={delayMax}
        pending={pending}
        onDryRun={() => send("DRY_RUN")}
        onLive={() => send("LIVE")}
        onClose={closeSheet}
      />
    </section>
  );
}

function chipTone(fields, kind) {
  if (fields.isRead) return "dim";
  if (kind === "STOP") return "err";
  if (fields.isDangerous) return "info";
  return "warn";
}

function chipLabel(fields, kind) {
  if (fields.isRead) return "только чтение";
  if (kind === "STOP") return "опасно";
  if (fields.isDangerous) return "отправка";
  return "без рассылки";
}

/** Строки блока «Что произойдёт» — без секретов и внутренних имён аккаунтов. */
function summarize(options) {
  const { kind, text, fields, conductKind, accountLabel, selectedAccount, groupLabel, limit, delayMin, delayMax } = options;

  if (kind === "BLACKLIST_ADD") {
    return [
      { label: "Кто", value: previewText(text, 60) },
      { label: "Что будет", value: "Исключён из всех будущих рассылок" },
      { label: "Действие", value: "Не отправляет сообщений" },
    ];
  }
  if (fields.isRead) {
    return [
      { label: "Аккаунт", value: accountLabel },
      { label: "Действие", value: "Только чтение, ничего не отправляется" },
    ];
  }
  if (kind === "STOP") {
    const target = selectedAccount === "all" ? "все аккаунты" : `«${accountLabel.toLowerCase()}»`;
    return [
      { label: "Действие", value: `Остановить отправку: ${target}` },
      { label: "Что будет", value: "Текущая очередь и запуски воркера прекратят работу" },
    ];
  }
  const rows = [
    { label: "Текст", value: previewText(text) },
    { label: "Аккаунт", value: accountLabel },
  ];
  if (fields.showConduct) rows.push({ label: "Канал", value: conductModeFor(conductKind)?.label || "" });
  if (fields.showGroups) rows.push({ label: "Группа", value: groupLabel });
  if (fields.showLimit) rows.push({ label: "Получателей", value: `до ${limit}` });
  if (fields.showDelay) rows.push({ label: "Пауза", value: delayPairLabel(delayMin, delayMax) });
  return rows;
}
