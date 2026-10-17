import type { Attachment, MessageRow } from "../api";
import type { VillageEvent } from "../events";

export type StepStatus = "pending" | "running" | "done" | "error";

export interface StepView {
  id: string;
  agent: string;
  instruction: string;
  use: string[];
  status: StepStatus;
  live?: string; // 串流中的半成品
  output?: string;
  error?: string;
  model?: string;
  startedAt?: number;
  elapsed_ms?: number;
  retries: number;
  retryReason?: string;
  warnings: string[];
  attachments?: Attachment[];
}

export type PlanSource = "mention" | "llm" | "keyword" | "direct";

export interface ReviewView {
  passed?: boolean;
  reason: string;
  skipped?: boolean;
  step?: string | null;
}

export interface RunView {
  id: string;
  message: string;
  images?: string[]; // 使用者附的圖（剛送出時是 data URL，從紀錄載入時是 /api/images/{id}）
  status: "planning" | "running" | "done" | "failed" | "cancelled";
  fromHistory?: boolean; // 從對話紀錄載入（沒有執行過程，只有結果）
  scribing?: boolean; // 書記正在記錄
  remembered?: string[]; // 書記這次記下的
  forgotten?: string[]; // 書記這次忘掉的
  reviewing?: boolean; // 審核員檢查中
  reviews: ReviewView[];
  source?: PlanSource;
  notes: string[];
  steps: StepView[];
  liveAnswer?: string; // 村長直接回覆時的串流文字
  answer?: string;
  attachments?: Attachment[]; // 最終步驟產生的圖片
  ok?: boolean;
  error?: string;
  startedAt: number;
  finishedAt?: number;
}

export type RunsState = { order: string[]; byId: Record<string, RunView> };

export const initialRuns: RunsState = { order: [], byId: {} };

type Action =
  | { type: "load"; messages: MessageRow[] } // 切換對話 / 重新整理：用存起來的訊息重建畫面
  | { type: "local"; run_id: string; message: string; images?: string[] }
  | { type: "event"; event: VillageEvent }
  | { type: "reset" };

function newRun(id: string, message: string): RunView {
  return { id, message, status: "planning", notes: [], steps: [], reviews: [], startedAt: Date.now() };
}

function patchStep(run: RunView, stepId: unknown, patch: (s: StepView) => StepView): RunView {
  return { ...run, steps: run.steps.map((s) => (s.id === stepId ? patch(s) : s)) };
}

/** 把後端事件累積成每一次問答的狀態；事件可能比 POST 回應早到，所以兩邊都能建立 run */
/** 存起來的訊息（user / assistant 成對）→ 已完成的問答；沒有執行過程，但有回答、圖片、審核結果與紀錄連結 */
export function runsFromMessages(messages: MessageRow[]): RunsState {
  const state: RunsState = { order: [], byId: {} };
  for (let i = 0; i < messages.length; i++) {
    const q = messages[i];
    if (q.role !== "user") continue;
    const a = messages[i + 1]?.role === "assistant" ? messages[++i] : undefined;
    const id = a?.run_id ?? q.id;
    const t0 = Date.parse(q.created_at);
    const run: RunView = {
      ...newRun(id, q.content),
      images: q.meta?.images?.map((i) => `/api/images/${i}`),
      startedAt: t0,
      finishedAt: a ? Date.parse(a.created_at) : t0,
      fromHistory: true,
      status: !a ? "failed" : a.status === "done" ? "done" : a.status === "cancelled" ? "cancelled" : "done",
      ok: a?.status === "done",
      answer: a?.status === "done" ? a.content : undefined,
      error: !a ? "這一輪沒有回答（可能還在處理或已中斷）" : a.status === "error" ? a.content || "執行失敗" : undefined,
      attachments: a?.meta?.attachments ?? [],
      reviews: a?.meta?.review != null ? [{ passed: a.meta.review, reason: "" }] : [],
    };
    if (a?.status === "error") run.status = "failed";
    state.order.push(id);
    state.byId[id] = run;
  }
  return state;
}

export function runsReducer(state: RunsState, action: Action): RunsState {
  if (action.type === "reset") return initialRuns;
  if (action.type === "load") return runsFromMessages(action.messages);
  if (action.type === "local") {
    const existing = state.byId[action.run_id];
    if (existing) {
      // 事件比 POST 回應早到時 run 已經建好了，補上圖片
      return action.images?.length ? { ...state, byId: { ...state.byId, [existing.id]: { ...existing, images: action.images } } } : state;
    }
    const run = { ...newRun(action.run_id, action.message), images: action.images };
    return { order: [...state.order, run.id], byId: { ...state.byId, [run.id]: run } };
  }

  const e = action.event;
  const id = e.run_id;
  if (!id) return state;
  let run = state.byId[id];
  let order = state.order;
  if (!run) {
    // 不是這個頁面發起的、沒看到開頭、或「新對話」清掉的 run 就忽略
    if (e.type !== "run.started") return state;
    run = newRun(id, String(e.message ?? ""));
    order = [...order, id];
  }

  switch (e.type) {
    case "plan.note":
      run = { ...run, notes: [...run.notes, e.note as string] };
      break;
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
    case "agent.started": // 審核員退回重做時同一步會再 started 一次
      run = patchStep(run, e.step_id, (s) => ({ ...s, status: "running", startedAt: s.startedAt ?? Date.now(), elapsed_ms: undefined }));
      break;
    case "agent.delta":
      if (e.step_id == null) run = { ...run, liveAnswer: e.text as string };
      else run = patchStep(run, e.step_id, (s) => ({ ...s, live: e.text as string }));
      break;
    case "agent.retry":
      run = patchStep(run, e.step_id, (s) => ({ ...s, live: "", retries: s.retries + 1, retryReason: e.reason as string }));
      break;
    case "agent.finished":
      if (e.step_id == null) break; // 村長直接回覆，結果在 run.finished
      run = patchStep(run, e.step_id, (s) => ({
        ...s,
        status: "done",
        live: undefined,
        output: e.output as string,
        model: e.model as string,
        elapsed_ms: e.elapsed_ms as number,
        warnings: (e.warnings as string[]) ?? [],
        attachments: (e.attachments as Attachment[]) ?? [],
      }));
      break;
    case "agent.error":
      run = patchStep(run, e.step_id, (s) => ({ ...s, status: "error", live: undefined, error: e.error as string }));
      break;
    case "run.finished":
      run = {
        ...run,
        status: "done",
        reviewing: false,
        ok: e.ok as boolean,
        answer: e.answer as string,
        attachments: (e.attachments as Attachment[]) ?? [],
        finishedAt: Date.now(),
      };
      break;
    case "run.failed":
      run = { ...run, status: "failed", ok: false, error: e.error as string, finishedAt: Date.now() };
      break;
    case "run.cancelled":
      run = {
        ...run,
        status: "cancelled",
        ok: false,
        reviewing: false,
        finishedAt: Date.now(),
        steps: run.steps.map((s) => (s.status === "running" || s.status === "pending" ? { ...s, status: "error", live: undefined, error: "已取消" } : s)),
      };
      break;
    case "memory.started":
      run = { ...run, scribing: true };
      break;
    case "memory.finished":
      run = { ...run, scribing: false, remembered: (e.added as string[]) ?? [], forgotten: (e.forgot as string[]) ?? [] };
      break;
    case "review.started":
      run = { ...run, reviewing: true };
      break;
    case "review.finished":
      run = {
        ...run,
        reviewing: false,
        reviews: [...run.reviews, { passed: e.passed as boolean | undefined, reason: e.reason as string, skipped: !!e.skipped, step: e.step as string | null }],
      };
      break;
    default:
      if (run === state.byId[id]) return state;
  }
  return { order, byId: { ...state.byId, [id]: run } };
}

/** 最後交給使用者的步驟（沒有其他步驟用到它的成果）；平行時可能有好幾個 */
export function leafIds(run: RunView): Set<string> {
  const used = new Set(run.steps.flatMap((s) => s.use));
  return new Set(run.steps.filter((s) => !used.has(s.id)).map((s) => s.id));
}
