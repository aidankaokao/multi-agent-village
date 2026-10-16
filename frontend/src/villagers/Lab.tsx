import { useState } from "react";
import { villagers, type AgentSpec, type AgentTest, type Expect, type Model, type TryResult } from "../api";
import { Field, ListInput, NumInput } from "./fields";

export const EMPTY_EXPECT: Expect = { contains: [], not_contains: [], lang: null, min_chars: null, max_chars: null, regex: null };

/** 模型選單：依村民設定 / 分級 / 指定模型（比較雲端與本地用） */
export function TargetSelect({
  value,
  onChange,
  models,
  defaultLabel = "依村民設定",
}: {
  value: string;
  onChange: (v: string) => void;
  models: Model[];
  defaultLabel?: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{defaultLabel}</option>
      <option value="tier:large">large 分級</option>
      <option value="tier:small">small 分級</option>
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.provider_name} / {m.display_name || m.model}
        </option>
      ))}
    </select>
  );
}

export function ResultView({ r }: { r: TryResult }) {
  return (
    <div className="try-result">
      <div className="row">
        <span>{r.ok ? "✅" : "❌"}</span>
        {r.model && <span className="tag">{r.model}</span>}
        <span className="muted">{(r.elapsed_ms / 1000).toFixed(1)}s</span>
        {r.attempts > 1 && <span className="tag warn">重做 {r.attempts - 1} 次</span>}
      </div>
      {r.retry_reasons.map((x, i) => (
        <div key={i} className="muted">
          ↻ {x}
        </div>
      ))}
      {r.warnings.map((w) => (
        <div key={w} className="tag warn">
          {w}
        </div>
      ))}
      {r.failures.map((f) => (
        <div key={f} className="step-error">
          ✗ {f}
        </div>
      ))}
      {r.error && <div className="step-error">{r.error}</div>}
      {r.output && <pre className="output">{r.output}</pre>}
    </div>
  );
}

// ---------------------------------------------------------------- 試打

export function TryPanel({ spec, models, onAddTest }: { spec: AgentSpec; models: Model[]; onAddTest: (t: AgentTest) => void }) {
  const [instruction, setInstruction] = useState("");
  const [context, setContext] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TryResult | null>(null);
  const [error, setError] = useState("");

  const go = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(await villagers.try({ spec, instruction, context, target: target || null }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lab">
      <p className="hint">用「目前編輯中」的設定直接跑一次（不用先儲存），流程和問答時一樣：LLM → 自動檢查 → 不合格重做。</p>
      <Field label="任務（模擬村長給的指示）">
        <textarea rows={3} value={instruction} placeholder="把「今天天氣很好，我們去散步吧」翻成英文" onChange={(e) => setInstruction(e.target.value)} />
      </Field>
      <Field label="前一步的成果（選填）" hint="模擬接力時收到的內容，例如詩人寫的詩">
        <textarea rows={2} value={context} onChange={(e) => setContext(e.target.value)} />
      </Field>
      <div className="row">
        <TargetSelect value={target} onChange={setTarget} models={models} />
        <button onClick={go} disabled={busy || !instruction.trim()}>
          {busy ? "執行中…" : "▶ 執行"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <>
          <ResultView r={result} />
          <button className="link" onClick={() => onAddTest({ input: instruction, context, expect: { ...EMPTY_EXPECT } })}>
            ＋ 把這個任務加入測試題
          </button>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 測試題

interface RunSummary {
  label: string;
  passed: number;
  total: number;
  ms: number;
}

export function TestsPanel({ spec, models, update }: { spec: AgentSpec; models: Model[]; update: (fn: (d: AgentSpec) => void) => void }) {
  const [target, setTarget] = useState("");
  const [results, setResults] = useState<(TryResult | "running" | undefined)[]>([]);
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const tests = spec.tests;

  const runOne = async (i: number): Promise<TryResult | null> => {
    setResults((r) => Object.assign([...r], { [i]: "running" }));
    try {
      const t = tests[i];
      const r = await villagers.try({ spec, instruction: t.input, context: t.context, target: target || null, expect: t.expect });
      setResults((rs) => Object.assign([...rs], { [i]: r }));
      return r;
    } catch (e) {
      setError((e as Error).message);
      setResults((rs) => Object.assign([...rs], { [i]: undefined }));
      return null;
    }
  };

  const runAll = async () => {
    setRunning(true);
    setError("");
    setResults([]);
    const t0 = Date.now();
    let passed = 0;
    for (let i = 0; i < tests.length; i++) {
      const r = await runOne(i); // 依序執行：本地模型通常一次只能跑一個
      if (r?.ok) passed++;
    }
    const label = target ? (models.find((m) => m.id === target)?.model ?? target) : "依村民設定";
    setHistory((h) => [{ label, passed, total: tests.length, ms: Date.now() - t0 }, ...h].slice(0, 8));
    setRunning(false);
  };

  const remove = (i: number) => {
    update((d) => void d.tests.splice(i, 1));
    setResults((r) => r.filter((_, j) => j !== i));
  };

  return (
    <div className="lab">
      <p className="hint">
        寫幾組任務與「結果應該符合的條件」，一次跑完看通過率。改 prompt 或換模型後再跑一次比較。測試題會跟著村民一起儲存。
      </p>
      <div className="row">
        <TargetSelect value={target} onChange={setTarget} models={models} />
        <button onClick={runAll} disabled={running || tests.length === 0}>
          {running ? "測試中…" : `▶ 全部執行（${tests.length} 題）`}
        </button>
      </div>
      {error && (
        <p className="error" onClick={() => setError("")}>
          {error}
        </p>
      )}
      {history.length > 0 && (
        <div className="card">
          <strong>測試紀錄</strong>
          {history.map((h, i) => (
            <div key={i} className="row">
              <span className={h.passed === h.total ? "tag ok" : "tag warn"}>
                {h.passed}/{h.total}
              </span>
              <span>{h.label}</span>
              <span className="muted">{(h.ms / 1000).toFixed(1)}s</span>
            </div>
          ))}
        </div>
      )}

      {tests.map((t, i) => (
        <TestCard key={i} index={i} test={t} result={results[i]} update={update} onRun={() => runOne(i)} onRemove={() => remove(i)} disabled={running} />
      ))}
      <button className="link" onClick={() => update((d) => void d.tests.push({ input: "", context: "", expect: { ...EMPTY_EXPECT } }))}>
        ＋ 新增測試題
      </button>
    </div>
  );
}

function TestCard({
  index: i,
  test: t,
  result,
  update,
  onRun,
  onRemove,
  disabled,
}: {
  index: number;
  test: AgentTest;
  result: TryResult | "running" | undefined;
  update: (fn: (d: AgentSpec) => void) => void;
  onRun: () => void;
  onRemove: () => void;
  disabled: boolean;
}) {
  const [showOutput, setShowOutput] = useState(false);
  const set = (fn: (x: AgentTest) => void) => update((d) => fn(d.tests[i]));
  const setE = (fn: (x: Expect) => void) => update((d) => fn(d.tests[i].expect));
  const status = result === "running" ? "⏳" : result ? (result.ok ? "✅" : "❌") : "・";

  return (
    <div className="card">
      <div className="row">
        <strong>
          {status} 第 {i + 1} 題
        </strong>
        {result && result !== "running" && <span className="muted">{(result.elapsed_ms / 1000).toFixed(1)}s</span>}
        <span style={{ flex: 1 }} />
        <button className="link" onClick={onRun} disabled={disabled || !t.input.trim() || result === "running"}>
          執行
        </button>
        <button className="link danger" onClick={onRemove}>
          刪除
        </button>
      </div>
      <Field label="任務">
        <textarea rows={2} value={t.input} onChange={(e) => set((x) => void (x.input = e.target.value))} />
      </Field>
      <Field label="前一步的成果（選填）">
        <input value={t.context} onChange={(e) => set((x) => void (x.context = e.target.value))} />
      </Field>
      <div className="grid2">
        <Field label="應包含">
          <ListInput value={t.expect.contains} placeholder="cat、weather" onChange={(v) => setE((x) => void (x.contains = v))} />
        </Field>
        <Field label="不應包含">
          <ListInput value={t.expect.not_contains} placeholder="翻譯如下、Here is" onChange={(v) => setE((x) => void (x.not_contains = v))} />
        </Field>
        <Field label="語言">
          <select value={t.expect.lang ?? ""} onChange={(e) => setE((x) => void (x.lang = (e.target.value || null) as Expect["lang"]))}>
            <option value="">不檢查</option>
            <option value="en">英文（不能有中文字）</option>
            <option value="zh">中文（要有中文字）</option>
          </select>
        </Field>
        <Field label="字數範圍">
          <div className="row">
            <NumInput value={t.expect.min_chars} min={0} placeholder="最少" onChange={(v) => setE((x) => void (x.min_chars = v))} />
            <NumInput value={t.expect.max_chars} min={0} placeholder="最多" onChange={(v) => setE((x) => void (x.max_chars = v))} />
          </div>
        </Field>
      </div>
      {result && result !== "running" && (
        <>
          {result.failures.map((f) => (
            <div key={f} className="step-error">
              ✗ {f}
            </div>
          ))}
          {result.error && <div className="step-error">{result.error}</div>}
          <button className="link" onClick={() => setShowOutput(!showOutput)}>
            {showOutput ? "▾ 隱藏輸出" : "▸ 看輸出"}
          </button>
          {showOutput && <ResultView r={result} />}
        </>
      )}
    </div>
  );
}
