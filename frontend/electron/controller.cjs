const { EventEmitter } = require("node:events");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const statuses = new Set([
  "offline",
  "idle",
  "listening",
  "transcribing",
  "understanding",
  "planning",
  "awaiting_confirmation",
  "executing",
  "completed",
  "failed",
  "cancelled",
  "emergency_stopped",
]);
function validEvent(e) {
  if (!e || typeof e !== "object" || Array.isArray(e)) return false;
  const keys = [
    "id",
    "sessionId",
    "runId",
    "sequence",
    "timestamp",
    "status",
    "type",
    "message",
    "payload",
  ];
  if (
    Object.keys(e).some((k) => !keys.includes(k)) ||
    keys.some((k) => !(k in e))
  )
    return false;
  if (
    ![
      "id",
      "sessionId",
      "runId",
      "timestamp",
      "status",
      "type",
      "message",
    ].every((k) => typeof e[k] === "string")
  )
    return false;
  if (!/^[a-f0-9]{32}$/.test(e.runId) || !/^[a-f0-9]{32}$/.test(e.sessionId))
    return false;
  if (
    !Number.isSafeInteger(e.sequence) ||
    e.sequence < 1 ||
    e.id !== `${e.runId}:${e.sequence}`
  )
    return false;
  if (
    !statuses.has(e.status) ||
    !Number.isFinite(Date.parse(e.timestamp)) ||
    e.message.length > 4000 ||
    !/^[a-z_]{1,40}$/.test(e.type)
  )
    return false;
  if (!e.payload || Array.isArray(e.payload) || typeof e.payload !== "object")
    return false;
  return (
    Object.keys(e.payload).length <= 30 &&
    Object.entries(e.payload).every(
      ([k, v]) =>
        /^[a-z_]{1,40}$/.test(k) &&
        (v === null ||
          typeof v === "boolean" ||
          (typeof v === "number" && Number.isFinite(v)) ||
          (typeof v === "string" && v.length <= 4000)),
    )
  );
}
class AgentController extends EventEmitter {
  constructor(root, spawnProcess = spawn) {
    super();
    this.root = root;
    this.spawnProcess = spawnProcess;
    this.child = null;
    this.events = [];
    this.current = null;
    this.config = {};
    this.connection = "offline";
    this.error = "";
    this.buffer = "";
    this.lastSequence = 0;
    this.runId = null;
    this.lastSeen = 0;
    this.dropped = 0;
    this.timer = setInterval(() => {
      if (
        this.child &&
        Date.now() - this.lastSeen >
          (this.connection === "starting" ? 120000 : 15000)
      ) {
        this.connection = "unresponsive";
        this.error =
          "Agent health signal lost. Use Ctrl+Alt+X if work is active.";
        this.publish();
      }
    }, 2000);
    this.timer.unref();
  }
  snapshot() {
    return {
      connection: this.connection,
      running: !!this.child,
      events: this.events,
      current: this.current,
      config: this.config,
      error: this.error,
      dropped: this.dropped,
    };
  }
  publish() {
    this.emit("snapshot", this.snapshot());
  }
  accept(e) {
    if (!validEvent(e)) {
      this.error = "Malformed agent event rejected";
      this.publish();
      return false;
    }
    if (this.runId && this.runId !== e.runId) return false;
    if (e.sequence <= this.lastSequence) return false;
    if (this.lastSequence && e.sequence !== this.lastSequence + 1)
      this.dropped += e.sequence - this.lastSequence - 1;
    this.runId = e.runId;
    this.lastSequence = e.sequence;
    this.lastSeen = Date.now();
    this.connection = "connected";
    if (
      this.current?.status === "emergency_stopped" &&
      e.status !== "emergency_stopped"
    )
      return false;
    this.current = e;
    if (e.type === "configuration") this.config = e.payload;
    if (e.type !== "heartbeat") this.events = [...this.events, e].slice(-1500);
    this.publish();
    return true;
  }
  consume(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > 65536) {
      this.error = "Agent frame limit exceeded";
      this.send("stop");
      this.buffer = "";
      this.publish();
      return;
    }
    let end;
    while ((end = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 1);
      try {
        this.accept(JSON.parse(line));
      } catch {
        this.error = "Malformed agent frame rejected";
        this.publish();
      }
    }
  }
  start() {
    if (this.child) return this.snapshot();
    const backend = path.join(this.root, "backend");
    const python =
      process.env.INTENTRA_PYTHON ||
      [
        path.join(backend, ".venv-win", "Scripts", "python.exe"),
        path.join(backend, ".venv-parakeet", "Scripts", "python.exe"),
      ].find((p) => fs.existsSync(p));
    if (!python || !path.isAbsolute(python) || !fs.existsSync(python)) {
      this.error =
        "Python environment missing. Set INTENTRA_PYTHON to an absolute Python executable path.";
      this.publish();
      return this.snapshot();
    }
    this.runId = null;
    this.lastSequence = 0;
    this.buffer = "";
    this.current = null;
    this.config = {};
    this.error = "";
    this.connection = "starting";
    this.lastSeen = Date.now();
    const child = this.spawnProcess(
      python,
      ["-u", path.join(backend, "desktop_entry.py")],
      {
        cwd: backend,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
        shell: false,
        env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      },
    );
    this.child = child;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (c) => this.consume(c));
    // Do not retain potentially private legacy console output.
    child.stderr.on("data", () => {});
    child.stdin.on("error", () => {});
    child.on("error", () => {
      this.error = "Could not launch Python";
      this.connection = "offline";
      this.child = null;
      this.publish();
    });
    child.on("close", (code) => {
      if (this.child === child) this.child = null;
      this.connection = "offline";
      if (code && !this.error)
        this.error = `Agent exited (${code}). Check dependencies and local model files.`;
      this.publish();
    });
    this.publish();
    return this.snapshot();
  }
  send(op) {
    if (!["stop", "shutdown", "ping"].includes(op))
      throw new Error("Unsupported operation");
    if (this.child?.stdin.writable)
      this.child.stdin.write(JSON.stringify({ op }) + "\n");
  }
  clearHistory() {
    this.events = [];
    this.dropped = 0;
    this.publish();
  }
  async close() {
    clearInterval(this.timer);
    const child = this.child;
    if (!child) return;
    this.send("shutdown");
    await Promise.race([
      new Promise((r) => child.once("close", r)),
      new Promise((r) => setTimeout(r, 5000)),
    ]);
    if (this.child === child) {
      child.stdin.end();
      child.kill();
    }
  }
}
module.exports = { AgentController, validEvent };
