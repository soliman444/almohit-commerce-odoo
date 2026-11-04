import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  evaluateConnectionCheck,
  isReadyStatus,
  isWppInitError,
  phaseFromStatus,
} from "./sessionStatus.ts";

describe("WPP init errors during bootstrap", () => {
  it("detects WPP is not defined", () => {
    assert.equal(isWppInitError(new Error("WPP is not defined")), true);
  });

  it("detects Socket.state undefined read", () => {
    assert.equal(
      isWppInitError(new Error("Cannot read properties of undefined (reading 'state')")),
      true,
    );
  });

  it("WPP undefined during init is not verification_failed", () => {
    const r = evaluateConnectionCheck({
      display: "QR",
      isConnected: false,
      hasQr: true,
      checkFailed: true,
      initError: true,
    });
    assert.equal(r.connected, false);
    assert.equal(r.verificationFailed, false);
    assert.equal(r.phase, "waiting_qr");
    assert.equal(r.keepQr, true);
  });

  it("getConnectionState WPP error during SYNCING stays connecting", () => {
    const r = evaluateConnectionCheck({
      display: "SYNCING",
      isConnected: false,
      hasQr: false,
      checkFailed: true,
      initError: true,
    });
    assert.equal(r.connected, false);
    assert.equal(r.phase, "connecting");
    assert.equal(r.verificationFailed, false);
  });
});

describe("auth vs isConnected", () => {
  it("isConnected=true with QR → not connected, keep QR", () => {
    const r = evaluateConnectionCheck({
      display: "QR",
      isConnected: true,
      hasQr: true,
    });
    assert.equal(r.connected, false);
    assert.equal(r.phase, "waiting_qr");
    assert.equal(r.keepQr, true);
  });

  it("SYNCING is connecting, not connected", () => {
    const r = evaluateConnectionCheck({
      display: "SYNCING",
      isConnected: true,
      hasQr: false,
    });
    assert.equal(r.connected, false);
    assert.equal(r.phase, "connecting");
  });

  it("does not announce connected before auth", () => {
    const r = evaluateConnectionCheck({
      display: "browserReady",
      isConnected: true,
      hasQr: false,
      mainReady: false,
    });
    assert.equal(r.connected, false);
  });

  it("mainReady after QR scan → connected", () => {
    const r = evaluateConnectionCheck({
      display: "inChat",
      isConnected: true,
      hasQr: false,
      mainReady: true,
    });
    assert.equal(r.connected, true);
    assert.equal(r.phase, "connected");
    assert.equal(r.keepQr, false);
  });

  it("isConnected=false with inChat → not confirmed", () => {
    const r = evaluateConnectionCheck({
      display: "inChat",
      isConnected: false,
      hasQr: false,
      mainReady: false,
    });
    assert.equal(r.connected, false);
  });

  it("connected after sync completes with mainReady", () => {
    const r = evaluateConnectionCheck({
      display: "SYNCING",
      isConnected: false,
      hasQr: false,
      mainReady: true,
    });
    assert.equal(r.connected, true);
    assert.equal(r.keepQr, false);
  });
});

describe("QR lifecycle helpers", () => {
  it("probeSkipped with QR stays waiting_qr", () => {
    const r = evaluateConnectionCheck({
      display: "notLogged",
      isConnected: false,
      hasQr: true,
      probeSkipped: true,
    });
    assert.equal(r.phase, "waiting_qr");
    assert.equal(r.keepQr, true);
    assert.equal(r.verificationFailed, false);
  });

  it("phaseFromStatus QR wins over clientConnected", () => {
    assert.equal(phaseFromStatus("QR", { clientConnected: true, hasQr: true }), "waiting_qr");
  });

  it("isReadyStatus rejects QR and syncing", () => {
    assert.equal(isReadyStatus("QR"), false);
    assert.equal(isReadyStatus("SYNCING"), false);
    assert.equal(isReadyStatus("inChat"), true);
  });
});
