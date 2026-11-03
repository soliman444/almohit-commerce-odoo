/**
 * Pure session-status helpers (no side effects).
 * Ready-to-send requires a verified client connection, not QR/sync alone.
 */

export type ConnectionPhase =
  | "connected"
  | "connecting"
  | "waiting_qr"
  | "disconnected"
  | "session_error"
  | "unknown";

/** Statuses that mean the WhatsApp client is ready to send. */
const READY_EXACT = new Set([
  "islogged",
  "inchat",
  "chatsavailable",
  "successchat",
  "main",
  "connected",
  "open",
]);

const WAITING_QR_EXACT = new Set([
  "notlogged",
  "qr",
  "desynced",
  "unpaired",
  "browserclose",
]);

const CONNECTING_EXACT = new Set([
  "opening",
  "pairing",
  "syncing",
  "loading_screen",
  "qrreadsuccess",
  "qrreaderror",
]);

const DISCONNECTED_EXACT = new Set([
  "disconnected",
  "logout",
  "conflict",
  "timeout",
  "closed",
  "close",
]);

const ERROR_EXACT = new Set(["phoneNotConnected", "error", "failed"].map((s) => s.toLowerCase()));

function normalizeStatusToken(raw: unknown): string {
  return String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "");
}

/** True only for statuses that indicate a ready messaging session. */
export function isReadyStatus(status: unknown): boolean {
  const token = normalizeStatusToken(status);
  return Boolean(token && READY_EXACT.has(token));
}

export function isWaitingQrStatus(status: unknown): boolean {
  return WAITING_QR_EXACT.has(normalizeStatusToken(status));
}

function isConnectingStatus(status: unknown): boolean {
  return CONNECTING_EXACT.has(normalizeStatusToken(status));
}

export function isDisconnectedStatus(status: unknown): boolean {
  return DISCONNECTED_EXACT.has(normalizeStatusToken(status));
}

function isSessionErrorStatus(status: unknown): boolean {
  const token = normalizeStatusToken(status);
  return ERROR_EXACT.has(token) || token === "phonenotconnected";
}

export function phaseFromStatus(
  status: unknown,
  opts: { hasQr?: boolean; clientConnected?: boolean | null } = {},
): ConnectionPhase {
  // Ready status strings alone are not enough — require a verified clientConnected=true.
  if (opts.clientConnected === true) {
    return "connected";
  }
  if (isSessionErrorStatus(status)) return "session_error";
  if (isDisconnectedStatus(status)) return "disconnected";
  // Past the early return, clientConnected is never true.
  if (isWaitingQrStatus(status) || opts.hasQr) {
    return "waiting_qr";
  }
  if (isConnectingStatus(status) || isReadyStatus(status)) return "connecting";
  if (opts.clientConnected === false) return "disconnected";
  return "unknown";
}
