/**
 * Pure session-status helpers (no side effects).
 *
 * WPPConnect 2.3.6 notes (from host.layer.js):
 * - getConnectionState() evaluates WPP.whatsapp.Socket.state with no WPP readiness wait.
 * - isConnected() uses WAPI.isConnected() after waitForPageLoad.
 * - isMainReady() / isAuthenticated() use WPP.conn.* once WA-JS is injected.
 * - statusFind / catchQR are the official signals during QR / login (before WPP is ready).
 */

export type ConnectionPhase =
  | "connected"
  | "connecting"
  | "waiting_qr"
  | "disconnected"
  | "session_error"
  | "unknown";

/** Logged-in / ready-to-send auth statuses from statusFind / interface. */
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
  "unpairedidle",
  "browserclose",
]);

const CONNECTING_EXACT = new Set([
  "opening",
  "pairing",
  "syncing",
  "loading_screen",
  "loadingscreen",
  "qrreadsuccess",
  "qrreaderror",
  "qrreadfail",
  "browserready",
  "starting",
]);

const DISCONNECTED_EXACT = new Set([
  "disconnected",
  "disconnectedmobile",
  "logout",
  "conflict",
  "timeout",
  "closed",
  "close",
  "stopped",
  "serverclose",
]);

const ERROR_EXACT = new Set(["phonenotconnected", "error", "failed"]);

export function normalizeStatusToken(raw: unknown): string {
  return String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "");
}

export function isReadyStatus(status: unknown): boolean {
  const token = normalizeStatusToken(status);
  return Boolean(token && READY_EXACT.has(token));
}

export function isWaitingQrStatus(status: unknown): boolean {
  return WAITING_QR_EXACT.has(normalizeStatusToken(status));
}

export function isConnectingStatus(status: unknown): boolean {
  return CONNECTING_EXACT.has(normalizeStatusToken(status));
}

export function isDisconnectedStatus(status: unknown): boolean {
  return DISCONNECTED_EXACT.has(normalizeStatusToken(status));
}

export function isSessionErrorStatus(status: unknown): boolean {
  const token = normalizeStatusToken(status);
  return ERROR_EXACT.has(token);
}

/**
 * Errors thrown when getConnectionState / WPP APIs run before WA-JS injection.
 * These are init noise — not session loss.
 */
export function isWppInitError(err: unknown): boolean {
  const msg = String(err instanceof Error ? err.message : err || "");
  return (
    /WPP is not defined/i.test(msg) ||
    /WAPI is not defined/i.test(msg) ||
    /Cannot read properties of undefined \(reading ['"]state['"]\)/i.test(msg) ||
    /Cannot read property ['"]state['"] of undefined/i.test(msg)
  );
}

/**
 * Auth / QR status outranks browser isConnected.
 * clientConnected alone never forces "connected".
 */
export function phaseFromStatus(
  status: unknown,
  opts: { hasQr?: boolean; clientConnected?: boolean | null } = {},
): ConnectionPhase {
  if (isSessionErrorStatus(status)) return "session_error";
  if (isDisconnectedStatus(status)) return "disconnected";

  if (isWaitingQrStatus(status) || (opts.hasQr && !isReadyStatus(status))) {
    return "waiting_qr";
  }

  if (isConnectingStatus(status)) return "connecting";

  if (opts.clientConnected === true && isReadyStatus(status)) {
    return "connected";
  }

  if (isReadyStatus(status)) return "connecting";

  if (opts.clientConnected === false && !opts.hasQr) return "disconnected";
  return "unknown";
}

export type ConnectionCheckInput = {
  display: string;
  /** Browser/socket readiness (isConnected) — not proof of login alone. */
  isConnected: boolean;
  hasQr: boolean;
  /** Official WPP.conn.isMainReady() when available. */
  mainReady?: boolean;
  /** Probe threw for any reason. */
  checkFailed?: boolean;
  /** Probe threw because WPP/WAPI was not injected yet. */
  initError?: boolean;
  /** True when we intentionally skipped WPP probes (e.g. still on QR screen). */
  probeSkipped?: boolean;
};

export type ConnectionCheckResult = {
  connected: boolean;
  phase: ConnectionPhase;
  status: string;
  keepQr: boolean;
  verificationFailed: boolean;
};

export function evaluateConnectionCheck(input: ConnectionCheckInput): ConnectionCheckResult {
  const display = String(input.display || "").trim();
  const statusToken = display || (input.hasQr ? "notLogged" : "unknown");

  // Init errors during QR / sync must not flip verification_failed or drop QR.
  if (input.initError) {
    if (input.hasQr || isWaitingQrStatus(display)) {
      return {
        connected: false,
        phase: "waiting_qr",
        status: display || "notLogged",
        keepQr: true,
        verificationFailed: false,
      };
    }
    if (isConnectingStatus(display) || isReadyStatus(display)) {
      return {
        connected: false,
        phase: "connecting",
        status: display || "SYNCING",
        keepQr: input.hasQr,
        verificationFailed: false,
      };
    }
    return {
      connected: false,
      phase: "connecting",
      status: statusToken,
      keepQr: true,
      verificationFailed: false,
    };
  }

  if (input.checkFailed) {
    return {
      connected: false,
      phase: "unknown",
      status: statusToken,
      keepQr: true,
      verificationFailed: true,
    };
  }

  if (input.probeSkipped && (input.hasQr || isWaitingQrStatus(display))) {
    return {
      connected: false,
      phase: "waiting_qr",
      status: display || "notLogged",
      keepQr: true,
      verificationFailed: false,
    };
  }

  if (isWaitingQrStatus(display) || (input.hasQr && !isReadyStatus(display) && !input.mainReady)) {
    return {
      connected: false,
      phase: "waiting_qr",
      status: display || "notLogged",
      keepQr: true,
      verificationFailed: false,
    };
  }

  if (isDisconnectedStatus(display)) {
    return {
      connected: false,
      phase: "disconnected",
      status: display,
      keepQr: false,
      verificationFailed: false,
    };
  }

  if (isSessionErrorStatus(display)) {
    return {
      connected: false,
      phase: "session_error",
      status: display,
      keepQr: input.hasQr,
      verificationFailed: false,
    };
  }

  if (isConnectingStatus(display) && !input.mainReady) {
    return {
      connected: false,
      phase: "connecting",
      status: display,
      keepQr: input.hasQr,
      verificationFailed: false,
    };
  }

  // Official main-ready OR (browser connected + ready auth status).
  if (input.mainReady || (input.isConnected && isReadyStatus(display))) {
    return {
      connected: true,
      phase: "connected",
      status: isReadyStatus(display) ? display : "inChat",
      keepQr: false,
      verificationFailed: false,
    };
  }

  if (input.isConnected && !isReadyStatus(display)) {
    return {
      connected: false,
      phase: display
        ? phaseFromStatus(display, { hasQr: input.hasQr, clientConnected: false })
        : "unknown",
      status: display || "unknown",
      keepQr: input.hasQr,
      verificationFailed: false,
    };
  }

  if (!input.isConnected && isReadyStatus(display)) {
    return {
      connected: false,
      phase: "connecting",
      status: display,
      keepQr: input.hasQr,
      verificationFailed: false,
    };
  }

  if (input.hasQr) {
    return {
      connected: false,
      phase: "waiting_qr",
      status: display || "notLogged",
      keepQr: true,
      verificationFailed: false,
    };
  }

  if (display) {
    return {
      connected: false,
      phase: phaseFromStatus(display, { hasQr: false, clientConnected: false }),
      status: display,
      keepQr: false,
      verificationFailed: false,
    };
  }

  return {
    connected: false,
    phase: "disconnected",
    status: "disconnected",
    keepQr: false,
    verificationFailed: false,
  };
}
