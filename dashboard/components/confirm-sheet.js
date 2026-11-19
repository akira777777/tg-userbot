"use client";

import { useMemo } from "react";
import { conductModeFor, dangerCountFor, delayPairLabel } from "../lib/command-scenarios";

/**
 * Нижняя панель подтверждения LIVE-запуска.
 * Ничего не считает про сервер: показывает те же строки, что и блок
 * «Что произойдёт», плюс явное предупреждение о реальной отправке.
 */
export default function ConfirmSheet({
  open,
  dialogRef,
  kind,
  conductKind,
  title,
  summary,
  showDelay,
  delayMin,
  delayMax,
  pending,
  onDryRun,
  onLive,
  onClose,
}) {
  const kicker = useMemo(
    () => (kind === "CONDUCT" ? conductModeFor(conductKind)?.label : title),
    [kind, conductKind, title],
  );
  if (!open) return null;

  const interval = showDelay ? `, интервал — ${delayPairLabel(delayMin, delayMax)}` : "";

  return (
    <div
      className="sheet-backdrop"
      role="presentation"
      onClick={() => !pending && onClose()}
    >
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-desc"
        ref={dialogRef}
        onClick={(event) => event.stopPropagation()}
      >
        <p className="sheet-kicker">{kicker}</p>
        <h3 id="confirm-title">Отправить в Telegram?</h3>
        <p className="hint" id="confirm-desc">
          Отменить после первого сообщения нельзя. Сообщения начнут уходить сразу{interval}.
        </p>
        <p className="sheet-warn" role="note">
          Реальная отправка {dangerCountFor(kind, conductKind)}: аккаунт будет виден получателям.
        </p>
        <dl className="sheet-facts">
          {summary.map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
        <div className="sheet-actions">
          <button
            type="button"
            className="ghost big"
            onClick={onDryRun}
            disabled={Boolean(pending)}
            data-autofocus
          >
            {pending === "send" ? "…" : "Только проверить"}
          </button>
          <button
            type="button"
            className="primary danger big"
            onClick={onLive}
            disabled={Boolean(pending)}
          >
            {pending === "confirm" ? "Запускаю…" : "Запустить"}
          </button>
        </div>
        <button type="button" className="link" onClick={onClose} disabled={Boolean(pending)}>
          Отмена
        </button>
      </div>
    </div>
  );
}
