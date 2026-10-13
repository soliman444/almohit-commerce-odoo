import path from "node:path";

function env(name: string, fallback = ""): string {
  return (process.env[name] ?? fallback).trim();
}

function envFlag(name: string, fallback = false): boolean {
  const raw = env(name).toLowerCase();
  if (!raw) return fallback;
  return raw === "1" || raw === "true" || raw === "yes";
}

export const config = {
  port: Number(env("PORT", "3000")) || 3000,
  token: env("WHATSAPP_SERVICE_TOKEN"),
  enabled: envFlag("WHATSAPP_ENABLED", true),
  session: env("WHATSAPP_SESSION", "almohit"),
  dataDir: path.resolve(env("WHATSAPP_DATA_DIR", "./data")),
  chromePath: env("CHROME_PATH") || undefined,
};
