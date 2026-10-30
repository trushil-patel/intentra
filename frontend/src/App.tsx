import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { MotionConfig, motion } from "motion/react";
import {
  AudioLines,
  ArrowUpRight,
  Command,
  PanelTop,
  Clock3,
  Settings2,
  ShieldCheck,
  ChevronRight,
  X,
  Square,
  Maximize2,
  Minimize2,
  Monitor,
  Keyboard,
  Search,
  Power,
  Trash2,
} from "lucide-react";
import { connectAgent, useAgent } from "./store";
import { labels, type AgentStatus } from "./contracts";
import { VoiceOrb } from "./VoiceOrb";
import {
  ActivityTimeline,
  CommandTranscript,
  ErrorDetails,
  ExecutionStatus,
  PermissionIndicator,
  SessionHistory,
} from "./Activity";
function loadPreference(key: string, fallback: boolean) {
  try {
    return localStorage.getItem(key) === null
      ? fallback
      : localStorage.getItem(key) === "true";
  } catch {
    return fallback;
  }
}
export default function App() {
  const { snapshot, uiError, act } = useAgent();
  const [page, setPage] = useState("center");
  const [settings, setSettings] = useState(false);
  const [palette, setPalette] = useState(false);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [session, setSession] = useState<string | null>(null);
  const [reduce, setReduce] = useState(() =>
    loadPreference("intentra.reduce", false),
  );
  const [autoOverlay, setAutoOverlay] = useState(() =>
    loadPreference("intentra.overlay", false),
  );
  const overlay = new URLSearchParams(location.search).has("overlay");
  useEffect(connectAgent, []);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((v) => !v);
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);
  useEffect(() => {
    if (autoOverlay && !overlay && snapshot.connection === "connected")
      void act("overlay");
  }, [autoOverlay, overlay, snapshot.connection, act]);
  const status: AgentStatus =
    snapshot.current?.status === "emergency_stopped"
      ? "emergency_stopped"
      : snapshot.connection === "offline"
        ? "offline"
        : (snapshot.current?.status ?? "offline");
  const currentEvents = snapshot.events.filter(
    (e) => e.sessionId === snapshot.current?.sessionId,
  );
  const events = session
    ? snapshot.events.filter((e) => e.sessionId === session)
    : currentEvents;
  const error = uiError || snapshot.error;
  function preference(
    key: string,
    value: boolean,
    update: (v: boolean) => void,
  ) {
    update(value);
    try {
      localStorage.setItem(key, String(value));
    } catch {
      useAgent.setState({ uiError: "Preference could not be saved." });
    }
  }
  const commands = [
    {
      label: "Open command center",
      run: () => {
        setPage("center");
        setSession(null);
      },
    },
    { label: "View session history", run: () => setPage("history") },
    { label: "Open settings", run: () => setSettings(true) },
    { label: "Show floating assistant", run: () => void act("overlay") },
    { label: "Emergency stop", run: () => void act("stop") },
  ];
  return (
    <MotionConfig reducedMotion={reduce ? "always" : "user"}>
      {overlay ? (
        <main className={`overlay-shell ${expanded ? "expanded" : ""}`}>
          <header className="overlay-header">
            <div className="brand">
              <AudioLines />
              <strong>Intentra</strong>
              <span className="tiny">OS</span>
            </div>
            <div className="window-actions">
              <button
                aria-label={expanded ? "Collapse overlay" : "Expand overlay"}
                onClick={() => {
                  setExpanded(!expanded);
                  void act(expanded ? "collapse" : "expand");
                }}
              >
                {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
              <button
                aria-label="Open command center"
                onClick={() => void act("dashboard")}
              >
                <PanelTop size={14} />
              </button>
              <button
                aria-label="Hide overlay"
                onClick={() => void act("hide-overlay")}
              >
                <X size={14} />
              </button>
            </div>
          </header>
          <div className="overlay-voice">
            <VoiceOrb status={status} small reduce={reduce} />
            <div>
              <ExecutionStatus status={status} />
              <p>
                {events.findLast((e) => e.type === "transcript")?.message ||
                  "Hold F8. Say what’s next."}
              </p>
            </div>
          </div>
          {expanded && (
            <div className="overlay-events">
              <ActivityTimeline events={events.slice(-4)} />
            </div>
          )}
          <PermissionIndicator status={status} events={currentEvents} />
          <footer>
            <span>
              <kbd>F8</kbd> Hold to talk
            </span>
            <button
              className="stop-small"
              disabled={!snapshot.running}
              onClick={() => void act("stop")}
            >
              <Square size={11} /> Stop
            </button>
          </footer>
        </main>
      ) : (
        <div className="app-shell">
          <aside className="sidebar">
            <div className="brand">
              <span className="brand-mark">
                <AudioLines size={24} />
              </span>
              <strong>
                Intentra<span className="os">OS</span>
              </strong>
            </div>
            <div className="workspace-label">Your personal command center</div>
            <nav aria-label="Main navigation">
              <button
                className={page === "center" ? "selected" : ""}
                onClick={() => {
                  setPage("center");
                  setSession(null);
                }}
              >
                <PanelTop size={18} />
                Command center
                <ChevronRight size={14} />
              </button>
              <button
                className={page === "history" ? "selected" : ""}
                onClick={() => setPage("history")}
              >
                <Clock3 size={18} />
                Session history
              </button>
              <button onClick={() => setSettings(true)}>
                <Settings2 size={18} />
                Settings
              </button>
            </nav>
            <button
              className="palette-trigger"
              onClick={() => setPalette(true)}
            >
              <Search size={16} />
              Quick actions<kbd>Ctrl K</kbd>
            </button>
            <div className="sidebar-bottom">
              <div className="local-note">
                <ShieldCheck size={19} />
                <div>
                  <strong>Built to stay local.</strong>
                  <span>Your voice. Your control.</span>
                </div>
              </div>
              <div className="version">
                <span>INTENTRA OS</span>
                <code>v0.1.0</code>
              </div>
            </div>
          </aside>
          <div className="main-layout">
            <header className="topbar">
              <div className="breadcrumb">
                Workspace <ChevronRight size={13} />
                <span>
                  {page === "center" ? "Command center" : "Session history"}
                </span>
              </div>
              <div className="topbar-actions">
                <span className={`connection ${snapshot.connection}`}>
                  <i />
                  {snapshot.connection === "connected"
                    ? "Agent connected"
                    : snapshot.connection === "starting"
                      ? "Starting agent"
                      : snapshot.connection === "unresponsive"
                        ? "Connection interrupted"
                        : "Agent offline"}
                </span>
                <span className="divider" />
                <button
                  className="subtle-button"
                  onClick={() => void act("overlay")}
                >
                  <Maximize2 size={15} />
                  Floating mode
                  <ArrowUpRight size={13} />
                </button>
              </div>
            </header>
            <main className="content">
              <ErrorDetails message={error} />
              {!window.intentra && (
                <div className="preview-note">
                  Browser preview · Open the desktop app to use your local voice
                  agent.
                </div>
              )}
              {snapshot.dropped > 0 && (
                <ErrorDetails
                  message={`${snapshot.dropped} events were not received. The timeline may be incomplete.`}
                />
              )}
              <div className="page-heading">
                <span className="eyebrow">
                  {page === "center"
                    ? "A more natural way to work"
                    : "Your work, in sequence"}
                </span>
                <h1>
                  {page === "center" ? (
                    <>
                      Your computer.
                      <br />
                      <span>Your voice.</span>
                    </>
                  ) : (
                    "Every command. In context."
                  )}
                </h1>
                <p>
                  {page === "center"
                    ? "One command. Any action."
                    : "A private, in-memory record of this desktop session."}
                </p>
              </div>
              {page === "center" ? (
                <>
                  <section className="command-workspace">
                    <div className="voice-stage">
                      <div className="stage-top">
                        <span className="eyebrow">Voice intelligence</span>
                        <span className="chip">
                          <i />
                          On-device
                        </span>
                      </div>
                      <VoiceOrb status={status} reduce={reduce} />
                      <div className="voice-status" aria-live="polite">
                        {snapshot.running && status === "offline" ? (
                          <span className="status">
                            Loading local voice engine
                          </span>
                        ) : (
                          <ExecutionStatus status={status} />
                        )}
                        <p>
                          {status === "emergency_stopped"
                            ? "All pending work cancelled. Restart when ready."
                            : snapshot.running && status === "offline"
                              ? "Loading your local model. This can take a moment."
                              : status === "offline"
                                ? "Connect your local agent to get started."
                                : status === "awaiting_confirmation"
                                  ? "Review the request. Keep your target app focused."
                                  : "A direct connection between thought and action."}
                        </p>
                      </div>
                      <div className="voice-control">
                        {!snapshot.running ? (
                          <button
                            className="primary-button"
                            disabled={!window.intentra}
                            onClick={() => void act("start")}
                          >
                            <Power size={16} />
                            {status === "emergency_stopped"
                              ? "Restart agent"
                              : "Start voice agent"}
                          </button>
                        ) : (
                          <span className="hold-key">
                            <kbd>F8</kbd>
                            <span>
                              Hold to talk{" "}
                              <span className="muted">· Release to send</span>
                            </span>
                          </span>
                        )}
                      </div>
                      <div className="voice-stage-bottom">
                        <span>
                          <ShieldCheck size={13} />
                          Python safety checks active in engine
                        </span>
                        <span>Local CPU</span>
                      </div>
                    </div>
                    <div className="activity-panel">
                      <header>
                        <div>
                          <span className="eyebrow">Live execution</span>
                          <h2>
                            {session
                              ? "Selected session"
                              : "From voice to action"}
                          </h2>
                        </div>
                        <span className="event-count">
                          {events
                            .filter(
                              (e) =>
                                ![
                                  "ready",
                                  "heartbeat",
                                  "configuration",
                                ].includes(e.type),
                            )
                            .length.toString()
                            .padStart(2, "0")}
                        </span>
                      </header>
                      <CommandTranscript events={events} />
                      <div className="activity-scroll">
                        <ActivityTimeline events={events} />
                      </div>
                      <button
                        className="text-button"
                        onClick={() => setPage("history")}
                      >
                        View session history
                        <ArrowUpRight size={14} />
                      </button>
                    </div>
                  </section>
                  <PermissionIndicator status={status} events={currentEvents} />
                  {status === "emergency_stopped" && (
                    <div className="emergency-banner" role="alert">
                      <ShieldCheck />
                      Emergency stop latched. The agent must exit before it can
                      be restarted.
                    </div>
                  )}
                  <section className="try-section">
                    <div className="section-heading">
                      <h2>Say it. Let Intentra handle it.</h2>
                      <span>Try a command with F8</span>
                    </div>
                    <div className="suggestions">
                      {[
                        {
                          icon: Monitor,
                          title: "Open your workspace",
                          text: "“Open Google Chrome.”",
                        },
                        {
                          icon: Keyboard,
                          title: "Make room for an idea",
                          text: "“Open Notepad.”",
                        },
                        {
                          icon: ArrowUpRight,
                          title: "Get where you’re going",
                          text: "“Open File Explorer.”",
                        },
                      ].map(({ icon: Icon, title, text }) => (
                        <div className="suggestion" key={title}>
                          <Icon size={19} />
                          <div>
                            <strong>{title}</strong>
                            <p>{text}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                </>
              ) : (
                <>
                  <div className="history-toolbar">
                    <span>
                      History clears when the desktop application exits.
                    </span>
                    <button
                      className="subtle-button"
                      onClick={() => void act("clear-history")}
                    >
                      <Trash2 size={14} />
                      Clear history
                    </button>
                  </div>
                  <SessionHistory
                    events={snapshot.events}
                    onSelect={(id) => {
                      setSession(id);
                      setPage("center");
                    }}
                  />
                </>
              )}
            </main>
            <footer className="main-footer">
              <span>
                <kbd>F8</kbd> Talk <span className="footer-dot">·</span>
                <kbd>F9</kbd> Approve <span className="footer-dot">·</span>
                <kbd>F10</kbd> Deny
              </span>
              <button
                className="emergency-control"
                disabled={!snapshot.running}
                onClick={() => void act("stop")}
              >
                <Square size={12} />
                Emergency stop <kbd>Ctrl Alt X</kbd>
              </button>
            </footer>
          </div>
        </div>
      )}
      <Dialog.Root open={settings} onOpenChange={setSettings}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-backdrop" />
          <Dialog.Content className="dialog-panel">
            <Dialog.Title>Make Intentra yours.</Dialog.Title>
            <Dialog.Description>
              Appearance preferences stay on this device. Engine controls remain
              with Python.
            </Dialog.Description>
            <Dialog.Close className="dialog-close" aria-label="Close settings">
              <X size={18} />
            </Dialog.Close>
            <div className="settings-section">
              <h3>General</h3>
              <label className="setting-row">
                <div>
                  <strong>Reduce motion</strong>
                  <p>Use a quieter, static voice visualization.</p>
                </div>
                <input
                  type="checkbox"
                  checked={reduce}
                  onChange={(e) =>
                    preference("intentra.reduce", e.target.checked, setReduce)
                  }
                />
              </label>
              <label className="setting-row">
                <div>
                  <strong>Show floating assistant when connected</strong>
                  <p>Keep status nearby without taking keyboard focus.</p>
                </div>
                <input
                  type="checkbox"
                  checked={autoOverlay}
                  onChange={(e) =>
                    preference(
                      "intentra.overlay",
                      e.target.checked,
                      setAutoOverlay,
                    )
                  }
                />
              </label>
            </div>
            <div className="settings-section">
              <h3>Voice engine</h3>
              <dl>
                <dt>Recognition</dt>
                <dd>
                  {String(snapshot.config.engine || "Connect to inspect")}
                </dd>
                <dt>Model</dt>
                <dd>
                  {String(
                    snapshot.config.model || "Unavailable until connected",
                  )}
                </dd>
                <dt>Capture mode</dt>
                <dd>{String(snapshot.config.voice_mode || "Unknown")}</dd>
                <dt>Device / threads</dt>
                <dd>
                  {snapshot.config.device
                    ? `${snapshot.config.device} / ${snapshot.config.threads}`
                    : "Unknown"}
                </dd>
              </dl>
            </div>
            <div className="settings-section">
              <h3>Keyboard controls</h3>
              <dl>
                <dt>Hold / release to talk</dt>
                <dd>
                  <kbd>F8</kbd>
                </dd>
                <dt>Approve / deny pending request</dt>
                <dd>
                  <kbd>F9</kbd> / <kbd>F10</kbd>
                </dd>
                <dt>Emergency shutdown</dt>
                <dd>
                  <kbd>Ctrl Alt X</kbd>
                </dd>
              </dl>
            </div>
            <div className="settings-section privacy">
              <h3>Privacy & security</h3>
              <p>
                Speech recognition runs locally. Optional Ollama planning
                receives your command on loopback. Visited websites have their
                own data policies.
              </p>
              <p>
                Redacted transcripts and events are kept in memory for up to
                1,500 events and disappear when this app exits. Python keeps
                rotating metadata audit logs. Redaction cannot identify every
                secret.
              </p>
              <p>
                Raw audio recording:{" "}
                <strong>
                  {snapshot.config.debug_recording === true
                    ? "Enabled in Python configuration"
                    : snapshot.config.debug_recording === false
                      ? "Disabled"
                      : "Unknown until connected"}
                </strong>
                .
              </p>
            </div>
            <div className="about-row">
              <span>
                Intentra OS <code>0.1.0</code>
              </span>
              <span>
                Agent {String(snapshot.config.agent_version || "not connected")}
              </span>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Dialog.Root open={palette} onOpenChange={setPalette}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-backdrop" />
          <Dialog.Content className="command-palette">
            <Dialog.Title>
              <Command size={18} />
              Quick actions
            </Dialog.Title>
            <Dialog.Description>
              Navigate your workspace. Voice commands use F8.
            </Dialog.Description>
            <input
              autoFocus
              aria-label="Search quick actions"
              placeholder="What would you like to do?"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div>
              {commands
                .filter((c) =>
                  c.label.toLowerCase().includes(query.toLowerCase()),
                )
                .map((c) => (
                  <button
                    key={c.label}
                    onClick={() => {
                      c.run();
                      setPalette(false);
                      setQuery("");
                    }}
                  >
                    {c.label}
                    <ArrowUpRight size={15} />
                  </button>
                ))}
            </div>
            <Dialog.Close className="palette-close">
              Escape to close
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </MotionConfig>
  );
}
