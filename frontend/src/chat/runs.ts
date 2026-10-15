import type { VillageEvent } from "../events";

export type StepStatus = "pending" | "running" | "done" | "error";

export interface StepView {
  id: string;
  agent: string;
  instruction: string;
  use: string[];
  status: StepStatus;
  output?: string;
  error?: string;
  model?: string;
  elapsed_ms?: number;
  retries: number;
  warnings: string[];
}

export type PlanSource = "mention" | "llm" | "keyword" | "direct";

export interface RunView {
  id: string;
  message: string;
  status: "planning" | "running" | "done" | "failed";
  source?: PlanSource;
  notes: string[];
  steps: StepView[];
  answer?: string;
  ok?: boolean;
  error?: string;
  startedAt: number;
}

export type RunsState = { order: string[]; byId: Record<string, RunView> };

export const initialRuns: RunsState = { order: [], byId: {} };

type Action = { type: "local"; run_id: string; message: string } | { type: "event"; event: VillageEvent };

function patchStep(run: RunView, stepId: unknown, patch: (s: StepView) => StepView): RunView {
  return { ...run, steps: run.steps.map((s) => (s.id === stepId ? patch(s) : s)) };
}

/** 把後端事件累積成每一次問答的狀態；事件可能比 POST 回應早到，所以兩邊都能建立 run */
export function runsReducer(state: RunsState, action: Action): RunsState {
  if (action.type === "local") {
    if (state.byId[action.run_id]) return state;
    const run: RunView = { id: action.run_id, message: action.message, status: "planning", notes: [], steps: [], startedAt: Date.now() };
    return { order: [...state.order, run.id], byId: { ...state.byId, [run.id]: run } };
  }

  const e = action.event;
  const id = e.run_id;
  if (!id) return state;
  let run = state.byId[id];
  let order = state.order;
  if (!run) {
    if (e.type !== "run.started") return state; // 不是這個頁面發起的、也沒看到開頭的 run 就忽略
    run = { id, message: String(e.message ?? ""), status: "planning", notes: [], steps: [], startedAt: Date.now() };
    order = [...order, id];
  }

  switch (e.type) {
    case "plan.created": {
      const steps = (e.steps as { id: string; agent: string; instruction: string; use: string[] }[]) ?? [];
      run = {
        ...run,
        status: "running",
        source: e.source as PlanSource,
        notes: (e.notes as string[]) ?? [],
        steps: steps.map((s) => ({ ...s, status: "pending", retries: 0, warnings: [] })),
      };
      break;
    }
    case "agent.started":
      run = patchStep(run, e.step_id, (s) => ({ ...s, status: "running" }));
      break;
    case "agent.retry":
      run = patchStep(run, e.step_id, (s) => ({ ...s, retries: s.retries + 1 }));
      break;
    case "agent.finished":
      run = patchStep(run, e.step_id, (s) => ({
        ...s,
        status: "done",
        output: e.output as string,
        model: e.model as string,
        elapsed_ms: e.elapsed_ms as number,
        warnings: (e.warnings as string[]) ?? [],
      }));
      break;
    case "agent.error":
      run = patchStep(run, e.step_id, (s) => ({ ...s, status: "error", error: e.error as string }));
      break;
    case "run.finished":
      run = { ...run, status: "done", ok: e.ok as boolean, answer: e.answer as string };
      break;
    case "run.failed":
      run = { ...run, status: "failed", ok: false, error: e.error as string };
      break;
    default:
      if (run === state.byId[id]) return state;
  }
  return { order, byId: { ...state.byId, [id]: run } };
}
