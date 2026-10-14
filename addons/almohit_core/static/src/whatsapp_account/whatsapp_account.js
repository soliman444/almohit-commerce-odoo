/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

export class AlmohitWhatsAppAccount extends Component {
    static template = "almohit_core.WhatsAppAccount";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({
            loading: true,
            busy: false,
            connected: false,
            status_text: "",
            phone: "",
            account_phone: "",
            qr: false,
            error: false,
        });
        onWillStart(() => this.refresh());
        this.timer = setInterval(() => this.refresh(), 2000);
        onWillUnmount(() => clearInterval(this.timer));
    }

    get qrSrc() {
        return this.state.qr ? `data:image/png;base64,${this.state.qr}` : "";
    }

    async refresh() {
        if (this.state.busy) {
            return;
        }
        try {
            const data = await this.orm.call("almohit.whatsapp.account", "get_live_state", []);
            this.applyState(data);
        } catch (error) {
            this.state.status_text = error?.message || _t("Could not read WhatsApp status.");
        } finally {
            this.state.loading = false;
        }
    }

    applyState(data) {
        this.state.connected = Boolean(data.connected);
        this.state.status_text = data.status_text || "";
        this.state.phone = data.phone || "";
        this.state.account_phone = data.account_phone || "";
        this.state.qr = this.state.connected ? false : data.qr || false;
        this.state.error = data.error || false;
    }

    confirmChangeAccount() {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Change WhatsApp account"),
            body: _t("Disconnect the current WhatsApp number and show a QR code for the new phone?"),
            confirm: () => this.changeAccount(),
            confirmLabel: _t("Change account"),
        });
    }

    async changeAccount() {
        this.state.busy = true;
        this.state.connected = false;
        this.state.qr = false;
        this.state.status_text = _t("Disconnecting...");
        try {
            const data = await this.orm.call("almohit.whatsapp.account", "unlink_account", []);
            this.applyState(data);
            if (data.error) {
                this.notification.add(data.error, { type: "danger" });
            } else {
                this.notification.add(_t("Scan the QR with the new WhatsApp number."), {
                    type: "info",
                });
            }
        } catch (error) {
            this.notification.add(error?.message || _t("Could not change the account."), {
                type: "danger",
            });
        } finally {
            this.state.busy = false;
            this.refresh();
        }
    }
}

registry.category("actions").add("almohit_whatsapp_account", AlmohitWhatsAppAccount);
