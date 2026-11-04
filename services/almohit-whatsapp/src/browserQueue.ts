/**
 * Serial browser/Puppeteer operation queue.
 * - One CDP-sensitive op at a time across concurrent callers.
 * - Re-entrant only within the same async context (AsyncLocalStorage) to avoid deadlock
 *   when send holds the lock and runs refreshConnectionImpl helpers that also enter the queue.
 * - Refresh coalescing: concurrent refresh waiters share one promise.
 * - Poll skips while send is active or a refresh is already queued/running.
 */

import { AsyncLocalStorage } from "node:async_hooks";

export type BrowserOpKind = "refresh" | "send" | "other";

export type BrowserQueueSnapshot = {
  sendInFlight: boolean;
  refreshInFlight: boolean;
  pending: number;
  locked: boolean;
};

type LockStore = { kind: BrowserOpKind };

type Job<T> = {
  kind: BrowserOpKind;
  run: () => Promise<T>;
  resolve: (value: { result: T; queueWaitMs: number }) => void;
  reject: (err: unknown) => void;
  enqueuedAt: number;
};

export class BrowserOpQueue {
  private readonly als = new AsyncLocalStorage<LockStore>();
  private sendInFlight = false;
  private refreshShared: Promise<boolean> | null = null;
  private chain: Promise<void> = Promise.resolve();
  private pending = 0;
  private dropPollWhileBusy = true;

  snapshot(): BrowserQueueSnapshot {
    return {
      sendInFlight: this.sendInFlight,
      refreshInFlight: Boolean(this.refreshShared),
      pending: this.pending,
      locked: Boolean(this.als.getStore()),
    };
  }

  isSendInFlight(): boolean {
    return this.sendInFlight;
  }

  /**
   * Run exclusive browser work.
   * Nested calls in the same async context run inline (no second queue wait).
   */
  async runExclusive<T>(
    kind: BrowserOpKind,
    fn: () => Promise<T>,
  ): Promise<{ result: T; queueWaitMs: number }> {
    if (this.als.getStore()) {
      const result = await fn();
      return { result, queueWaitMs: 0 };
    }

    const enqueuedAt = Date.now();
    this.pending += 1;

    const resultPromise = new Promise<{ result: T; queueWaitMs: number }>((resolve, reject) => {
      const job: Job<T> = {
        kind,
        enqueuedAt,
        run: fn,
        resolve,
        reject,
      };
      this.chain = this.chain.then(
        () => this.executeJob(job),
        () => this.executeJob(job),
      );
    });

    try {
      return await resultPromise;
    } finally {
      this.pending = Math.max(0, this.pending - 1);
    }
  }

  private async executeJob<T>(job: Job<T>): Promise<void> {
    const queueWaitMs = Math.max(0, Date.now() - job.enqueuedAt);
    if (job.kind === "send") this.sendInFlight = true;
    try {
      const result = await this.als.run({ kind: job.kind }, () => job.run());
      job.resolve({ result, queueWaitMs });
    } catch (err) {
      job.reject(err);
    } finally {
      if (job.kind === "send") this.sendInFlight = false;
    }
  }

  /**
   * Coalesced refresh: all waiters share one in-flight promise.
   */
  runRefresh(fn: () => Promise<boolean>): Promise<boolean> {
    if (this.refreshShared) return this.refreshShared;
    this.refreshShared = this.runExclusive("refresh", fn)
      .then(({ result }) => result)
      .finally(() => {
        this.refreshShared = null;
      });
    return this.refreshShared;
  }

  /**
   * Polling entry: never pile up refresh jobs behind send or an active refresh.
   */
  requestPollRefresh(fn: () => Promise<boolean>): Promise<boolean> | null {
    if (this.sendInFlight) return null;
    if (this.refreshShared) return null;
    if (this.dropPollWhileBusy && this.pending > 0) return null;
    return this.runRefresh(fn);
  }

  reset(): void {
    this.sendInFlight = false;
    this.refreshShared = null;
    this.chain = Promise.resolve();
    this.pending = 0;
  }
}

export const browserQueue = new BrowserOpQueue();
