const { test } = require("node:test");
const assert = require("node:assert/strict");
const { AgentController, validEvent } = require("../electron/controller.cjs");
const event = (n = 1, patch = {}) => ({
  id: "a".repeat(32) + ":" + n,
  runId: "a".repeat(32),
  sessionId: "b".repeat(32),
  sequence: n,
  timestamp: new Date().toISOString(),
  status: "idle",
  type: "ready",
  message: "Ready",
  payload: {},
  ...patch,
});
function make() {
  const c = new AgentController("W:/intentra");
  clearInterval(c.timer);
  return c;
}
test("strict validation rejects extras, invalid scalars and status", () => {
  assert(validEvent(event()));
  for (const patch of [
    { extra: 1 },
    { status: "invented" },
    { payload: { x: { nested: true } } },
    { sequence: 0 },
    { id: "bad" },
    { timestamp: "bad" },
  ])
    assert(!validEvent(event(1, patch)));
});
test("duplicate and stale events are rejected, gaps remain visible", () => {
  const c = make();
  assert(c.accept(event()));
  assert(!c.accept(event()));
  c.accept(event(4));
  assert.equal(c.events.length, 2);
  assert.equal(c.dropped, 2);
  assert(!c.accept(event(3)));
});
test("partial frames are reassembled and malformed frames rejected", () => {
  const c = make();
  const frame = JSON.stringify(event()) + "\n";
  c.consume(frame.slice(0, 30));
  assert.equal(c.events.length, 0);
  c.consume(frame.slice(30));
  assert.equal(c.events.length, 1);
  c.consume("bad\n");
  assert.match(c.error, /Malformed/);
});
test("heartbeats do not flood history", () => {
  const c = make();
  c.accept(event());
  c.accept(event(2, { type: "heartbeat" }));
  assert.equal(c.events.length, 1);
  assert.equal(c.connection, "connected");
});
test("emergency stop cannot be overwritten by late work", () => {
  const c = make();
  c.accept(event(1, { status: "emergency_stopped", type: "emergency" }));
  assert(!c.accept(event(2, { status: "executing" })));
  assert.equal(c.current.status, "emergency_stopped");
});
test("renderer reconnect obtains authoritative snapshot without process launch", () => {
  const c = make();
  c.accept(event());
  assert.deepEqual(c.snapshot().events, [
    event(1, { timestamp: c.events[0].timestamp }),
  ]);
  assert.equal(c.child, null);
});
test("only explicit pipe operations are allowed", () => {
  const c = make();
  assert.throws(() => c.send("eval"));
  assert.throws(() => c.send("confirm"));
});
test("event history is bounded and can be cleared", () => {
  const c = make();
  for (let i = 1; i < 1601; i++) c.accept(event(i));
  assert.equal(c.events.length, 1500);
  c.clearHistory();
  assert.equal(c.events.length, 0);
});
test("foreign process events cannot splice into the current stream", () => {
  const c = make();
  c.accept(event());
  assert(
    !c.accept(event(2, { runId: "c".repeat(32), id: "c".repeat(32) + ":2" })),
  );
});

test("start is singleton, connection failure is visible, restart is explicit", () => {
  const { EventEmitter } = require("node:events");
  const { PassThrough } = require("node:stream");
  const old = process.env.INTENTRA_PYTHON;
  process.env.INTENTRA_PYTHON = process.execPath;
  let calls = 0;
  const spawn = () => {
    calls++;
    const p = new EventEmitter();
    p.stdout = new PassThrough();
    p.stderr = new PassThrough();
    p.stdin = new PassThrough();
    p.kill = () => {};
    return p;
  };
  const c = new AgentController("W:/intentra", spawn);
  clearInterval(c.timer);
  try {
    c.start();
    const first = c.child;
    c.start();
    assert.equal(calls, 1);
    first.emit("close", 1);
    assert.equal(c.snapshot().running, false);
    assert.match(c.error, /exited/);
    assert.equal(calls, 1);
    c.start();
    assert.equal(calls, 2);
  } finally {
    if (old === undefined) delete process.env.INTENTRA_PYTHON;
    else process.env.INTENTRA_PYTHON = old;
  }
});
