import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { isAuthorized } from "./auth.js";
import { config } from "./config.js";
import { getStatus, sendText } from "./whatsapp.js";

type SendBody = {
  phone?: unknown;
  message?: unknown;
};

function json(res: ServerResponse, status: number, body: unknown) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function fail(res: ServerResponse, status: number, code: string, message: string) {
  json(res, status, { ok: false, error: { code, message } });
}

async function readJson(req: IncomingMessage): Promise<SendBody> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    if (chunks.reduce((sum, item) => sum + item.length, 0) > 32_000) {
      throw new Error("payload_too_large");
    }
  }
  const raw = Buffer.concat(chunks).toString("utf8") || "{}";
  const parsed = JSON.parse(raw) as SendBody;
  if (!parsed || typeof parsed !== "object") throw new Error("invalid_json");
  return parsed;
}

async function handle(req: IncomingMessage, res: ServerResponse) {
  const url = new URL(req.url || "/", "http://almohit-whatsapp.local");
  if (req.method === "GET" && url.pathname === "/health") {
    json(res, 200, { ok: true, whatsapp: getStatus() });
    return;
  }
  if (req.method === "POST" && url.pathname === "/send") {
    if (!isAuthorized(req)) {
      fail(res, 401, "UNAUTHORIZED", "Valid bearer token required.");
      return;
    }
    let body: SendBody;
    try {
      body = await readJson(req);
    } catch {
      fail(res, 400, "INVALID_JSON", "Invalid JSON body.");
      return;
    }
    const phone = typeof body.phone === "string" ? body.phone.trim() : "";
    const message = typeof body.message === "string" ? body.message.trim() : "";
    if (!phone || !message) {
      fail(res, 400, "INVALID_PAYLOAD", "phone and message are required.");
      return;
    }
    try {
      const sent = await sendText(phone, message);
      json(res, 200, { ok: true, message_id: sent.messageId });
    } catch (error) {
      const code = (error as { code?: string }).code || "SEND_FAILED";
      const messageText = error instanceof Error ? error.message : "Send failed.";
      const status = code === "WHATSAPP_NOT_CONNECTED" || code === "WHATSAPP_DISABLED" ? 503 : 400;
      fail(res, status, code, messageText);
    }
    return;
  }
  fail(res, 404, "NOT_FOUND", "Not found.");
}

export function listen() {
  const server = createServer((req, res) => {
    handle(req, res).catch((error) => {
      console.error("[whatsapp] request failed", error);
      if (!res.headersSent) fail(res, 500, "INTERNAL_ERROR", "Internal error.");
    });
  });
  server.listen(config.port, "0.0.0.0", () => {
    console.log(`[whatsapp] listening on :${config.port}`);
  });
  return server;
}
