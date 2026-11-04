/**
 * Pure helpers for WhatsApp unlink / client shutdown.
 * Timeouts race the original promise without cancelling it — callers must
 * isolate the session (generation bump + chrome kill) before starting another client.
 */

export type RaceOutcome = "ok" | "timeout" | "error";

export function raceOutcome(
  settled: "pending" | "fulfilled" | "rejected",
  timedOut: boolean,
): RaceOutcome {
  if (timedOut && settled === "pending") return "timeout";
  if (settled === "fulfilled") return "ok";
  if (settled === "rejected") return "error";
  return "timeout";
}

/**
 * Race a promise against a timeout. The original promise is NOT cancelled.
 * Callers must not reuse the same session/client after a timeout until the
 * underlying process is forcibly isolated (e.g. chrome-profile kill).
 */
export function raceWithTimeout<T>(
  promise: Promise<T>,
  ms: number,
): Promise<{ outcome: RaceOutcome; value?: T; error?: unknown; durationMs: number }> {
  const started = Date.now();
  return new Promise((resolve) => {
    let settled: "pending" | "fulfilled" | "rejected" = "pending";
    let finished = false;
    const finish = (payload: {
      outcome: RaceOutcome;
      value?: T;
      error?: unknown;
    }) => {
      if (finished) return;
      finished = true;
      resolve({ ...payload, durationMs: Date.now() - started });
    };

    const timer = setTimeout(() => {
      if (settled === "pending") {
        finish({ outcome: "timeout" });
      }
    }, ms);

    promise.then(
      (value) => {
        settled = "fulfilled";
        clearTimeout(timer);
        finish({ outcome: "ok", value });
      },
      (error) => {
        settled = "rejected";
        clearTimeout(timer);
        finish({ outcome: "error", error });
      },
    );
  });
}

/** qr_pending must never imply a live QR while the session is stopped. */
export function computeQrPending(opts: {
  connected: boolean;
  hasQr: boolean;
  status: string;
  phase?: string;
  verificationFailed?: boolean;
}): boolean {
  if (opts.connected) return false;
  if (opts.hasQr) return false;
  const status = String(opts.status || "")
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "");
  if (status === "stopped") return false;
  if (opts.phase === "disconnected" && status === "disconnected") return false;
  if (opts.verificationFailed) return false;
  // Hint that a QR may appear later — never claim one exists.
  return status !== "stopped";
}

export function canClearSessionData(opts: {
  clientDetached: boolean;
  chromeStopped: boolean;
}): boolean {
  return opts.clientDetached && opts.chromeStopped;
}

export function canStartNewSession(opts: {
  previousClientDetached: boolean;
  chromeStopped: boolean;
  sessionDataCleared: boolean;
}): boolean {
  return opts.previousClientDetached && opts.chromeStopped && opts.sessionDataCleared;
}

export type UnlinkRelinkResult = {
  unlinked: boolean;
  relinked: boolean;
  relink_error: string | null;
};

export function classifyUnlinkRelink(opts: {
  stopCompleted: boolean;
  sessionCleared: boolean;
  startAttempted: boolean;
  startFailed: boolean;
  hasQr: boolean;
  connected: boolean;
}): UnlinkRelinkResult {
  if (!opts.stopCompleted) {
    return {
      unlinked: false,
      relinked: false,
      relink_error: "Failed to stop the previous WhatsApp session.",
    };
  }
  if (!opts.sessionCleared) {
    return {
      unlinked: true,
      relinked: false,
      relink_error: "Session disconnected but session files were not cleared safely.",
    };
  }
  if (opts.startFailed) {
    return {
      unlinked: true,
      relinked: false,
      relink_error: "Session disconnected but starting a new session failed.",
    };
  }
  if (opts.connected || opts.hasQr) {
    return { unlinked: true, relinked: Boolean(opts.connected || opts.hasQr), relink_error: null };
  }
  if (opts.startAttempted) {
    return {
      unlinked: true,
      relinked: false,
      relink_error: "Session disconnected but new QR was not ready in time.",
    };
  }
  return {
    unlinked: true,
    relinked: false,
    relink_error: "Session disconnected but re-link did not start.",
  };
}
