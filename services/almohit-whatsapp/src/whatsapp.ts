import fs from "node:fs";
import path from "node:path";
import { create, type Whatsapp } from "@wppconnect-team/wppconnect";
import qrcode from "qrcode-terminal";
import { config } from "./config.js";
import { normalizeSendPhone } from "./phone.js";

let client: Whatsapp | null = null;
let sessionStatus = "starting";
let connected = false;
let starting: Promise<void> | null = null;

function dataPath(...parts: string[]): string {
  return path.join(config.dataDir, ...parts);
}

function markStatus(status: string) {
  sessionStatus = status;
  connected = [
    "isLogged",
    "qrReadSuccess",
    "inChat",
    "chatsAvailable",
    "successChat",
  ].includes(status);
  console.log(`[whatsapp] session=${config.session} status=${status} connected=${connected}`);
}

export function getStatus() {
  return {
    connected,
    status: sessionStatus,
    session: config.session,
    enabled: config.enabled,
  };
}

export async function startWhatsApp(): Promise<void> {
  if (starting) return starting;
  starting = (async () => {
    fs.mkdirSync(dataPath("tokens"), { recursive: true });
    fs.mkdirSync(dataPath("chrome-profile"), { recursive: true });
    client = await create({
      session: config.session,
      folderNameToken: dataPath("tokens"),
      headless: true,
      devtools: false,
      useChrome: Boolean(config.chromePath),
      logQR: false,
      disableWelcome: true,
      updatesLog: false,
      autoClose: 0,
      waitForLogin: true,
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
      catchQR: (_base64, ascii, _attempts, urlCode) => {
        markStatus("notLogged");
        console.log("[whatsapp] Scan this QR with WhatsApp:");
        if (ascii) console.log(ascii);
        else if (urlCode) qrcode.generate(urlCode, { small: true });
      },
      statusFind: (status) => {
        markStatus(String(status));
      },
    });
    markStatus(sessionStatus === "starting" ? "isLogged" : sessionStatus);
    connected = true;
  })();
  try {
    await starting;
  } catch (error) {
    starting = null;
    connected = false;
    markStatus("error");
    throw error;
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
