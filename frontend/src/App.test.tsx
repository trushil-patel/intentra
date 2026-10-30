import { render, screen, fireEvent, act } from "@testing-library/react";
import { beforeEach, describe, it, expect, vi } from "vitest";
import App from "./App";
import {
  ActivityTimeline,
  PermissionIndicator,
  CommandTranscript,
  ExecutionStatus,
} from "./Activity";
import { VoiceOrb } from "./VoiceOrb";
import { connectAgent, emptySnapshot, useAgent } from "./store";
import { eventSchema, type AgentEvent } from "./contracts";
const event = (overrides: Partial<AgentEvent> = {}): AgentEvent => ({
  id: "a".repeat(32) + ":1",
  runId: "a".repeat(32),
  sessionId: "b".repeat(32),
  sequence: 1,
  timestamp: new Date().toISOString(),
  status: "idle",
  type: "ready",
  message: "Ready",
  payload: {},
  ...overrides,
});
beforeEach(() => {
  useAgent.setState({ snapshot: emptySnapshot, uiError: "" });
  delete window.intentra;
  localStorage.clear();
});
describe("real event UI", () => {
  it("renders offline with no fabricated timeline", () => {
    render(<App />);
    expect(
      screen.getByRole("heading", { name: /Your computer/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Start voice agent" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Waiting for your first command"),
    ).toBeInTheDocument();
  });
  it("displays transcript as text, never HTML", () => {
    render(
      <CommandTranscript
        events={[
          event({
            type: "transcript",
            message: "<img src=x onerror=alert(1)>",
          }),
        ]}
      />,
    );
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
  it("distinguishes uncertainty from verification", () => {
    const { container } = render(
      <ActivityTimeline
        events={[
          event({
            type: "verification",
            message: "open_app: uncertain",
            payload: { verification: "UNCERTAIN" },
          }),
        ]}
      />,
    );
    expect(container.querySelector(".failure")).toBeTruthy();
    expect(container.querySelector(".verified")).toBeNull();
  });
  it("does not turn a started action into a completed step", () => {
    const { container } = render(
      <ActivityTimeline
        events={[
          event({
            type: "action_started",
            status: "executing",
            message: "open_app",
          }),
        ]}
      />,
    );
    expect(container.querySelector(".verified")).toBeNull();
  });
  it("shows F9/F10 without approval controls", () => {
    render(
      <PermissionIndicator
        status="awaiting_confirmation"
        events={[
          event({
            type: "confirmation",
            message: "Review typing",
            status: "awaiting_confirmation",
          }),
        ]}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Review typing");
    expect(screen.getByText("F9")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("shows the emergency state distinctly", () => {
    render(<ExecutionStatus status="emergency_stopped" />);
    expect(screen.getByText("Emergency stopped")).toHaveClass(
      "status-emergency_stopped",
    );
  });
  it("honors explicit reduced motion", () => {
    render(<VoiceOrb status="listening" reduce />);
    expect(screen.getByRole("img")).toHaveAttribute("data-motion", "off");
  });
  it("does not animate emergency stop", () => {
    render(<VoiceOrb status="emergency_stopped" />);
    expect(screen.getByRole("img")).toHaveAttribute("data-motion", "off");
  });
  it("shows bridge errors", () => {
    useAgent.setState({ uiError: "Connection lost" });
    render(<App />);
    expect(screen.getByRole("alert")).toHaveTextContent("Connection lost");
  });
  it("opens accessible settings and persists a real preference", () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Reduce motion/ }));
    expect(localStorage.getItem("intentra.reduce")).toBe("true");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
describe("bridge subscription", () => {
  it("rejects malformed payloads", () => {
    expect(
      eventSchema.safeParse({ ...event(), status: "fake-success" }).success,
    ).toBe(false);
    useAgent.getState().apply({});
    expect(useAgent.getState().snapshot).toEqual(emptySnapshot);
    expect(useAgent.getState().uiError).toMatch(/Invalid/);
  });
  it("cleans listeners and ignores a late snapshot after unmount", async () => {
    let resolve!: (v: unknown) => void;
    const unsubscribe = vi.fn();
    window.intentra = {
      subscribe: () => unsubscribe,
      snapshot: () => new Promise((r) => (resolve = r)),
      action: vi.fn(),
    };
    const close = connectAgent();
    close();
    resolve({ ...emptySnapshot, running: true });
    await Promise.resolve();
    expect(unsubscribe).toHaveBeenCalledOnce();
    expect(useAgent.getState().snapshot.running).toBe(false);
  });
  it("does not overwrite a live event with an older initial snapshot", async () => {
    let resolve!: (v: unknown) => void;
    let listener!: (v: unknown) => void;
    window.intentra = {
      subscribe: (cb) => {
        listener = cb;
        return () => {};
      },
      snapshot: () => new Promise((r) => (resolve = r)),
      action: vi.fn(),
    };
    const close = connectAgent();
    listener({ ...emptySnapshot, running: true, current: event() });
    resolve(emptySnapshot);
    await Promise.resolve();
    expect(useAgent.getState().snapshot.running).toBe(true);
    close();
  });
  it("updates the UI when a real snapshot arrives", () => {
    render(<App />);
    act(() =>
      useAgent.getState().apply({
        ...emptySnapshot,
        connection: "connected",
        running: true,
        current: event({ status: "transcribing" }),
      }),
    );
    expect(screen.getByText("Transcribing locally")).toBeInTheDocument();
  });
});
