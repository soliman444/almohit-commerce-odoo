import { config } from "./config.js";
import { listen } from "./server.js";
import { startWhatsApp } from "./whatsapp.js";

listen();

if (!config.token) {
  console.warn("[whatsapp] WHATSAPP_SERVICE_TOKEN is empty; POST /send is denied.");
}

startWhatsApp().catch((error) => {
  console.error("[whatsapp] failed to start WhatsApp Web session", error);
});
