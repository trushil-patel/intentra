import {
  Check,
  ArrowUpRight,
  AudioLines,
  ShieldCheck,
  AlertCircle,
  Clock3,
  ChevronRight,
} from "lucide-react";
import type { AgentEvent, AgentStatus } from "./contracts";
import { labels } from "./contracts";
export function ExecutionStatus({ status }: { status: AgentStatus }) {
  return (
    <span className={`status status-${status}`}>
      <i />
      {labels[status]}
    </span>
  );
}
export function CommandTranscript({ events }: { events: AgentEvent[] }) {
  const transcript = events.findLast((e) => e.type === "transcript");
  return (
    <div className="transcript">
      <span className="eyebrow">Latest command</span>
      <p>
        {transcript
          ? `“${transcript.message}”`
          : "Your next idea starts with your voice."}
      </p>
      {!transcript && (
        <span className="muted">Hold F8, speak naturally, then release.</span>
      )}
    </div>
  );
}
export function ExecutionStep({ event }: { event: AgentEvent }) {
  const verified =
    (event.type === "verification" &&
      event.payload.verification === "VERIFIED") ||
    event.type === "completed";
  const failed =
    [
      "error",
      "blocked",
      "uncertain",
      "cancelled",
      "emergency",
      "startup_error",
    ].includes(event.type) ||
    (event.type === "verification" &&
      event.payload.verification !== "VERIFIED");
  const Icon = verified
    ? Check
    : failed
      ? AlertCircle
      : event.type === "confirmation"
        ? ShieldCheck
        : event.type === "capture"
          ? AudioLines
          : ArrowUpRight;
  return (
    <li
      className={`execution-step ${verified ? "verified" : failed ? "failure" : ""}`}
    >
      <span className="step-icon">
        <Icon size={14} />
      </span>
      <div>
        <span className="step-kind">{event.type.replaceAll("_", " ")}</span>
        <p>{event.message || labels[event.status]}</p>
        {typeof event.payload.tool === "string" && (
          <code>{event.payload.tool}</code>
        )}
      </div>
      <time dateTime={event.timestamp}>
        {new Date(event.timestamp).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}
      </time>
    </li>
  );
}
export function ActivityTimeline({ events }: { events: AgentEvent[] }) {
  const shown = events.filter(
    (e) =>
      !["heartbeat", "configuration", "ready", "transcript"].includes(e.type),
  );
  return shown.length ? (
    <ol className="timeline">
      {shown.map((e) => (
        <ExecutionStep key={e.id} event={e} />
      ))}
    </ol>
  ) : (
    <div className="empty-state">
      <span className="empty-icon">
        <Clock3 size={22} />
      </span>
      <h3>
        A little less clicking.
        <br />A lot more doing.
      </h3>
      <p>
        Your command’s progress will appear here,
        <br />
        as it happens on your computer.
      </p>
      <span className="live-label">
        <i />
        Waiting for your first command
      </span>
    </div>
  );
}
export function PermissionIndicator({
  events,
  status,
}: {
  events: AgentEvent[];
  status: AgentStatus;
}) {
  const request = events.findLast((e) => e.type === "confirmation");
  if (status !== "awaiting_confirmation") return null;
  return (
    <section className="permission" role="alert">
      <ShieldCheck size={22} />
      <div>
        <strong>
          Review before continuing
          {request?.payload.risk ? ` · ${request.payload.risk} risk` : ""}
        </strong>
        <p>
          {request?.message ||
            "The agent is waiting for approval in the existing confirmation system."}
        </p>
        <span>
          Keep the target app focused. <kbd>F9</kbd> Approve <kbd>F10</kbd> Deny
        </span>
      </div>
    </section>
  );
}
export function ErrorDetails({ message }: { message: string }) {
  return message ? (
    <div className="error-banner" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
    </div>
  ) : null;
}
export function SessionHistory({
  events,
  onSelect,
}: {
  events: AgentEvent[];
  onSelect: (id: string) => void;
}) {
  const groups = Map.groupBy(
    events.filter(
      (e) => !["configuration", "heartbeat", "ready"].includes(e.type),
    ),
    (e) => e.sessionId,
  );
  return (
    <div className="session-list">
      {groups.size === 0 ? (
        <div className="empty-state">
          <Clock3 />
          <h3>A fresh start.</h3>
          <p>Your real command sessions will appear here.</p>
        </div>
      ) : (
        [...groups].reverse().map(([id, items]) => {
          const last = items.at(-1)!;
          return (
            <button key={id} className="session" onClick={() => onSelect(id)}>
              <AudioLines />
              <div>
                <strong>
                  {items.find((e) => e.type === "transcript")?.message ||
                    "Voice session"}
                </strong>
                <span>
                  {new Date(last.timestamp).toLocaleString()} · {items.length}{" "}
                  events
                </span>
              </div>
              <ExecutionStatus status={last.status} />
              <ChevronRight size={16} />
            </button>
          );
        })
      )}
    </div>
  );
}
