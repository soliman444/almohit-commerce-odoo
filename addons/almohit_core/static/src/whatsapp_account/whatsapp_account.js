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
        this._refreshSeq = 0;
        this._pollGeneration = 0;
        this.state = useState({
            loading: true,
            busy: false,
            connected: false,
            phase: "unknown",
            status_text: "",
            connected_phone: "",
            desired_phone: "",
            phones_match: false,
            phone_mismatch: false,
            show_change_account: false,
            qr: false,
            verification_failed: false,
            service_reachable: true,
            last_verified_at: false,
            can_manage: true,
        });
        onWillStart(() => this.refresh());
        this.timer = setInterval(() => this.refresh(), 2000);
        onWillUnmount(() => clearInterval(this.timer));
    }

    get qrSrc() {
        return this.state.qr ? `data:image/png;base64,${this.state.qr}` : "";
    }

    get statusAlertClass() {
        if (this.state.phone_mismatch) {
            return "alert alert-danger mb-3";
        }
        if (this.state.connected && this.state.phones_match) {
            return "alert alert-success mb-3";
        }
        if (this.state.connected) {
            return "alert alert-warning mb-3";
        }
        if (
            this.state.phase === "service_unreachable" ||
            this.state.verification_failed ||
            !this.state.service_reachable
        ) {
            return "alert alert-secondary mb-3";
        }
        if (this.state.phase === "waiting_qr" || this.state.phase === "connecting") {
            return "alert alert-info mb-3";
        }
        return "alert alert-warning mb-3";
    }

    get statusTitle() {
        if (this.state.loading) {
            return _t("Checking…");
        }
        if (this.state.phone_mismatch) {
            return _t("Number mismatch");
        }
        if (this.state.connected && this.state.phones_match) {
            return _t("Connected");
        }
        if (this.state.connected) {
            return _t("Connected (verify desired number)");
        }
        if (
            this.state.phase === "service_unreachable" ||
            this.state.verification_failed ||
            !this.state.service_reachable
        ) {
            return _t("Could not verify");
        }
        if (this.state.phase === "waiting_qr") {
            return _t("Waiting for QR");
        }
        if (this.state.phase === "connecting") {
            return _t("Connecting");
        }
        if (this.state.phase === "session_error") {
            return _t("Session error");
        }
        return _t("Not connected");
    }

    onDesiredPhoneInput(ev) {
        this.state.desired_phone = ev.target.value || "";
        const desired = (this.state.desired_phone || "").replace(/\D/g, "");
        const connected = (this.state.connected_phone || "").replace(/\D/g, "");
        const match =
            Boolean(this.state.connected) &&
            Boolean(desired) &&
            Boolean(connected) &&
            desired === connected;
        this.state.phones_match = match;
        this.state.show_change_account = Boolean(this.state.desired_phone.trim()) && !match;
    }

    async refresh() {
        if (this.state.busy) {
            return;
        }
        const seq = ++this._refreshSeq;
        const pollGen = this._pollGeneration;
        try {
            const data = await this.orm.call("almohit.whatsapp.account", "get_live_state", []);
            if (seq !== this._refreshSeq || pollGen !== this._pollGeneration) {
                return;
            }
            this.applyState(data);
        } catch (error) {
            if (seq !== this._refreshSeq || pollGen !== this._pollGeneration) {
                return;
            }
            this.state.connected = false;
            this.state.phase = "service_unreachable";
            this.state.verification_failed = true;
            this.state.service_reachable = false;
            this.state.status_text = error?.message || _t("Could not verify");
            this.state.qr = false;
        } finally {
            if (seq === this._refreshSeq) {
                this.state.loading = false;
            }
        }
    }

    applyState(data) {
        this.state.connected = Boolean(data.connected);
        this.state.phase = data.phase || "unknown";
        this.state.status_text = data.status_text || "";
        this.state.connected_phone = data.connected_phone || "";
        if (!this.state.busy) {
            this.state.desired_phone = data.desired_phone || "";
        }
        this.state.phones_match = Boolean(data.phones_match);
        this.state.phone_mismatch = Boolean(data.phone_mismatch);
        this.state.show_change_account = Boolean(data.show_change_account);
        this.state.qr = this.state.connected ? false : data.qr || false;
        this.state.verification_failed = Boolean(data.verification_failed);
        this.state.service_reachable = data.service_reachable !== false;
        this.state.last_verified_at = data.last_verified_at || false;
        this.state.can_manage = data.can_manage !== false;
    }

    confirmChangeAccount() {
        if (this.state.busy || !this.state.show_change_account) {
            return;
        }
        this.dialog.add(ConfirmationDialog, {
            title: _t("Change WhatsApp account"),
            body: _t(
                "The current WhatsApp session will be disconnected. You will need to scan a new QR code with the desired number. Continue?"
            ),
            confirm: () => this.changeAccount(),
            confirmLabel: _t("Change account"),
            cancel: () => {},
            cancelLabel: _t("Cancel"),
        });
    }

    async changeAccount() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        this._pollGeneration += 1;
        const myGen = this._pollGeneration;
        this.state.connected = false;
        this.state.phones_match = false;
        this.state.qr = false;
        this.state.phase = "connecting";
        this.state.status_text = _t("Disconnecting previous session…");
        try {
            const data = await this.orm.call("almohit.whatsapp.account", "change_account", [
                this.state.desired_phone,
            ]);
            if (myGen !== this._pollGeneration) {
                return;
            }
            this.applyState(data);
            if (data.error) {
                this.notification.add(data.error, { type: "danger" });
            } else {
                this.notification.add(_t("Scan the new QR with the desired WhatsApp number."), {
                    type: "info",
                });
            }
        } catch (error) {
            if (myGen !== this._pollGeneration) {
                return;
            }
            this.notification.add(error?.message || _t("Could not change the account."), {
                type: "danger",
            });
        } finally {
            if (myGen === this._pollGeneration) {
                this.state.busy = false;
                this.refresh();
            }
        }
    }
}

registry.category("actions").add("almohit_whatsapp_account", AlmohitWhatsAppAccount);
