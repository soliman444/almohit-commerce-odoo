import assert from "node:assert/strict";
import { describe, it, beforeEach } from "node:test";
import { BrowserOpQueue } from "./browserQueue.ts";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

describe("BrowserOpQueue serialization", () => {
  /** @type {BrowserOpQueue} */
  let q;

  beforeEach(() => {
    q = new BrowserOpQueue();
  });

  it("poll starts before send — send waits, poll does not overlap CDP", async () => {
    const order = [];
    const poll = q.runRefresh(async () => {
      order.push("poll-start");
      await sleep(40);
      order.push("poll-end");
      return true;
    });
    await sleep(5);
    const send = q.runExclusive("send", async () => {
      order.push("send-start");
      assert.equal(q.isSendInFlight(), true);
      await sleep(10);
      order.push("send-end");
      return "ok";
    });
    await Promise.all([poll, send]);
    assert.deepEqual(order, ["poll-start", "poll-end", "send-start", "send-end"]);
  });

  it("send starts before poll — poll is skipped (no queue pile-up)", async () => {
    let pollRan = false;
    const send = q.runExclusive("send", async () => {
      await sleep(50);
      return "sent";
    });
    await sleep(5);
    const polled = q.requestPollRefresh(async () => {
      pollRan = true;
      return false;
    });
    assert.equal(polled, null);
    await send;
    assert.equal(pollRan, false);
  });

  it("concurrent sends are serialized", async () => {
    const order = [];
    const a = q.runExclusive("send", async () => {
      order.push("a-start");
      await sleep(30);
      order.push("a-end");
      return 1;
    });
    const b = q.runExclusive("send", async () => {
      order.push("b-start");
      await sleep(5);
      order.push("b-end");
      return 2;
    });
    const results = await Promise.all([a, b]);
    assert.deepEqual(order, ["a-start", "a-end", "b-start", "b-end"]);
    assert.equal(results[0].result, 1);
    assert.equal(results[1].result, 2);
    assert.ok(results[1].queueWaitMs >= 20);
  });

  it("refresh concurrent with send does not interleave", async () => {
    const order = [];
    const send = q.runExclusive("send", async () => {
      order.push("send");
      await sleep(40);
      return true;
    });
    await sleep(5);
    const refresh = q.runRefresh(async () => {
      order.push("refresh");
      return true;
    });
    await Promise.all([send, refresh]);
    assert.deepEqual(order, ["send", "refresh"]);
  });

  it("hanging send still releases queue for later refresh", async () => {
    let released = false;
    const hung = q.runExclusive("send", async () => {
      await sleep(40);
      return "done";
    });
    const after = hung.then(() =>
      q.runRefresh(async () => {
        released = true;
        return true;
      }),
    );
    await after;
    assert.equal(released, true);
  });

  it("rejected send still releases queue", async () => {
    await assert.rejects(
      () =>
        q.runExclusive("send", async () => {
          throw new Error("send boom");
        }),
      /send boom/,
    );
    const r = await q.runRefresh(async () => true);
    assert.equal(r, true);
  });

  it("failed refresh does not leave send blocked", async () => {
    await assert.rejects(
      () =>
        q.runRefresh(async () => {
          throw new Error("refresh boom");
        }),
      /refresh boom/,
    );
    const { result } = await q.runExclusive("send", async () => "ok");
    assert.equal(result, "ok");
  });

  it("polling resumes after send (second poll runs)", async () => {
    await q.runExclusive("send", async () => {
      await sleep(20);
      return true;
    });
    let ran = false;
    const polled = q.requestPollRefresh(async () => {
      ran = true;
      return true;
    });
    assert.ok(polled);
    await polled;
    assert.equal(ran, true);
  });

  it("no deadlock: send calls nested exclusive refresh inline", async () => {
    const { result } = await q.runExclusive("send", async () => {
      // Nested exclusive must not wait on itself.
      const nested = await q.runExclusive("refresh", async () => "nested");
      return nested.result;
    });
    assert.equal(result, "nested");
  });

  it("coalesced refresh waiters share one execution", async () => {
    let runs = 0;
    const a = q.runRefresh(async () => {
      runs += 1;
      await sleep(30);
      return true;
    });
    const b = q.runRefresh(async () => {
      runs += 1;
      return false;
    });
    const [ra, rb] = await Promise.all([a, b]);
    assert.equal(runs, 1);
    assert.equal(ra, true);
    assert.equal(rb, true);
  });

  it("requestPollRefresh drops while refresh already in flight", async () => {
    const first = q.runRefresh(async () => {
      await sleep(40);
      return true;
    });
    await sleep(5);
    const second = q.requestPollRefresh(async () => false);
    assert.equal(second, null);
    await first;
  });
});
