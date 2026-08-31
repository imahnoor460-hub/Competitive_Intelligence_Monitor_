import { test } from "node:test";
import assert from "node:assert/strict";

import { JobPollerRegistry, isTerminalStatus } from "./job-poller.ts";

const INTERVAL = 3000;

/** Lets queued promise callbacks run — the registry settles a job inside
 * .then(), which is a microtask, so ticking the clock alone isn't enough. */
async function flush(times = 6): Promise<void> {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

interface FakeJob {
  status: string;
  surfaces_discovered?: number;
}

/** Poll stub returning `statuses` in order, repeating the last one forever.
 * The literal "throw" yields a rejected promise instead. */
function stubPoll(statuses: string[]) {
  let calls = 0;
  const poll = async (): Promise<FakeJob> => {
    const status = statuses[Math.min(calls, statuses.length - 1)];
    calls += 1;
    if (status === "throw") throw new Error("network blip");
    return { status, surfaces_discovered: 3 };
  };
  return { poll, calls: () => calls };
}

test("terminal statuses match what the API actually serializes", () => {
  // Verified against CompetitorDiscoveryJobStatus: queued | running | success
  // | failed. A drift here is what would silently make a poller immortal.
  assert.equal(isTerminalStatus("success"), true);
  assert.equal(isTerminalStatus("failed"), true);
  assert.equal(isTerminalStatus("queued"), false);
  assert.equal(isTerminalStatus("running"), false);
});

test("1. starting the same job twice creates only one poller", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll, calls } = stubPoll(["running"]);

  assert.equal(registry.start(2, poll, () => {}), true);
  assert.equal(registry.start(2, poll, () => {}), false, "second start is a no-op");
  assert.equal(registry.activeCount, 1);

  t.mock.timers.tick(INTERVAL);
  await flush();

  assert.equal(calls(), 1, "one interval means one request per tick");
});

test("1b. the same job arriving many times still yields one request per tick", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll, calls } = stubPoll(["running"]);

  for (let i = 0; i < 10; i += 1) registry.start(2, poll, () => {});

  assert.equal(registry.activeCount, 1);

  // Ticked one interval at a time with the microtasks flushed between, so each
  // request completes before the next tick — the way real elapsed time behaves.
  // Ticking three intervals at once would instead model three ticks with a
  // request still in flight, which the in-flight guard deliberately collapses
  // into one (covered by the slow-poll test below).
  for (let i = 0; i < 3; i += 1) {
    t.mock.timers.tick(INTERVAL);
    await flush();
  }

  assert.equal(calls(), 3, "3 intervals elapsed => exactly 3 requests, not 30");
});

test("2. a job returning success stops polling and settles once", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll, calls } = stubPoll(["running", "success"]);
  const settled: FakeJob[] = [];

  registry.start(2, poll, (job) => settled.push(job));

  t.mock.timers.tick(INTERVAL);
  await flush();
  assert.equal(registry.isPolling(2), true, "still polling while running");

  t.mock.timers.tick(INTERVAL);
  await flush();

  assert.equal(registry.isPolling(2), false);
  assert.equal(registry.activeCount, 0);
  assert.deepEqual(settled.map((j) => j.status), ["success"]);

  // The reported symptom: no further requests once the job is terminal.
  const callsAtSettle = calls();
  t.mock.timers.tick(INTERVAL * 20);
  await flush();
  assert.equal(calls(), callsAtSettle, "0 further requests after success");
});

test("3. a job returning failed stops polling and settles once", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll, calls } = stubPoll(["failed"]);
  const settled: FakeJob[] = [];

  registry.start(2, poll, (job) => settled.push(job));

  t.mock.timers.tick(INTERVAL);
  await flush();

  assert.equal(registry.isPolling(2), false);
  assert.deepEqual(settled.map((j) => j.status), ["failed"]);

  const callsAtSettle = calls();
  t.mock.timers.tick(INTERVAL * 20);
  await flush();
  assert.equal(calls(), callsAtSettle, "0 further requests after failure");
});

test("4. a queued/running job keeps polling at one request per interval", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll, calls } = stubPoll(["queued", "running", "running", "running"]);
  const settled: FakeJob[] = [];

  registry.start(2, poll, (job) => settled.push(job));

  for (let i = 1; i <= 4; i += 1) {
    t.mock.timers.tick(INTERVAL);
    await flush();
    assert.equal(calls(), i, `expected ${i} requests after ${i} intervals`);
    assert.equal(registry.isPolling(2), true);
  }

  assert.deepEqual(settled, [], "a non-terminal job never settles");
});

test("5. a failing poll neither stops nor wedges the poller", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll, calls } = stubPoll(["throw", "throw", "success"]);
  const settled: FakeJob[] = [];

  registry.start(2, poll, (job) => settled.push(job));

  t.mock.timers.tick(INTERVAL);
  await flush();
  assert.equal(registry.isPolling(2), true, "a transient error keeps polling");

  // The important half: the error must clear the in-flight flag, or the
  // poller would tick forever without ever issuing another request.
  t.mock.timers.tick(INTERVAL);
  await flush();
  assert.equal(calls(), 2, "retries after an error");

  t.mock.timers.tick(INTERVAL);
  await flush();
  assert.equal(registry.isPolling(2), false, "and can still settle afterwards");
  assert.deepEqual(settled.map((j) => j.status), ["success"]);
  assert.equal(registry.activeCount, 0, "no orphaned interval left behind");
});

test("6. stopAll clears every interval the registry owns", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const a = stubPoll(["running"]);
  const b = stubPoll(["running"]);
  const c = stubPoll(["running"]);

  registry.start(1, a.poll, () => {});
  registry.start(2, b.poll, () => {});
  registry.start(3, c.poll, () => {});
  assert.equal(registry.activeCount, 3);

  // What the provider's unmount cleanup calls.
  registry.stopAll();

  assert.equal(registry.activeCount, 0);
  assert.equal(registry.isPolling(2), false);

  t.mock.timers.tick(INTERVAL * 10);
  await flush();

  assert.equal(a.calls() + b.calls() + c.calls(), 0, "no requests after unmount");
});

test("7. a remount does not accumulate pollers for the same job", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });

  // Mount: one provider, one registry, one poller.
  const first = new JobPollerRegistry(INTERVAL);
  const before = stubPoll(["running"]);
  first.start(2, before.poll, () => {});

  // Unmount: the effect cleanup runs. Without this the previous instance's
  // interval kept firing forever, which is what produced repeated requests for
  // one job from several sockets.
  first.stopAll();

  // Remount: a fresh registry, and the job tracked again.
  const second = new JobPollerRegistry(INTERVAL);
  const after = stubPoll(["running"]);
  second.start(2, after.poll, () => {});

  for (let i = 0; i < 3; i += 1) {
    t.mock.timers.tick(INTERVAL);
    await flush();
  }

  assert.equal(before.calls(), 0, "the unmounted registry issues nothing");
  assert.equal(after.calls(), 3, "exactly one request per interval overall");
});

test("a poll slower than the interval does not stack concurrent requests", async (t) => {
  // The root cause of the reported log: setInterval with an async callback
  // fires again whether or not the previous request finished, so a slow poll
  // piled up concurrent requests for one job — arriving less than the interval
  // apart, each from its own socket.
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);

  let calls = 0;
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const poll = async (): Promise<FakeJob> => {
    calls += 1;
    await gate;
    return { status: "running" };
  };

  registry.start(2, poll, () => {});

  t.mock.timers.tick(INTERVAL * 5);
  await flush();

  assert.equal(calls, 1, "5 intervals elapsed but the first request never returned");

  release!();
  await flush();

  t.mock.timers.tick(INTERVAL);
  await flush();
  assert.equal(calls, 2, "polling resumes once the in-flight request settles");
});

test("overlapping terminal responses settle the job exactly once", async (t) => {
  // Guards the knock-on effect: the settle handler bumps completedCount, which
  // the dashboard watches to refetch. Settling N times meant N full reloads.
  t.mock.timers.enable({ apis: ["setInterval"] });
  const registry = new JobPollerRegistry(INTERVAL);
  const { poll } = stubPoll(["success"]);
  let settleCount = 0;

  registry.start(2, poll, () => {
    settleCount += 1;
  });

  t.mock.timers.tick(INTERVAL * 5);
  await flush();

  assert.equal(settleCount, 1);
});
