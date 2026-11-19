"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { resolveRequestAccount } from "./command-options";
import { controlFetch } from "./control-client";
import { friendlyError } from "./format";
import { newIdempotencyKey } from "./idempotency";
import { hapticImpact, hapticNotify } from "./telegram";

function queuedSuccessText(kind, mode, idempotent) {
  if (idempotent) return "Такая команда уже есть в очереди — дубликат не создан.";
  if (kind === "STOP") return "Команда остановки поставлена в очередь.";
  if (kind === "BLACKLIST_ADD") return "Исключение добавлено в очередь.";
  if (mode === "LIVE") return "Запущено. Статус появится в очереди ниже.";
  if (mode === "DRY_RUN") return "Проверка поставлена в очередь.";
  return "Запрос отправлен воркеру.";
}

/**
 * Постановка команды в очередь.
 * LIVE всегда идёт через одноразовый nonce: сервер выдаёт его на ту же
 * нормализованную полезную нагрузку, поэтому «подтвердить» на одном экране
 * нельзя, а отправить другое — тоже.
 */
export function useQueueCommand() {
  const [pending, setPending] = useState(""); // "" | "send" | "confirm"
  const [message, setMessage] = useState(null); // { tone: "ok" | "error", text }
  const [lastQueuedAt, setLastQueuedAt] = useState(0);

  const submit = useCallback(async ({ kind, mode, account, payload, accounts }) => {
    setPending(mode === "LIVE" ? "confirm" : "send");
    setMessage(null);
    try {
      const requestAccount = resolveRequestAccount(kind, account, accounts);
      const needsConfirm = mode === "LIVE" && ["BROADCAST", "DMSEND", "CONDUCT"].includes(kind);
      let confirmationNonce;
      if (needsConfirm) {
        const { response: confirmation, body: confirmationBody } = await controlFetch("/api/control/confirmations", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ kind, mode, account: requestAccount, payload }),
        });
        if (!confirmation.ok || !confirmationBody.ok) {
          throw new Error(confirmationBody.error || "Подтверждение отклонено");
        }
        confirmationNonce = confirmationBody.nonce;
      }
      const { response, body } = await controlFetch("/api/control/commands", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          kind,
          mode,
          account: requestAccount,
          payload,
          idempotency_key: newIdempotencyKey(),
          confirmation_nonce: confirmationNonce,
        }),
      });
      if (!response.ok || !body.ok) throw new Error(body.error || "Команда не принята");
      hapticNotify("success");
      setLastQueuedAt(Date.now());
      setMessage({
        tone: "ok",
        text: queuedSuccessText(kind, mode, body.idempotent),
      });
      return true;
    } catch (error) {
      hapticImpact("rigid");
      setMessage({ tone: "error", text: friendlyError(error) });
      return false;
    } finally {
      setPending("");
    }
  }, []);

  return { pending, message, setMessage, submit, lastQueuedAt };
}

/**
 * Диалог подтверждения: Escape закрывает, фокус переходит внутрь панели
 * и возвращается на кнопку-инициатор, фон не прокручивается.
 */
export function useConfirmSheet(open, onClose) {
  const dialogRef = useRef(null);
  const restoreRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => {
      dialogRef.current?.querySelector("[data-autofocus]")?.focus();
    }, 30);
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(focusTimer);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
      const node = restoreRef.current;
      if (node && typeof node.focus === "function") node.focus();
    };
  }, [open, onClose]);

  return dialogRef;
}
