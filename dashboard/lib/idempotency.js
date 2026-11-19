/**
 * UUID v4 для idempotency_key.
 * crypto.randomUUID есть только в secure context (https или localhost),
 * а Mini App открывают и по http на LAN/в эмуляторе — там нужен запасной путь.
 */
export function newIdempotencyKey() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    try {
      return crypto.randomUUID();
    } catch {
      // падаем в запасной путь
    }
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  // Math.random только как последний шанс: ключ живёт в пределах одной формы.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = (Math.random() * 16) | 0;
    const digit = char === "x" ? value : (value & 0x3) | 0x8;
    return digit.toString(16);
  });
}
