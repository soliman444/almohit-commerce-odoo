import { execFile, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { create, type Whatsapp } from "@wppconnect-team/wppconnect";
import { config } from "./config.js";
import { normalizeSendPhone } from "./phone.js";
import {
  type ConnectionPhase,
  evaluateConnectionCheck,
  isConnectingStatus,
  isDisconnectedStatus,
  isReadyStatus,
  isWaitingQrStatus,
  isWppInitError,
  phaseFromStatus,
} from "./sessionStatus.js";
import { browserQueue } from "./browserQueue.js";
import {
  canClearSessionData,
  canStartNewSession,
  classifyUnlinkRelink,
  computeQrPending,
  raceWithTimeout,
} from "./unlinkLifecycle.js";

export type UnlinkResult = {
  whatsapp: {
    connected: boolean;
    status: string;
    phase: string;
    session: string;
    enabled: boolean;
    phone: string;
    verification_failed: boolean;
    last_verified_at: string | null;
    qr_issued_at: string | null;
    qr_pending: boolean;
  };
  qr: string | null;
  unlinked: boolean;
  relinked: boolean;
  relink_error: string | null;
};

type WhatsAppClient = Whatsapp & {
  isConnected?: () => Promise<boolean>;
  getConnectionState?: () => Promise<unknown>;
  isMainReady?: () => Promise<boolean>;
  isAuthenticated?: () => Promise<boolean>;
  isLoggedIn?: () => Promise<boolean>;
  getHostDevice?: () => Promise<unknown>;
  getWid?: () => Promise<unknown>;
  onInterfaceChange?: (cb: (state: unknown) => void) => Promise<unknown> | unknown;
  onStateChange?: (cb: (state: unknown) => void) => Promise<unknown> | unknown;
  logout?: () => Promise<unknown>;
  close?: () => Promise<unknown>;
};

let client: WhatsAppClient | null = null;
let sessionStatus = "starting";
let phase: ConnectionPhase = "unknown";
/** True only after authenticated readiness (isMainReady / ready status + connected). */
let connected = false;
let verificationFailed = false;
let lastVerifiedAt: string | null = null;
let starting: Promise<void> | null = null;
let unlinking: Promise<UnlinkResult> | null = null;
let generation = 0;
let lastQr: string | null = null;
let lastQrIssuedAt: string | null = null;
let lastPhone = "";
let pollTimer: ReturnType<typeof setInterval> | null = null;
let qrDropTimer: ReturnType<typeof setTimeout> | null = null;
/** Set after statusFind/interface signals that WA-JS injection is expected. */
let wppProbeAllowed = false;
let lastInitErrorKey = "";
let lastInitErrorAt = 0;

function dataPath(...parts: string[]): string {
  return path.join(config.dataDir, ...parts);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function applyPhase(status: string, extras: {
  connected?: boolean;
  verificationFailed?: boolean;
  phone?: string;
  clearQr?: boolean;
  keepPhone?: boolean;
} = {}) {
  sessionStatus = status || sessionStatus || "unknown";
  if (extras.connected !== undefined) connected = extras.connected;
  if (extras.verificationFailed !== undefined) verificationFailed = extras.verificationFailed;
  if (extras.phone !== undefined) lastPhone = extras.phone;
  if (extras.keepPhone === false) lastPhone = "";
  if (extras.clearQr) {
    lastQr = null;
    lastQrIssuedAt = null;
  }
  phase = phaseFromStatus(sessionStatus, {
    hasQr: Boolean(lastQr),
    clientConnected: connected ? true : verificationFailed ? null : false,
  });
  // Never report connected unless verified.
  if (phase !== "connected") {
    connected = false;
  } else if (!connected) {
    phase = verificationFailed ? "unknown" : isWaitingQrStatus(sessionStatus) ? "waiting_qr" : "connecting";
  }
}

function markUnverified(status: string, opts: { failed?: boolean; forcePhase?: ConnectionPhase } = {}) {
  connected = false;
  verificationFailed = opts.failed !== false;
  sessionStatus = status || sessionStatus;
  phase = opts.forcePhase || phaseFromStatus(sessionStatus, {
    hasQr: Boolean(lastQr),
    clientConnected: null,
  });
  if (phase === "connected") phase = "unknown";
}

function markVerified(statusHint?: string) {
  connected = true;
  verificationFailed = false;
  lastVerifiedAt = new Date().toISOString();
  sessionStatus = statusHint || sessionStatus || "isLogged";
  lastQr = null;
  lastQrIssuedAt = null;
  if (qrDropTimer) {
    clearTimeout(qrDropTimer);
    qrDropTimer = null;
  }
  phase = "connected";
}

function stripQr(value: string): string {
  const raw = (value || "").trim();
  if (!raw) return "";
  if (raw.startsWith("data:") && raw.includes(",")) {
    return raw.slice(raw.indexOf(",") + 1).replace(/\s/g, "");
  }
  return raw.replace(/\s/g, "");
}

function phoneFromUnknown(value: unknown): string {
  if (!value) return "";
  if (typeof value === "string") {
    const user = value.split("@")[0].replace(/\D/g, "");
    // Require a plausible E.164 national number — reject bare country codes like "1".
    if (user.length < 8 || user.length > 15) return "";
    return `+${user}`;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return (
      phoneFromUnknown(record.wid) ||
      phoneFromUnknown(record.id) ||
      phoneFromUnknown(record.user) ||
      phoneFromUnknown(record._serialized) ||
      phoneFromUnknown(record.phoneNumber) ||
      phoneFromUnknown(record.phone) ||
      ""
    );
  }
  return "";
}

function displayFromState(state: unknown): string {
  if (!state) return "";
  if (typeof state === "string") return state;
  if (typeof state === "object") {
    const record = state as Record<string, unknown>;
    return String(record.displayInfo || record.mode || record.state || record.type || "");
  }
  return String(state);
}

async function capturePhone() {
  if (!client) return;
  try {
    if (client.getHostDevice) {
      const phone = phoneFromUnknown(await client.getHostDevice());
      if (phone) lastPhone = phone;
    }
  } catch {
    /* host device is optional */
  }
  try {
    if (client.getWid) {
      const phone = phoneFromUnknown(await client.getWid());
      if (phone) lastPhone = phone;
    }
  } catch {
    /* wid is optional */
  }
}

function logInitErrorOnce(message: string): void {
  const key = message.slice(0, 80);
  const now = Date.now();
  if (key === lastInitErrorKey && now - lastInitErrorAt < 30_000) return;
  lastInitErrorKey = key;
  lastInitErrorAt = now;
  console.warn("[whatsapp] wpp init probe skipped:", key);
}

function shouldSkipWppProbe(): boolean {
  if (lastQr && !wppProbeAllowed) return true;
  if (isWaitingQrStatus(sessionStatus) && !wppProbeAllowed) return true;
  return false;
}

/**
 * Internal connection probe — must only run while holding browserQueue (or re-entered).
 * Does not call getConnectionState while waiting for QR (WPP not injected yet in 2.3.6).
 */
async function refreshConnectionImpl(): Promise<boolean> {
  const current = client;
  if (!current) {
    markUnverified(sessionStatus || "disconnected", { failed: false, forcePhase: "disconnected" });
    return false;
  }

  // During QR / pre-inject: rely on statusFind + catchQR only (official WPPConnect path).
  if (shouldSkipWppProbe()) {
    const decision = evaluateConnectionCheck({
      display: sessionStatus || "notLogged",
      isConnected: false,
      hasQr: Boolean(lastQr),
      probeSkipped: true,
    });
    applyPhase(decision.status, {
      connected: false,
      verificationFailed: false,
    });
    phase = decision.phase;
    connected = false;
    return false;
  }

  try {
    let mainReady = false;
    let display = sessionStatus || "";
    let isConn = false;
    let initError = false;

    if (current.isMainReady) {
      try {
        mainReady = Boolean(await current.isMainReady());
        if (mainReady) wppProbeAllowed = true;
      } catch (err) {
        if (isWppInitError(err)) {
          initError = true;
          logInitErrorOnce(err instanceof Error ? err.message : String(err));
        } else {
          throw err;
        }
      }
    }

    // getConnectionState reads WPP.whatsapp.Socket.state — only after WPP is expected.
    if (
      !initError &&
      current.getConnectionState &&
      (wppProbeAllowed || mainReady || isReadyStatus(sessionStatus) || isConnectingStatus(sessionStatus))
    ) {
      try {
        const state = await current.getConnectionState();
        const fromState = displayFromState(state);
        if (fromState) display = fromState;
      } catch (err) {
        if (isWppInitError(err)) {
          initError = true;
          logInitErrorOnce(err instanceof Error ? err.message : String(err));
        } else {
          throw err;
        }
      }
    }

    if (!initError && current.isConnected && (mainReady || isReadyStatus(display) || isReadyStatus(sessionStatus))) {
      try {
        isConn = Boolean(await current.isConnected());
      } catch (err) {
        if (isWppInitError(err)) {
          initError = true;
          logInitErrorOnce(err instanceof Error ? err.message : String(err));
        } else {
          throw err;
        }
      }
    }

    if (!display) display = sessionStatus || "";

    const decision = evaluateConnectionCheck({
      display,
      isConnected: isConn,
      hasQr: Boolean(lastQr),
      mainReady,
      initError,
      checkFailed: initError,
    });

    if (decision.connected) {
      await capturePhone();
      markVerified(decision.status);
      return true;
    }

    applyPhase(decision.status, {
      connected: false,
      verificationFailed: decision.verificationFailed,
    });
    phase = decision.phase;
    connected = false;
    return false;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const initError = isWppInitError(err);
    const decision = evaluateConnectionCheck({
      display: sessionStatus || "unknown",
      isConnected: false,
      hasQr: Boolean(lastQr),
      checkFailed: true,
      initError,
    });
    if (initError) {
      logInitErrorOnce(message);
      applyPhase(decision.status, {
        connected: false,
        verificationFailed: false,
      });
      phase = decision.phase;
      connected = false;
      return false;
    }
    markUnverified(decision.status, { failed: true, forcePhase: decision.phase });
    console.warn("[whatsapp] connection check failed:", message.slice(0, 120));
    return false;
  }
}

/**
 * Public refresh: wait for the in-flight probe (never return a stale connected flag).
 * Serialized on browserQueue; coalesced so concurrent callers share one probe.
 */
async function refreshConnection(): Promise<boolean> {
  return browserQueue.runRefresh(() => refreshConnectionImpl());
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer as unknown as NodeJS.Timeout);
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (qrDropTimer) {
    clearTimeout(qrDropTimer);
    qrDropTimer = null;
  }
}

function pollIntervalMs(): number {
  if (phase === "waiting_qr" || (lastQr && !connected)) return 5000;
  if (phase === "connecting" && !connected) return 3000;
  return 2000;
}

function startPolling() {
  stopPolling();
  const schedule = () => {
    pollTimer = setTimeout(() => {
      // Skip while send/refresh runs — do not pile CDP work behind sendText.
      const polled = browserQueue.requestPollRefresh(() => refreshConnectionImpl());
      const done = polled ? polled.then(() => undefined, () => undefined) : Promise.resolve();
      void done.finally(() => {
        if (generation >= 0 && client) schedule();
      });
    }, pollIntervalMs()) as unknown as ReturnType<typeof setInterval>;
  };
  schedule();
}

function handleInterfaceState(state: unknown) {
  const display = displayFromState(state).toUpperCase();
  if (!display) return;
  if (["MAIN", "CONVERSATION", "CHAT", "CONNECTED"].some((name) => display.includes(name))) {
    // Status hint only — verification still required via refreshConnection.
    wppProbeAllowed = true;
    lastQr = null;
    lastQrIssuedAt = null;
    sessionStatus = "inChat";
    phase = "connecting";
    void refreshConnection();
    return;
  }
  if (display.includes("SYNC")) {
    // Page loaded / WA-JS injecting — still not authenticated-ready.
    wppProbeAllowed = true;
    sessionStatus = "SYNCING";
    phase = "connecting";
    connected = false;
    verificationFailed = false;
    return;
  }
  if (!display.includes("QR")) return;
  wppProbeAllowed = false;
  if (!connected) {
    applyPhase("notLogged", { connected: false, verificationFailed: false });
    return;
  }
  if (qrDropTimer) return;
  qrDropTimer = setTimeout(() => {
    qrDropTimer = null;
    void (async () => {
      const stillConnected = await refreshConnection();
      if (!stillConnected && !verificationFailed) {
        applyPhase("notLogged", { connected: false, verificationFailed: false });
      }
    })();
  }, 8000);
}

async function attachClientListeners(created: WhatsAppClient, gen: number) {
  try {
    if (created.onInterfaceChange) {
      await created.onInterfaceChange((state) => {
        if (gen !== generation) return;
        handleInterfaceState(state);
      });
    }
  } catch {
    /* optional */
  }
  try {
    if (created.onStateChange) {
      await created.onStateChange((state) => {
        if (gen !== generation) return;
        const display = displayFromState(state);
        if (!display) return;
        if (isDisconnectedStatus(display)) {
          applyPhase(display, { connected: false, verificationFailed: false });
          return;
        }
        if (isReadyStatus(display)) {
          wppProbeAllowed = true;
          sessionStatus = display;
          phase = "connecting";
          void refreshConnection();
          return;
        }
        applyPhase(display, { connected: false, verificationFailed: false });
      });
    }
  } catch {
    /* optional */
  }
}

function unlockChromeProfile() {
  for (const name of ["SingletonLock", "SingletonSocket", "SingletonCookie"]) {
    try {
      fs.rmSync(dataPath("chrome-profile", name), { force: true });
    } catch {
      /* stale lock from a previous container */
    }
  }
}

function clearSessionData() {
  for (const dir of ["tokens", "chrome-profile"]) {
    const full = dataPath(dir);
    try {
      fs.rmSync(full, { recursive: true, force: true });
    } catch (error) {
      console.warn("[whatsapp] could not clear session dir");
    }
    fs.mkdirSync(full, { recursive: true });
  }
}

function chromeProfilePath(): string {
  return dataPath("chrome-profile").replace(/\\/g, "/");
}

/** PIDs of Chromium processes whose args reference this service profile only. */
function listChromeProfilePids(): number[] {
  const profile = chromeProfilePath();
  const pids: number[] = [];
  try {
    const listed = spawnSync("ps", ["-eo", "pid=,args="], {
      encoding: "utf8",
      timeout: 5000,
    });
    if (listed.status !== 0 || !listed.stdout) return pids;
    for (const line of listed.stdout.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (!/chrom(e|ium)/i.test(trimmed)) continue;
      if (!trimmed.includes(profile)) continue;
      const pid = Number.parseInt(trimmed.split(/\s+/)[0] || "", 10);
      if (!Number.isFinite(pid) || pid <= 1) continue;
      pids.push(pid);
    }
  } catch {
    /* ignore */
  }
  return pids;
}

/**
 * Kill only Chromium processes tied to this service's chrome-profile.
 * Never broad `pkill -f chromium`.
 */
function killOrphanChrome(): Promise<void> {
  return new Promise((resolve) => {
    const profile = chromeProfilePath();
    const pids = listChromeProfilePids();
    if (pids.length > 0) {
      for (const pid of pids) {
        try {
          process.kill(pid, "SIGTERM");
        } catch {
          /* ignore */
        }
      }
      resolve();
      return;
    }
    // Last resort on Linux containers: only match our user-data-dir path.
    const escaped = profile.replace(/'/g, "");
    execFile(
      "sh",
      ["-c", `pkill -f 'user-data-dir=${escaped}' || pkill -f '${escaped}' || true`],
      () => resolve(),
    );
  });
}

async function ensureChromeStopped(maxWaitMs: number): Promise<boolean> {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    await killOrphanChrome();
    await sleep(250);
    if (listChromeProfilePids().length === 0) return true;
  }
  await killOrphanChrome();
  return listChromeProfilePids().length === 0;
}

function qrPendingFlag(): boolean {
  return computeQrPending({
    connected,
    hasQr: Boolean(lastQr),
    status: sessionStatus,
    phase,
    verificationFailed,
  });
}

export function getStatus() {
  return {
    connected,
    status: sessionStatus,
    phase,
    session: config.session,
    enabled: config.enabled,
    verification_failed: verificationFailed,
    last_verified_at: lastVerifiedAt,
    qr_pending: qrPendingFlag(),
  };
}

export function getSession() {
  return {
    connected,
    status: sessionStatus,
    phase,
    session: config.session,
    enabled: config.enabled,
    phone: lastPhone,
    verification_failed: verificationFailed,
    last_verified_at: lastVerifiedAt,
    qr_issued_at: connected ? null : lastQrIssuedAt,
    qr_pending: qrPendingFlag(),
  };
}

export function getQr(): string | null {
  return connected ? null : lastQr;
}

export async function startWhatsApp(): Promise<void> {
  if (starting) return starting;
  const gen = generation;
  starting = (async () => {
    fs.mkdirSync(dataPath("tokens"), { recursive: true });
    fs.mkdirSync(dataPath("chrome-profile"), { recursive: true });
    unlockChromeProfile();
    phase = "connecting";
    const created = (await create({
      session: config.session,
      folderNameToken: dataPath("tokens"),
      headless: true,
      devtools: false,
      useChrome: Boolean(config.chromePath),
      logQR: false,
      disableWelcome: true,
      updatesLog: false,
      autoClose: 0,
      waitForLogin: false,
      puppeteerOptions: {
        executablePath: config.chromePath,
        userDataDir: dataPath("chrome-profile"),
        args: [
          "--no-sandbox",
          "--disable-setuid-sandbox",
          "--disable-dev-shm-usage",
          "--disable-gpu",
          "--disable-extensions",
        ],
      },
      catchQR: (base64) => {
        if (gen !== generation) return;
        if (connected) return;
        // Never log QR payload. Always replace with the newest code only.
        const next = stripQr(base64 || "");
        if (!next) return;
        wppProbeAllowed = false;
        lastQr = next;
        lastQrIssuedAt = new Date().toISOString();
        verificationFailed = false;
        applyPhase("notLogged", { connected: false, verificationFailed: false });
      },

      statusFind: (status) => {
        if (gen !== generation) return;
        const raw = String(status || "unknown");
        const token = raw.toLowerCase().replace(/[\s_-]+/g, "");

        if (token === "browserclose" || token === "serverclose" || token === "autoclosecalled") {
          wppProbeAllowed = false;
          lastQr = null;
          lastQrIssuedAt = null;
          applyPhase(raw, { connected: false, verificationFailed: false, clearQr: true });
          return;
        }

        if (token === "qrreadsuccess") {
          // QR scanned — drop previous QR image; wait for sync / main ready.
          lastQr = null;
          lastQrIssuedAt = null;
          wppProbeAllowed = true;
          applyPhase("qrReadSuccess", { connected: false, verificationFailed: false, clearQr: true });
          return;
        }

        if (isWaitingQrStatus(raw) || token === "notlogged") {
          wppProbeAllowed = false;
          applyPhase(raw || "notLogged", { connected: false, verificationFailed: false });
          return;
        }

        if (isReadyStatus(raw)) {
          wppProbeAllowed = true;
          lastQr = null;
          lastQrIssuedAt = null;
          sessionStatus = raw;
          phase = "connecting";
          connected = false;
          void refreshConnection();
          return;
        }

        if (isConnectingStatus(raw)) {
          wppProbeAllowed = true;
          applyPhase(raw, { connected: false, verificationFailed: false });
          return;
        }

        applyPhase(raw, { connected: false, verificationFailed: false });
      },
    })) as WhatsAppClient;
    if (gen !== generation) {
      await created.close?.().catch(() => undefined);
      return;
    }
    client = created;
    wppProbeAllowed = false;
    await attachClientListeners(created, gen);
    startPolling();
    // Soft refresh — will skip WPP probes while waiting for QR.
    await refreshConnection();
    if (sessionStatus === "starting") {
      applyPhase("browserReady", { connected: false, verificationFailed: false });
    }
  })();
  try {
    await starting;
  } catch (error) {
    if (gen === generation) {
      starting = null;
      markUnverified("error", { failed: true, forcePhase: "session_error" });
    }
    throw error;
  } finally {
    if (gen === generation) {
      starting = null;
    }
  }
}

const LOGOUT_TIMEOUT_MS = 8_000;
const CLOSE_TIMEOUT_MS = 8_000;
const CHROME_STOP_WAIT_MS = 10_000;
const QR_WAIT_MS = 20_000;

function newUnlinkCorrelationId(): string {
  return `unlink_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function logUnlink(event: string, cid: string, extra: Record<string, unknown> = {}): void {
  console.log(
    JSON.stringify({
      service: "almohit-whatsapp",
      event,
      cid,
      ts: new Date().toISOString(),
      ...extra,
    }),
  );
}

async function waitForClient(ms: number): Promise<WhatsAppClient | null> {
  const waitUntil = Date.now() + ms;
  while (Date.now() < waitUntil) {
    const current = client;
    if (current) return current;
    await sleep(200);
  }
  return client;
}

/**
 * Logout then close with independent timeouts.
 * A timeout does not cancel the original promise — the client reference must already
 * be detached (generation bumped, client=null) so a late resolve cannot revive it.
 * After timeout we always proceed to chrome-profile-scoped kill.
 */
async function closeClient(
  current: WhatsAppClient | null,
  cid: string,
): Promise<{ logout: string; close: string }> {
  if (!current) {
    return { logout: "skipped", close: "skipped" };
  }

  let logoutResult = "skipped";
  if (current.logout) {
    logUnlink("logout_started", cid);
    const raced = await raceWithTimeout(current.logout(), LOGOUT_TIMEOUT_MS);
    // Detach late settlement — ignore after timeout.
    void raced;
    if (raced.outcome === "timeout") {
      logoutResult = "timeout";
      logUnlink("logout_timeout", cid, { duration_ms: raced.durationMs });
    } else if (raced.outcome === "error") {
      logoutResult = "error";
      logUnlink("logout_completed", cid, {
        ok: false,
        duration_ms: raced.durationMs,
        error_kind: exceptionKind(raced.error),
      });
    } else {
      logoutResult = "ok";
      logUnlink("logout_completed", cid, { ok: true, duration_ms: raced.durationMs });
    }
  }

  let closeResult = "skipped";
  if (current.close) {
    logUnlink("client_close_started", cid);
    const raced = await raceWithTimeout(current.close(), CLOSE_TIMEOUT_MS);
    if (raced.outcome === "timeout") {
      closeResult = "timeout";
      logUnlink("client_close_timeout", cid, { duration_ms: raced.durationMs });
    } else if (raced.outcome === "error") {
      closeResult = "error";
      logUnlink("client_close_completed", cid, {
        ok: false,
        duration_ms: raced.durationMs,
        error_kind: exceptionKind(raced.error),
      });
    } else {
      closeResult = "ok";
      logUnlink("client_close_completed", cid, { ok: true, duration_ms: raced.durationMs });
    }
  }

  return { logout: logoutResult, close: closeResult };
}

async function stopWhatsApp(cid?: string): Promise<{ chromeStopped: boolean }> {
  const logCid = cid || newUnlinkCorrelationId();
  generation += 1;
  const pending = starting;
  starting = null;
  stopPolling();
  logUnlink("polling_stopped", logCid);

  let current = client;
  client = null;
  connected = false;
  verificationFailed = false;
  wppProbeAllowed = false;
  lastVerifiedAt = null;
  lastQr = null;
  lastQrIssuedAt = null;
  lastPhone = "";
  lastInitErrorKey = "";
  lastInitErrorAt = 0;
  applyPhase("stopped", { connected: false, verificationFailed: false, keepPhone: false, clearQr: true });

  if (!current && pending) {
    // Wait briefly for in-flight create, then detach again so we never dual-own the profile.
    current = await waitForClient(8000);
    client = null;
  }

  await closeClient(current, logCid);

  logUnlink("chrome_cleanup_started", logCid);
  const chromeStarted = Date.now();
  const chromeStopped = await ensureChromeStopped(CHROME_STOP_WAIT_MS);
  logUnlink("chrome_cleanup_completed", logCid, {
    chrome_stopped: chromeStopped,
    duration_ms: Date.now() - chromeStarted,
    remaining_pids: listChromeProfilePids().length,
  });

  return { chromeStopped };
}

export async function unlinkWhatsApp(): Promise<UnlinkResult> {
  if (unlinking) return unlinking;
  const cid = newUnlinkCorrelationId();
  const unlinkStartedAt = Date.now();
  logUnlink("unlink_started", cid);

  unlinking = (async () => {
    let stopCompleted = false;
    let sessionCleared = false;
    let startAttempted = false;
    let startFailed = false;

    try {
      const { chromeStopped } = await stopWhatsApp(cid);
      stopCompleted = true;

      const clientDetached = client === null;
      if (
        !canClearSessionData({
          clientDetached,
          chromeStopped,
        })
      ) {
        logUnlink("unlink_failed", cid, {
          stage: "session_clear_blocked",
          client_detached: clientDetached,
          chrome_stopped: chromeStopped,
          duration_ms: Date.now() - unlinkStartedAt,
        });
        return {
          whatsapp: getSession(),
          qr: null,
          ...classifyUnlinkRelink({
            stopCompleted: true,
            sessionCleared: false,
            startAttempted: false,
            startFailed: false,
            hasQr: false,
            connected: false,
          }),
        };
      }

      logUnlink("session_data_clear_started", cid);
      const clearStarted = Date.now();
      clearSessionData();
      unlockChromeProfile();
      sessionCleared = true;
      logUnlink("session_data_clear_completed", cid, {
        duration_ms: Date.now() - clearStarted,
      });

      if (
        !canStartNewSession({
          previousClientDetached: client === null,
          chromeStopped,
          sessionDataCleared: sessionCleared,
        })
      ) {
        startFailed = true;
        logUnlink("unlink_failed", cid, {
          stage: "new_session_blocked",
          duration_ms: Date.now() - unlinkStartedAt,
        });
        return {
          whatsapp: getSession(),
          qr: getQr(),
          ...classifyUnlinkRelink({
            stopCompleted,
            sessionCleared,
            startAttempted: false,
            startFailed: true,
            hasQr: Boolean(lastQr),
            connected,
          }),
        };
      }

      applyPhase("waiting_qr", {
        connected: false,
        verificationFailed: false,
        keepPhone: false,
        clearQr: true,
      });

      logUnlink("new_session_starting", cid);
      startAttempted = true;
      try {
        await startWhatsApp();
        logUnlink("new_session_started", cid, {
          duration_ms: Date.now() - unlinkStartedAt,
        });
      } catch (err) {
        startFailed = true;
        logUnlink("unlink_failed", cid, {
          stage: "new_session_start",
          error_kind: exceptionKind(err),
          duration_ms: Date.now() - unlinkStartedAt,
        });
        return {
          whatsapp: getSession(),
          qr: getQr(),
          ...classifyUnlinkRelink({
            stopCompleted,
            sessionCleared,
            startAttempted,
            startFailed: true,
            hasQr: Boolean(lastQr),
            connected,
          }),
        };
      }

      const qrDeadline = Date.now() + QR_WAIT_MS;
      while (!lastQr && !connected && Date.now() < qrDeadline) {
        await sleep(250);
      }
      if (lastQr) {
        logUnlink("qr_received", cid, {
          qr_issued_at: lastQrIssuedAt,
          duration_ms: Date.now() - unlinkStartedAt,
        });
      }

      const classified = classifyUnlinkRelink({
        stopCompleted,
        sessionCleared,
        startAttempted,
        startFailed: false,
        hasQr: Boolean(lastQr),
        connected,
      });

      if (classified.relink_error) {
        logUnlink("unlink_completed", cid, {
          unlinked: true,
          relinked: false,
          relink_incomplete: true,
          duration_ms: Date.now() - unlinkStartedAt,
        });
      } else {
        logUnlink("unlink_completed", cid, {
          unlinked: true,
          relinked: classified.relinked,
          duration_ms: Date.now() - unlinkStartedAt,
        });
      }

      return {
        whatsapp: getSession(),
        qr: getQr(),
        ...classified,
      };
    } catch (err) {
      logUnlink("unlink_failed", cid, {
        error_kind: exceptionKind(err),
        duration_ms: Date.now() - unlinkStartedAt,
      });
      return {
        whatsapp: getSession(),
        qr: getQr(),
        ...classifyUnlinkRelink({
          stopCompleted,
          sessionCleared,
          startAttempted,
          startFailed: true,
          hasQr: Boolean(lastQr),
          connected,
        }),
      };
    }
  })();

  try {
    return await unlinking;
  } finally {
    unlinking = null;
  }
}

function resultId(result: unknown): string {
  if (!result) return "";
  if (typeof result === "string") return result;
  if (typeof result === "object") {
    const record = result as { id?: unknown; messageId?: unknown };
    if (typeof record.id === "string") return record.id;
    if (record.id && typeof record.id === "object" && "_serialized" in record.id) {
      return String((record.id as { _serialized?: string })._serialized ?? "");
    }
    if (typeof record.messageId === "string") return record.messageId;
  }
  return "";
}

function newSendCorrelationId(): string {
  return `send_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** Mask destination digits for logs — never the full number. */
function maskDestination(raw: string): string {
  const digits = String(raw || "").replace(/\D/g, "");
  if (digits.length < 6) return "******";
  return `${digits.slice(0, 3)}******${digits.slice(-3)}`;
}

function exceptionKind(err: unknown): string {
  if (err instanceof Error) {
    const code = (err as Error & { code?: string }).code;
    return code ? `${err.name}:${code}` : err.name || "Error";
  }
  return typeof err;
}

/** Redact phones, WA IDs, tokens, and long digit runs from exception text before logging. */
function sanitizeErrorMessage(raw: unknown, limit = 160): string {
  let text = String(raw ?? "");
  if (!text) return "";
  text = text
    .replace(/bearer\s+[A-Za-z0-9._\-]+/gi, "bearer [redacted]")
    .replace(/data:[^;]+;base64,[A-Za-z0-9+/=\s]+/gi, "[redacted-b64]")
    .replace(/\b\d{6,15}@(?:c\.us|s\.whatsapp\.net|lid|g\.us)\b/gi, "[redacted-wid]")
    .replace(/\+[1-9]\d{7,14}\b/g, "[redacted-phone]")
    .replace(/(?<!\d)(?:00)?[1-9]\d{7,14}(?!\d)/g, "[redacted-phone]")
    .replace(/\b[0-9a-f]{16,}\b/gi, "[redacted-id]");
  if (text.length > limit) text = text.slice(0, limit);
  return text;
}

function logSend(event: string, cid: string, extra: Record<string, unknown> = {}): void {
  // Safe structured log: no tokens, message body, full phone, QR, or session secrets.
  console.log(
    JSON.stringify({
      service: "almohit-whatsapp",
      event,
      cid,
      ts: new Date().toISOString(),
      ...extra,
    }),
  );
}

export async function sendText(phone: string, message: string): Promise<{ messageId: string }> {
  const cid = newSendCorrelationId();
  const startedAt = Date.now();
  logSend("send_started", cid, {
    to_masked: maskDestination(phone),
    message_len: String(message || "").length,
    phase,
    connected,
    verification_failed: verificationFailed,
  });

  if (!config.enabled) {
    const error = new Error("WhatsApp sending is disabled.");
    (error as Error & { code?: string }).code = "WHATSAPP_DISABLED";
    logSend("wpp_send_failed", cid, {
      stage: "disabled",
      error_kind: "WHATSAPP_DISABLED",
      duration_ms: Date.now() - startedAt,
    });
    throw error;
  }

  // Serialize all CDP work: connection probe + sendText. Nested refresh uses impl (no deadlock).
  const { result, queueWaitMs } = await browserQueue.runExclusive("send", async () => {
    const acquiredWaitMs = Date.now() - startedAt;
    logSend("browser_queue_acquired", cid, {
      queue_wait_ms: acquiredWaitMs,
      phase,
    });

    logSend("connection_check_started", cid, { phase, connected });
    const checkStartedAt = Date.now();
    let ready = false;
    try {
      ready = await refreshConnectionImpl();
    } catch (err) {
      logSend("connection_check_failed", cid, {
        error_kind: exceptionKind(err),
        duration_ms: Date.now() - checkStartedAt,
        phase,
        verification_failed: verificationFailed,
      });
      throw err;
    }

    if (!ready || !connected || !client) {
      logSend("connection_check_failed", cid, {
        error_kind: verificationFailed ? "WHATSAPP_UNVERIFIED" : "WHATSAPP_NOT_CONNECTED",
        duration_ms: Date.now() - checkStartedAt,
        phase,
        connected,
        has_client: Boolean(client),
        verification_failed: verificationFailed,
      });
      const error = new Error(
        verificationFailed
          ? "WhatsApp session could not be verified."
          : "WhatsApp is not connected.",
      );
      (error as Error & { code?: string }).code = verificationFailed
        ? "WHATSAPP_UNVERIFIED"
        : "WHATSAPP_NOT_CONNECTED";
      throw error;
    }

    logSend("connection_check_succeeded", cid, {
      duration_ms: Date.now() - checkStartedAt,
      phase,
      last_verified_at: lastVerifiedAt,
    });

    const normalized = normalizeSendPhone(phone);
    if (!normalized) {
      const error = new Error("Phone number is not a valid international number.");
      (error as Error & { code?: string }).code = "INVALID_PHONE";
      logSend("wpp_send_failed", cid, {
        stage: "validate_phone",
        error_kind: "INVALID_PHONE",
        duration_ms: Date.now() - startedAt,
      });
      throw error;
    }

    // Never log message body, tokens, or full destination.
    const active = client;
    const wppStartedAt = Date.now();
    logSend("wpp_send_started", cid, {
      to_masked: maskDestination(normalized),
      ms_since_request: wppStartedAt - startedAt,
      queue_wait_ms: acquiredWaitMs,
    });
    try {
      const sendResult = await active.sendText(normalized, message);
      const messageId = resultId(sendResult) || `${Date.now()}`;
      logSend("wpp_send_succeeded", cid, {
        result_ok: true,
        duration_ms: Date.now() - wppStartedAt,
        total_duration_ms: Date.now() - startedAt,
        queue_wait_ms: acquiredWaitMs,
      });
      return { messageId };
    } catch (err) {
      logSend("wpp_send_failed", cid, {
        stage: "client.sendText",
        error_kind: exceptionKind(err),
        error_message: sanitizeErrorMessage(err instanceof Error ? err.message : "non_error"),
        duration_ms: Date.now() - wppStartedAt,
        total_duration_ms: Date.now() - startedAt,
        queue_wait_ms: acquiredWaitMs,
      });
      throw err;
    }
  });

  if (queueWaitMs > 0) {
    logSend("browser_queue_waited", cid, { queue_wait_ms: queueWaitMs });
  }
  return result;
}
