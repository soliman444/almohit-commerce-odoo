import { parsePhoneNumberFromString } from "libphonenumber-js";

export function normalizeSendPhone(raw: string): string | null {
  const compact = raw.trim();
  if (!compact) return null;
  const parsed = parsePhoneNumberFromString(compact);
  if (parsed?.isValid()) return parsed.number.replace(/^\+/, "");
  if (compact.startsWith("+")) {
    const digits = compact.replace(/\D/g, "");
    if (digits.length >= 8 && digits.length <= 15) return digits;
  }
  return null;
}
