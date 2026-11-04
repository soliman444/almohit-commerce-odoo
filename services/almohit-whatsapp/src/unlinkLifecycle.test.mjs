import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  canClearSessionData,
  canStartNewSession,
  classifyUnlinkRelink,
  computeQrPending,
  raceOutcome,
  raceWithTimeout,
} from "./unlinkLifecycle.ts";

describe("raceWithTimeout", () => {
  it("logout succeeds", async () => {
    const r = await raceWithTimeout(Promise.resolve("ok"), 1000);
    assert.equal(r.outcome, "ok");
    assert.equal(r.value, "ok");
  });

  it("logout hangs → timeout without cancelling original", async () => {
    let finished = false;
    const hung = new Promise((resolve) => {
      setTimeout(() => {
        finished = true;
        resolve("late");
      }, 80);
    });
    const r = await raceWithTimeout(hung, 20);
    assert.equal(r.outcome, "timeout");
    await hung;
    assert.equal(finished, true);
  });

  it("logout fails", async () => {
    const r = await raceWithTimeout(Promise.reject(new Error("logout boom")), 1000);
    assert.equal(r.outcome, "error");
  });

  it("close hangs → timeout", async () => {
    const hung = new Promise(() => undefined);
    const r = await raceWithTimeout(hung, 30);
    assert.equal(r.outcome, "timeout");
  });

  it("raceOutcome mapping", () => {
    assert.equal(raceOutcome("pending", true), "timeout");
    assert.equal(raceOutcome("fulfilled", false), "ok");
    assert.equal(raceOutcome("rejected", false), "error");
  });
});

describe("session clear and new client gates", () => {
  it("blocks clear until chrome stopped", () => {
    assert.equal(canClearSessionData({ clientDetached: true, chromeStopped: false }), false);
    assert.equal(canClearSessionData({ clientDetached: false, chromeStopped: true }), false);
    assert.equal(canClearSessionData({ clientDetached: true, chromeStopped: true }), true);
  });

  it("blocks new session on same profile until clear + chrome stopped", () => {
    assert.equal(
      canStartNewSession({
        previousClientDetached: true,
        chromeStopped: false,
        sessionDataCleared: true,
      }),
      false,
    );
    assert.equal(
      canStartNewSession({
        previousClientDetached: true,
        chromeStopped: true,
        sessionDataCleared: false,
      }),
      false,
    );
    assert.equal(
      canStartNewSession({
        previousClientDetached: true,
        chromeStopped: true,
        sessionDataCleared: true,
      }),
      true,
    );
  });
});

describe("unlink / relink classification", () => {
  it("QR after unlink is not a false full-link success alone, but marks relink progress", () => {
    const r = classifyUnlinkRelink({
      stopCompleted: true,
      sessionCleared: true,
      startAttempted: true,
      startFailed: false,
      hasQr: true,
      connected: false,
    });
    assert.equal(r.unlinked, true);
    assert.equal(r.relinked, true);
    assert.equal(r.relink_error, null);
  });

  it("no QR after start → unlink ok, relink incomplete", () => {
    const r = classifyUnlinkRelink({
      stopCompleted: true,
      sessionCleared: true,
      startAttempted: true,
      startFailed: false,
      hasQr: false,
      connected: false,
    });
    assert.equal(r.unlinked, true);
    assert.equal(r.relinked, false);
    assert.match(r.relink_error || "", /QR/);
  });

  it("start failed → no false success", () => {
    const r = classifyUnlinkRelink({
      stopCompleted: true,
      sessionCleared: true,
      startAttempted: true,
      startFailed: true,
      hasQr: false,
      connected: false,
    });
    assert.equal(r.unlinked, true);
    assert.equal(r.relinked, false);
    assert.match(r.relink_error || "", /starting a new session failed/i);
  });
});

describe("qr_pending", () => {
  it("stopped status never claims pending QR", () => {
    assert.equal(
      computeQrPending({
        connected: false,
        hasQr: false,
        status: "stopped",
      }),
      false,
    );
  });

  it("has QR → qr_pending false (QR already available)", () => {
    assert.equal(
      computeQrPending({
        connected: false,
        hasQr: true,
        status: "notLogged",
      }),
      false,
    );
  });
});
