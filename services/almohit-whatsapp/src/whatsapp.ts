import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { create, type Whatsapp } from "@wppconnect-team/wppconnect";
import { config } from "./config.js";
import { normalizeSendPhone } from "./phone.js";

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

const CONNECTED_STATUSES = new Set([
  "islogged",
  "qrreadsuccess",
  "inchat",
  "chatsavailable",
  "successchat",
  "main",
  "syncing",
  "connected",
  "open",
]);

let client: WhatsAppClient | null = null;
let sessionStatus = "starting";
let connected = false;
let starting: Promise<void> | null = null;
let generation = 0;
let lastQr: string | null = null;
let lastPhone = "";
let pollTimer: ReturnType<typeof setInterval> | null = null;
let qrDropTimer: ReturnType<typeof setTimeout> | null = null;

function dataPath(...parts: string[]): string {
  return path.join(config.dataDir, ...parts);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isConnectedStatus(status: string): boolean {
  const compact = String(status || "").toLowerCase().replace(/[^a-z]/g, "");
  if (CONNECTED_STATUSES.has(compact)) return true;
  return ["main", "syncing", "connected", "inchat"].some((name) => compact.includes(name));
}
function isDisconnectedStatus(status: string): boolean {
  const compact = String(status || "")
    .toLowerCase()
    .replace(/[^a-z]/g, "");

  return [
    "qr",
    "notlogged",
    "unpaired",
    "unpairedidle",
    "disconnected",
    "disconnectedmobile",
    "browserclose",
    "stopped",
    "error",
    "waitingqr",
  ].some((name) => compact.includes(name));
}

function markStatus(status: string, { force = false } = {}) {
  const explicitDisconnected = isDisconnectedStatus(status);

  const nextConnected = explicitDisconnected
    ? false
    : isConnectedStatus(status);

  if (connected && !nextConnected && !force && !explicitDisconnected) {
    return;
  }

  sessionStatus = status;
  connected = nextConnected;

  if (connected) {
    lastQr = null;

    if (qrDropTimer) {
      clearTimeout(qrDropTimer);
      qrDropTimer = null;
    }
  }

  console.log(
    `[whatsapp] session=${config.session} status=${status} connected=${connected}`
  );
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

async function refreshConnection(): Promise<boolean> {
  const current = client;
  if (!current) return false;

  try {
    const state = current.getConnectionState
      ? await current.getConnectionState()
      : "";

    const display = displayFromState(state);

    if (display) {
      if (isDisconnectedStatus(display)) {
        markStatus(display, { force: true });
        return false;
      }

      if (isConnectedStatus(display)) {
        markStatus(display || "inChat");
        await capturePhone();
        return true;
      }
    }

    const isConn = current.isConnected
      ? await current.isConnected()
      : false;

    if (isConn) {
      markStatus("inChat");
      await capturePhone();
      return true;
    }

    return false;
  } catch {
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
  if (["MAIN", "SYNCING", "CONVERSATION", "CHAT", "CONNECTED"].some((name) => display.includes(name))) {
    markStatus(display.includes("SYNC") ? "SYNCING" : "inChat");
    void capturePhone();
    return;
  }
  if (!display.includes("QR")) return;
  if (!connected) {
    markStatus("notLogged", { force: true });
    return;
  }
  if (qrDropTimer) return;
  qrDropTimer = setTimeout(() => {
    qrDropTimer = null;
    void (async () => {
      const stillConnected = await refreshConnection();
      if (!stillConnected) markStatus("notLogged", { force: true });
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

        if (display) {
          markStatus(display);
        }

        if (connected) {
          void capturePhone();
        }
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
      console.warn("[whatsapp] could not clear %s", full, error);
    }
    fs.mkdirSync(full, { recursive: true });
  }
}

function killOrphanChrome(): Promise<void> {
  return new Promise((resolve) => {
    execFile("sh", ["-c", "pkill -f chromium || true"], () => resolve());
  });
}

export function getStatus() {
  return {
    connected,
    status: sessionStatus,
    session: config.session,
    enabled: config.enabled,
    qr_pending: Boolean(!connected && !lastQr),
  };
}

export function getSession() {
  return {
    connected,
    status: sessionStatus,
    session: config.session,
    enabled: config.enabled,
    phone: lastPhone,
    qr_pending: Boolean(!connected && !lastQr),
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

        lastQr = stripQr(base64 || "") || lastQr;
        markStatus("notLogged", { force: true });
      },

      statusFind: (status) => {
        if (gen !== generation) return;

        markStatus(String(status));

        if (connected) {
          void capturePhone();
        }
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
    if (sessionStatus === "starting") markStatus("browserReady");
    if (connected) await capturePhone();
  })();
  try {
    await starting;
  } catch (error) {
    if (gen === generation) {
      starting = null;
      connected = false;
      markStatus("error", { force: true });
    }
    throw error;
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
  lastQr = null;
  lastPhone = "";
  markStatus("stopped", { force: true });
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
  await stopWhatsApp();
  clearSessionData();
  unlockChromeProfile();
  markStatus("waiting_qr", { force: true });
  startWhatsApp().catch((error) => {
    console.error("[whatsapp] failed to restart after unlink", error);
  });
  const deadline = Date.now() + 15000;
  while (!lastQr && !connected && Date.now() < deadline) {
    await sleep(250);
  }
  return {
    whatsapp: getSession(),
    qr: getQr(),
  };
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
  if (!connected || !client) {
    const error = new Error("WhatsApp is not connected.");
    (error as Error & { code?: string }).code = "WHATSAPP_NOT_CONNECTED";
    throw error;
  }
  const normalized = normalizeSendPhone(phone);
  if (!normalized) {
    const error = new Error("Phone number is not a valid international number.");
    (error as Error & { code?: string }).code = "INVALID_PHONE";
    throw error;
  }
  const result = await client.sendText(normalized, message);
  return { messageId: resultId(result) || `${Date.now()}` };
}
