import { LIMITS } from "./command-options";

/** Локальная проверка формы до отправки. Возвращает текст ошибки или "". */
export function validateCommand({ kind, conductKind = "broadcast", text, limit, delayMin, delayMax, fields }) {
  const value = String(text || "").trim();

  if (kind === "BLACKLIST_ADD") {
    if (!value) return "Укажите @username или числовой ID.";
    if (/\s/.test(value)) return "Одно значение: @username или ID, без пробелов.";
    if (!/^(@[A-Za-z][\w]{3,31}|-?\d{3,20})$/.test(value)) {
      return "Не похоже на @username или ID. Пример: @ivan или 123456789.";
    }
    return "";
  }

  if (fields.needsText && !value) return "Заполните текст сообщения.";
  if (fields.showLimit) {
    const parsed = Number(limit);
    if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return "Укажите целое число получателей.";
    const spec = fields.showConduct
      ? (conductKind === "both" ? LIMITS.conductBoth : LIMITS.conductDm)
      : LIMITS.dm;
    if (parsed < spec.min || parsed > spec.max) {
      return `Получателей должно быть от ${spec.min} до ${spec.max}.`;
    }
  }
  if (fields.showDelay && Number(delayMax) < Number(delayMin)) {
    return "Пауза «до» не может быть меньше паузы «от».";
  }
  return "";
}
