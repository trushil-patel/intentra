import { z } from "zod";
export const statuses = [
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
] as const;
export type AgentStatus = (typeof statuses)[number];
const scalar = z.union([
  z.string().max(4000),
  z.number().finite(),
  z.boolean(),
  z.null(),
]);
export const eventSchema = z
  .object({
    id: z.string(),
    sessionId: z.string().regex(/^[a-f0-9]{32}$/),
    runId: z.string().regex(/^[a-f0-9]{32}$/),
    sequence: z.number().int().positive(),
    timestamp: z.string().refine((v) => Number.isFinite(Date.parse(v))),
    status: z.enum(statuses),
    type: z.string().regex(/^[a-z_]{1,40}$/),
    message: z.string().max(4000),
    payload: z.record(z.string(), scalar),
  })
  .strict();
export type AgentEvent = z.infer<typeof eventSchema>;
export const snapshotSchema = z
  .object({
    connection: z.enum(["offline", "starting", "connected", "unresponsive"]),
    running: z.boolean(),
    events: z.array(eventSchema).max(1500),
    current: eventSchema.nullable(),
    config: z.record(z.string(), scalar),
    error: z.string(),
    dropped: z.number().nonnegative(),
  })
  .strict();
export type Snapshot = z.infer<typeof snapshotSchema>;
export type Action =
  | "start"
  | "stop"
  | "overlay"
  | "hide-overlay"
  | "expand"
  | "collapse"
  | "dashboard"
  | "clear-history"
  | "quit";
export interface DesktopAPI {
  snapshot: () => Promise<unknown>;
  action: (action: Action) => Promise<unknown>;
  subscribe: (callback: (value: unknown) => void) => () => void;
}
declare global {
  interface Window {
    intentra?: DesktopAPI;
  }
}
export const labels: Record<AgentStatus, string> = {
  offline: "Agent offline",
  idle: "Ready when you are",
  listening: "Listening",
  transcribing: "Transcribing locally",
  understanding: "Understanding your intent",
  planning: "Building the plan",
  awaiting_confirmation: "Your approval is needed",
  executing: "Working on it",
  completed: "Task verified",
  failed: "Attention needed",
  cancelled: "Command cancelled",
  emergency_stopped: "Emergency stopped",
};
