import { execFile, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { create, type Whatsapp } from "@wppconnect-team/wppconnect";
import { config } from "./config.js";
import { normalizeSendPhone } from "./phone.js";
import {
  type ConnectionPhase,
  isDisconnectedStatus,
  isReadyStatus,
  isWaitingQrStatus,
  phaseFromStatus,
} from "./sessionStatus.js";

type WhatsAppClient = Whatsapp & {
  isConnected?: () => Promise<boolean>;
  getConnectionState?: () => Promise<unknown>;
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
/** True only after a successful client.isConnected() verification. */
let connected = false;
let verificationFailed = false;
let lastVerifiedAt: string | null = null;
let starting: Promise<void> | null = null;
let unlinking: Promise<{ whatsapp: ReturnType<typeof getSession>; qr: string | null }> | null =
  null;
let generation = 0;
let lastQr: string | null = null;
let lastQrIssuedAt: string | null = null;
let lastPhone = "";
let pollTimer: ReturnType<typeof setInterval> | null = null;
let qrDropTimer: ReturnType<typeof setTimeout> | null = null;

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
    return user ? `+${user}` : "";
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return (
      phoneFromUnknown(record.wid) ||
      phoneFromUnknown(record.id) ||
      phoneFromUnknown(record.user) ||
      phoneFromUnknown(record._serialized) ||
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

/**
 * Fresh connection check. On failure: mark unverified — do NOT clear session files or force QR.
 */
async function refreshConnection(): Promise<boolean> {
  const current = client;
  if (!current) {
    markUnverified(sessionStatus || "disconnected", { failed: false, forcePhase: "disconnected" });
    return false;
  }

  try {
    const state = current.getConnectionState ? await current.getConnectionState() : "";
    const display = displayFromState(state);

    if (display && isDisconnectedStatus(display)) {
      applyPhase(display, { connected: false, verificationFailed: false });
      return false;
    }

    const isConn = current.isConnected ? await current.isConnected() : false;
    if (isConn) {
      await capturePhone();
      markVerified(display && isReadyStatus(display) ? display : "inChat");
      return true;
    }

    // Client present but not connected — waiting for QR / pairing.
    if (display) {
      applyPhase(display, { connected: false, verificationFailed: false });
    } else if (lastQr) {
      applyPhase("notLogged", { connected: false, verificationFailed: false });
    } else {
      applyPhase(sessionStatus || "disconnected", { connected: false, verificationFailed: false });
    }
    return false;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    // Keep phone/session data; do not request a new QR automatically.
    markUnverified(sessionStatus || "unknown", { failed: true });
    console.warn("[whatsapp] connection check failed:", message.slice(0, 120));
    return false;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (qrDropTimer) {
    clearTimeout(qrDropTimer);
    qrDropTimer = null;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    void refreshConnection();
  }, 2000);
}

function handleInterfaceState(state: unknown) {
  const display = displayFromState(state).toUpperCase();
  if (!display) return;
  if (["MAIN", "CONVERSATION", "CHAT", "CONNECTED"].some((name) => display.includes(name))) {
    // Status hint only — verification still required via refreshConnection.
    sessionStatus = "inChat";
    phase = "connecting";
    void refreshConnection();
    return;
  }
  if (display.includes("SYNC")) {
    sessionStatus = "SYNCING";
    phase = "connecting";
    return;
  }
  if (!display.includes("QR")) return;
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

/**
 * Kill only Chromium processes tied to this service's chrome-profile.
 * Never broad `pkill -f chromium`.
 */
function killOrphanChrome(): Promise<void> {
  return new Promise((resolve) => {
    const profile = dataPath("chrome-profile").replace(/\\/g, "/");
    try {
      const listed = spawnSync("ps", ["-eo", "pid=,args="], {
        encoding: "utf8",
        timeout: 5000,
      });
      if (listed.status === 0 && listed.stdout) {
        for (const line of listed.stdout.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (!/chrom(e|ium)/i.test(trimmed)) continue;
          // Match only this service profile path — never all chrome-profile processes.
          if (!trimmed.includes(profile)) continue;
          const pid = Number.parseInt(trimmed.split(/\s+/)[0] || "", 10);
          if (!Number.isFinite(pid) || pid <= 1) continue;
          try {
            process.kill(pid, "SIGTERM");
          } catch {
            /* ignore */
          }
        }
        resolve();
        return;
      }
    } catch {
      /* fall through */
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

export function getStatus() {
  return {
    connected,
    status: sessionStatus,
    phase,
    session: config.session,
    enabled: config.enabled,
    verification_failed: verificationFailed,
    last_verified_at: lastVerifiedAt,
    qr_pending: Boolean(!connected && !lastQr && !verificationFailed),
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
    qr_pending: Boolean(!connected && !lastQr && !verificationFailed),
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
        // Never log QR payload.
        lastQr = stripQr(base64 || "") || lastQr;
        lastQrIssuedAt = new Date().toISOString();
        applyPhase("notLogged", { connected: false, verificationFailed: false });
      },

      statusFind: (status) => {
        if (gen !== generation) return;
        const raw = String(status || "unknown");
        if (isReadyStatus(raw)) {
          sessionStatus = raw;
          phase = "connecting";
          void refreshConnection();
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
    await attachClientListeners(created, gen);
    startPolling();
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

async function waitForClient(ms: number): Promise<WhatsAppClient | null> {
  const waitUntil = Date.now() + ms;
  while (Date.now() < waitUntil) {
    const current = client;
    if (current) return current;
    await sleep(200);
  }
  return client;
}

async function closeClient(current: WhatsAppClient | null) {
  if (!current) return;
  if (current.logout) await current.logout().catch(() => undefined);
  if (current.close) await current.close().catch(() => undefined);
}

async function stopWhatsApp() {
  generation += 1;
  const pending = starting;
  starting = null;
  stopPolling();
  let current = client;
  client = null;
  connected = false;
  verificationFailed = false;
  lastVerifiedAt = null;
  lastQr = null;
  lastQrIssuedAt = null;
  lastPhone = "";
  applyPhase("stopped", { connected: false, verificationFailed: false, keepPhone: false, clearQr: true });
  if (!current && pending) {
    current = await waitForClient(8000);
    client = null;
  }
  await closeClient(current);
  await sleep(800);
  await killOrphanChrome();
  await sleep(400);
}

export async function unlinkWhatsApp() {
  if (unlinking) return unlinking;
  unlinking = (async () => {
    await stopWhatsApp();
    clearSessionData();
    unlockChromeProfile();
    applyPhase("waiting_qr", { connected: false, verificationFailed: false, keepPhone: false, clearQr: true });
    startWhatsApp().catch(() => {
      console.error("[whatsapp] failed to restart after unlink");
    });
    const deadline = Date.now() + 15000;
    while (!lastQr && !connected && Date.now() < deadline) {
      await sleep(250);
    }
    return {
      whatsapp: getSession(),
      qr: getQr(),
    };
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

export async function sendText(phone: string, message: string): Promise<{ messageId: string }> {
  if (!config.enabled) {
    const error = new Error("WhatsApp sending is disabled.");
    (error as Error & { code?: string }).code = "WHATSAPP_DISABLED";
    throw error;
  }
  const ready = await refreshConnection();
  if (!ready || !connected || !client) {
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
  const normalized = normalizeSendPhone(phone);
  if (!normalized) {
    const error = new Error("Phone number is not a valid international number.");
    (error as Error & { code?: string }).code = "INVALID_PHONE";
    throw error;
  }
  // Never log message body or tokens.
  const result = await client.sendText(normalized, message);
  return { messageId: resultId(result) || `${Date.now()}` };
}
