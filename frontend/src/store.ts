import { create } from "zustand";
import { snapshotSchema, type Snapshot, type Action } from "./contracts";
export const emptySnapshot: Snapshot = {
  connection: "offline",
  running: false,
  events: [],
  current: null,
  config: {},
  error: "",
  dropped: 0,
};
interface State {
  snapshot: Snapshot;
  uiError: string;
  apply: (value: unknown) => void;
  act: (action: Action) => Promise<void>;
}
export const useAgent = create<State>((set) => ({
  snapshot: emptySnapshot,
  uiError: "",
  apply: (value) => {
    const result = snapshotSchema.safeParse(value);
    if (result.success) set({ snapshot: result.data });
    else set({ uiError: "Invalid desktop message rejected." });
  },
  act: async (action) => {
    try {
      if (!window.intentra)
        throw new Error(
          "Open the desktop application to connect the local agent.",
        );
      await window.intentra.action(action);
      set({ uiError: "" });
    } catch (e) {
      set({
        uiError: e instanceof Error ? e.message : "Desktop operation failed",
      });
    }
  },
}));
export function connectAgent() {
  let alive = true;
  const apply = (v: unknown) => {
    if (alive) useAgent.getState().apply(v);
  };
  if (!window.intentra)
    return () => {
      alive = false;
    };
  let updates = 0;
  const unsubscribe = window.intentra.subscribe((v) => {
    updates++;
    apply(v);
  });
  const initial = updates;
  void window.intentra
    .snapshot()
    .then((v) => {
      if (updates === initial) apply(v);
    })
    .catch(() => {
      if (alive)
        useAgent.setState({
          uiError: "Could not connect to the desktop bridge.",
        });
    });
  return () => {
    alive = false;
    unsubscribe();
  };
}
