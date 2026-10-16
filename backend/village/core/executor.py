"""執行一次問答：村長規劃 → 執行步驟（互不依賴的可平行）→ harness 檢查 → 審核員 → 發事件、寫執行紀錄。

每個動作都發事件到 EventBus，前端的村落動畫與聊天框完全由事件驅動。
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field

from village.core.card import AgentSpec
from village.core.harness import check_output, mask_profanity, normalize, preview, profanity_hits
from village.core.orchestrator import ChatLLM, Plan, PlanError, Step, _extract_json, direct_reply, make_plan
from village.core.registry import AgentRegistry
from village.core.reviewer import REVIEWER_ID, Leaf, Review, review
from village.events import EventBus
from village.llm.base import ChatMessage, LLMError, TextCallback
from village.llm.service import llm_user
from village.store.runs import TraceStore
from village.store.settings import RulesStore, VillageRules
from village.tools import TOOLS, ToolError, ToolResult

log = logging.getLogger("village.executor")

MAYOR = "mayor"
STEP_RETRIES = 1  # 輸出不合格時帶回饋重做的次數
DELTA_INTERVAL = 0.08  # 串流事件最短間隔（秒），避免每個 token 都推一次


class DeltaEmitter:
    """把 LLM 串流節流成 agent.delta 事件。

    事件帶的是「目前為止的完整文字」（已清 <think>、遮蔽髒話），前端直接取代顯示，
    掉幾個事件或換備援模型重來都不會錯亂。最後的完整結果由 agent.finished / run.finished 提供。
    """

    def __init__(self, bus: EventBus, **ids):
        self.bus, self.ids = bus, ids
        self._last = 0.0
        self._sent = ""

    async def __call__(self, text: str) -> None:
        now = time.monotonic()
        if now - self._last < DELTA_INTERVAL:
            return
        shown = preview(text)
        if shown == self._sent:
            return
        self._last, self._sent = now, shown
        await self.bus.publish("agent.delta", **self.ids, text=shown)


@dataclass
class StepResult:
    id: str
    agent: str
    instruction: str
    use: list[str]
    status: str = "pending"  # pending / done / error
    output: str = ""
    error: str | None = None
    model: str | None = None
    elapsed_ms: int = 0
    attempts: int = 0
    warnings: list[str] = field(default_factory=list)
    input: str = ""  # 實際送給村民 LLM 的任務訊息（含前一步成果），執行紀錄用
    retry_reasons: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    rejected: list[dict] = field(default_factory=list)  # 被審核員退回的舊版本 {output, reason}
    attachments: list[dict] = field(default_factory=list)  # 工具產生的圖片等（{kind, name, data, title}）
    tool_args: dict | None = None  # 工具型村民：LLM（或退路）整理出的參數


def render_context(step: Step, results: dict[str, StepResult], registry: AgentRegistry) -> str:
    parts = []
    for ref in step.use:
        r = results.get(ref)
        if r and r.output:
            spec = registry.get(r.agent)
            parts.append(f"參考資料（來自{spec.name if spec else r.agent}）：\n{r.output}")
    return "\n\n".join(parts)


def render_user(template: str, instruction: str, context: str) -> str:
    return template.replace("{instruction}", instruction).replace("{context}", context).strip()


def leaf_steps(plan: Plan) -> list[Step]:
    """最後交給使用者的步驟：沒有其他步驟用到它的成果（例如「同時翻成英文和日文」就有兩個）。"""
    used = {u for s in plan.steps for u in s.use}
    return [s for s in plan.steps if s.id not in used]


def compose_answer(plan: Plan, results: dict[str, StepResult], registry: AgentRegistry) -> tuple[str, bool]:
    ok = all(r.status == "done" for r in results.values())
    leaves = [results[s.id] for s in leaf_steps(plan)]
    done = [r for r in leaves if r.status == "done"]
    if len(leaves) == 1:
        return (done[0].output if done else ""), ok
    blocks = []
    for r in done:
        spec = registry.get(r.agent)
        blocks.append(f"【{spec.name if spec else r.agent}】{r.instruction}\n{r.output}")
    return "\n\n".join(blocks), ok


DEFAULT_RULES = VillageRules(reviewer_enabled=False, parallel=False)  # 沒接 DB 時（測試）維持最單純的行為


class Runner:
    def __init__(
        self,
        registry: AgentRegistry,
        llm: ChatLLM,
        bus: EventBus,
        handoff_delay: float = 1.0,
        rules: RulesStore | None = None,
        trace: TraceStore | None = None,
    ):
        self.registry = registry
        self.llm = llm
        self.bus = bus
        self.handoff_delay = handoff_delay  # 讓前端有時間播放「走過去交接」的動畫
        self.rules = rules
        self.trace = trace

    async def run(self, run_id: str, message: str, traced: bool = False, user_id: str | None = None) -> dict:
        """traced=True 代表呼叫端已經建立了執行紀錄（POST /api/chat）；user_id：提問者，事件內容只送給他。"""
        self.bus.claim(run_id, user_id)
        try:
            with llm_user(user_id):  # 這次問答的 LLM 呼叫都用提問者自己的設定與額度
                return await self._run(run_id, message, traced, user_id)
        finally:
            self.bus.release(run_id)

    async def _run(self, run_id: str, message: str, traced: bool, user_id: str | None) -> dict:
        emit = self.bus.publish
        rules = self.rules.current if self.rules else DEFAULT_RULES
        agents = self.registry.all()
        t0 = time.monotonic()
        plan: Plan | None = None
        results: dict[str, StepResult] = {}
        reviews: list[Review] = []
        if self.trace:
            self.trace.keep = rules.keep_runs
            if not traced:
                await self.trace.start(run_id, message, user_id)

        async def record(status: str, **fields) -> None:
            if self.trace:
                await self.trace.finish(
                    run_id, status=status, plan=plan.to_dict() if plan else None,
                    steps=[asdict(r) for r in results.values()], reviews=[rv.to_dict() for rv in reviews],
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                    extra_tokens_in=sum(rv.tokens_in for rv in reviews),
                    extra_tokens_out=sum(rv.tokens_out for rv in reviews),
                    **fields,
                )

        await emit("run.started", run_id=run_id, agent=MAYOR, message=message)
        try:
            async def on_note(note: str) -> None:
                await emit("plan.note", run_id=run_id, agent=MAYOR, note=note)

            plan = await make_plan(message, agents, self.llm, on_note=on_note)
            await emit("plan.created", run_id=run_id, agent=MAYOR, **plan.to_dict())

            if not plan.steps:
                await emit("agent.started", run_id=run_id, step_id=None, agent=MAYOR, instruction=message)
                on_text = DeltaEmitter(self.bus, run_id=run_id, step_id=None, agent=MAYOR)
                answer = await direct_reply(message, agents, self.llm, on_text=on_text)
                await emit("agent.finished", run_id=run_id, step_id=None, agent=MAYOR, output=answer)
                await emit("run.finished", run_id=run_id, ok=True, answer=answer, steps=[], reviews=[])
                await record("done", answer=answer)
                return {"ok": True, "answer": answer, "plan": plan.to_dict(), "steps": [], "reviews": []}

            results = {s.id: StepResult(id=s.id, agent=s.agent, instruction=s.instruction, use=s.use) for s in plan.steps}
            await self._execute(run_id, plan, results, rules)
            answer, ok = compose_answer(plan, results, self.registry)
            if ok and rules.reviewer_enabled:
                await self._review_loop(run_id, message, plan, results, reviews, rules)
                answer, ok = compose_answer(plan, results, self.registry)

            steps = [asdict(r) for r in results.values()]
            rv = [x.to_dict() for x in reviews]
            files = [a for s in leaf_steps(plan) if results[s.id].status == "done" for a in results[s.id].attachments]
            await emit("run.finished", run_id=run_id, ok=ok, answer=answer, steps=steps, reviews=rv, attachments=files)
            await record("done" if ok else "error", answer=answer)
            return {"ok": ok, "answer": answer, "plan": plan.to_dict(), "steps": steps, "reviews": rv, "attachments": files}
        except asyncio.CancelledError:
            # 使用者按「停止」：通知前端讓村民收工，紀錄標成 cancelled，再把取消往上傳
            await emit("run.cancelled", run_id=run_id, agent=MAYOR)
            await record("cancelled", error="使用者取消")
            raise
        except Exception as e:
            log.exception("run %s failed", run_id)
            await emit("run.failed", run_id=run_id, agent=MAYOR, error=str(e))
            await record("error", error=str(e))
            return {"ok": False, "error": str(e)}

    async def _execute(self, run_id: str, plan: Plan, results: dict[str, StepResult], rules: VillageRules) -> None:
        """依相依關係一批一批執行：同一批互不依賴，開啟平行時同時跑（最多 max_parallel 個）。"""
        sem = asyncio.Semaphore(rules.max_parallel if rules.parallel else 1)
        remaining = list(plan.steps)
        while remaining:
            ready = [s for s in remaining if all(results[u].status != "pending" for u in s.use)]
            if not ready:  # 理論上不會發生（use 只能引用前面的步驟）
                break
            remaining = [s for s in remaining if s not in ready]

            async def one(step: Step) -> None:
                async with sem:
                    await self._start_step(run_id, step, results, rules)

            if rules.parallel and len(ready) > 1:
                await asyncio.gather(*(one(s) for s in ready))
            else:
                for s in ready:
                    await one(s)

    async def _start_step(self, run_id: str, step: Step, results: dict[str, StepResult], rules: VillageRules) -> None:
        r = results[step.id]
        deps = [results[u] for u in step.use]
        if any(d.status != "done" for d in deps):
            r.status, r.error = "error", "前一步失敗，略過"
            await self.bus.publish("agent.error", run_id=run_id, step_id=step.id, agent=step.agent, error=r.error)
            return
        for d in deps:
            if d.agent != step.agent:
                await self.bus.publish("agent.handoff", run_id=run_id, from_agent=d.agent, to_agent=step.agent)
        if deps and self.handoff_delay:
            await asyncio.sleep(self.handoff_delay)
        await self._run_step(run_id, step, r, rules, step.instruction, render_context(step, results, self.registry))

    async def _run_step(
        self, run_id: str, step: Step, r: StepResult, rules: VillageRules, instruction: str, context: str
    ) -> None:
        emit = self.bus.publish
        spec = self.registry.get(step.agent)
        await emit("agent.started", run_id=run_id, step_id=step.id, agent=step.agent, instruction=step.instruction)
        t0 = time.monotonic()
        try:
            if spec is None:
                raise LLMError(f"村民「{step.agent}」不存在")

            async def on_retry(reason: str) -> None:
                await emit("agent.retry", run_id=run_id, step_id=step.id, agent=step.agent, reason=reason)

            await asyncio.wait_for(
                run_agent(
                    self.llm, spec, instruction, context, r,
                    on_text=DeltaEmitter(self.bus, run_id=run_id, step_id=step.id, agent=step.agent),
                    on_retry=on_retry,
                ),
                timeout=rules.step_timeout_s,
            )
            r.status, r.error = "done", None
        except TimeoutError:
            r.status, r.error = "error", f"逾時：超過 {rules.step_timeout_s} 秒沒有完成（可在「村規」調整）"
        except LLMError as e:
            r.status, r.error = "error", str(e)
        r.elapsed_ms += int((time.monotonic() - t0) * 1000)
        if r.status == "done":
            await emit(
                "agent.finished", run_id=run_id, step_id=step.id, agent=step.agent,
                output=r.output, model=r.model, elapsed_ms=r.elapsed_ms, warnings=r.warnings,
                attachments=r.attachments,
            )
        else:
            await emit("agent.error", run_id=run_id, step_id=step.id, agent=step.agent, error=r.error)

    async def _review_loop(
        self, run_id: str, message: str, plan: Plan, results: dict[str, StepResult], reviews: list[Review],
        rules: VillageRules,
    ) -> None:
        """審核員檢查最終成果；不合格就把理由交給負責的村民重做（最多 review_redo 次），之後照實交出。"""
        emit = self.bus.publish
        by_id = {s.id: s for s in plan.steps}
        for round_ in range(rules.review_redo + 1):
            leaves = [
                Leaf(s.id, self._name(s.agent), s.instruction, results[s.id].output) for s in leaf_steps(plan)
            ]
            await emit("review.started", run_id=run_id, agent=REVIEWER_ID, round=round_ + 1)
            rv = await review(self.llm, message, leaves, rules.reviewer_target)
            if rv is None:
                await emit("review.finished", run_id=run_id, agent=REVIEWER_ID, skipped=True, reason="審核員無法使用，略過審核")
                return
            reviews.append(rv)
            await emit("review.finished", run_id=run_id, agent=REVIEWER_ID, **rv.to_dict())
            if rv.passed or round_ == rules.review_redo:
                return
            step, r = by_id[rv.step], results[rv.step]
            r.rejected.append({"output": r.output, "reason": rv.reason})
            await emit("agent.retry", run_id=run_id, step_id=step.id, agent=step.agent, reason=f"審核員退回：{rv.reason}")
            await emit("agent.handoff", run_id=run_id, from_agent=REVIEWER_ID, to_agent=step.agent)
            instruction = f"{step.instruction}\n\n審核員退回了你上一次的成果。原因：{rv.reason}\n請這樣修正：{rv.fix or rv.reason}"
            context = render_context(step, results, self.registry)
            context = f"{context}\n\n你上一次的成果（被退回）：\n{r.output}".strip()
            await self._run_step(run_id, step, r, rules, instruction, context)
            if r.status != "done":
                return

    def _name(self, agent_id: str) -> str:
        spec = self.registry.get(agent_id)
        return spec.name if spec else agent_id


async def run_agent(
    llm: ChatLLM,
    spec: AgentSpec,
    instruction: str,
    context: str,
    r: StepResult,
    *,
    on_text: TextCallback | None = None,
    on_retry: Callable[[str], Awaitable[None]] | None = None,
    target: str | None = None,
) -> None:
    """依村民類型執行；問答、試打、測試題共用。"""
    if spec.kind == "tool":
        await run_tool_agent(llm, spec, instruction, context, r, on_text=on_text, on_retry=on_retry, target=target)
    else:
        await run_prompt_agent(llm, spec, instruction, context, r, on_text=on_text, on_retry=on_retry, target=target)


TOOL_RETRIES = 2  # 參數不合法時帶錯誤訊息請 LLM 重新整理的次數
_NUMBER = re.compile(r"-?[\d,]+(?:\.\d+)?")


async def run_tool_agent(
    llm: ChatLLM,
    spec: AgentSpec,
    instruction: str,
    context: str,
    r: StepResult,
    *,
    on_text: TextCallback | None = None,
    on_retry: Callable[[str], Awaitable[None]] | None = None,
    target: str | None = None,
) -> None:
    """工具型村民：LLM 整理參數（JSON schema）→ 工具計算 → 參數有錯帶回饋重來 → 仍不行就用工具的退路從文字抓參數。

    LLM 完全不能用時也會直接走退路，所以弱模型或沒有模型時，簡單的計算 / 圖表 / 日期仍然做得到。
    """
    assert spec.tool is not None
    tool = TOOLS[spec.tool.id]
    system = spec.prompt.system.strip() if spec.prompt and spec.prompt.system.strip() else tool.default_prompt
    if extra := tool.context():
        system = f"{system}\n\n{extra}"
    template = spec.prompt.user_template if spec.prompt else "任務：{instruction}\n{context}"
    r.input = render_user(template, instruction, context)
    messages = [ChatMessage("user", r.input)]
    result: ToolResult | None = None
    problem = ""

    async def say(text: str) -> None:
        if on_text:
            await on_text(text)

    for _ in range(TOOL_RETRIES + 1):
        r.attempts += 1
        try:
            res = await llm.chat(
                target or spec.model.target, messages, system=system, json_schema=tool.schema(),
                temperature=spec.model.temperature, max_tokens=spec.model.max_tokens, fallback=spec.model.fallback,
            )
        except LLMError as e:
            problem = f"LLM 無法使用：{e}"
            break
        r.model = res.model
        r.tokens_in += res.usage.get("input_tokens", 0)
        r.tokens_out += res.usage.get("output_tokens", 0)
        try:
            args = _extract_json(res.text)
            await say(f"🔧 交給{tool.name}：{json.dumps(args, ensure_ascii=False)[:200]}")
            result, r.tool_args = tool.run(args), args
            break
        except (PlanError, ToolError) as e:
            problem = str(e)
            r.retry_reasons.append(problem)
            if on_retry:
                await on_retry(f"參數有問題：{problem}")
            messages += [
                ChatMessage("assistant", res.text or "（空白）"),
                ChatMessage("user", f"這組參數有問題：{problem}。請修正後重新輸出完整 JSON。"),
            ]

    if result is None:  # 退路：不靠 LLM，直接從文字抓參數
        args = tool.fallback(instruction, context)
        if args is not None:
            try:
                result, r.tool_args = tool.run(args), args
                r.warnings.append("LLM 沒整理出可用的參數，改由程式直接從文字抓取")
            except ToolError as e:
                problem = f"{problem}；程式直接抓取也失敗：{e}" if problem else str(e)
        if result is None:
            raise LLMError(f"{tool.name}沒辦法完成：{problem or '看不出要處理的內容'}")

    r.output = result.text
    r.attachments = [a.to_dict() for a in result.attachments]
    if spec.tool.explain:
        await _explain(llm, spec, instruction, result, r, target, say)


async def _explain(llm: ChatLLM, spec: AgentSpec, instruction: str, result: ToolResult, r: StepResult, target, say) -> None:
    """請 LLM 用一兩句話說明工具結果；說明裡的數字必須和工具結果一致，不一致就只給工具結果。"""
    fact = result.detail or result.text
    try:
        res = await llm.chat(
            target or spec.model.target,
            [ChatMessage("user", f"使用者的請求：{instruction}\n工具算出的結果：{fact}")],
            fallback=spec.model.fallback,
            system=f"你是村子裡的{spec.name}。用一兩句繁體中文回答使用者，數字必須和工具結果完全相同，不要重新計算、不要補充其他數字。",
            temperature=0.2,
            max_tokens=400,
        )
    except LLMError:
        return
    r.tokens_in += res.usage.get("input_tokens", 0)
    r.tokens_out += res.usage.get("output_tokens", 0)
    text = normalize(res.text)
    nums = _NUMBER.findall(fact.split("=")[-1]) if "=" in fact else []
    if not text or (nums and nums[-1].replace(",", "") not in text.replace(",", "")):
        r.warnings.append("說明裡的數字和工具結果不一致，只顯示工具結果")
        return
    r.output = f"{text}\n\n{result.text}"
    await say(r.output)


async def run_prompt_agent(
    llm: ChatLLM,
    spec: AgentSpec,
    instruction: str,
    context: str,
    r: StepResult,
    *,
    on_text: TextCallback | None = None,
    on_retry: Callable[[str], Awaitable[None]] | None = None,
    target: str | None = None,
) -> None:
    """執行一位 prompt 型村民：呼叫 LLM → harness 檢查 → 不合格帶回饋重做。結果寫進 r。

    問答流程與「村民管理 → 試打 / 測試」共用。target 可指定模型（比較雲端與本地用），預設依村民設定。
    """
    assert spec.prompt is not None
    r.input = render_user(spec.prompt.user_template, instruction, context)
    messages = [ChatMessage("user", r.input)]
    feedback: str | None = None
    for _ in range(STEP_RETRIES + 1):
        r.attempts += 1
        result = await llm.chat(
            target or spec.model.target,
            messages,
            fallback=spec.model.fallback,
            system=spec.prompt.system,
            temperature=spec.model.temperature,
            max_tokens=spec.model.max_tokens,
            **({"on_text": on_text} if on_text else {}),
        )
        text = normalize(result.text)
        r.output, r.model = text, result.model
        r.tokens_in += result.usage.get("input_tokens", 0)
        r.tokens_out += result.usage.get("output_tokens", 0)
        feedback = check_output(text, spec.checks)
        if feedback is None:
            return
        r.retry_reasons.append(feedback)
        if on_retry:
            await on_retry(feedback)
        messages += [ChatMessage("assistant", text or "（空白）"), ChatMessage("user", feedback)]

    # 重試後仍不合格：髒話直接遮蔽、太長照收但標警告、空白則失敗
    if not r.output:
        raise LLMError(feedback or "輸出為空")
    if profanity_hits(r.output):
        r.output = mask_profanity(r.output)
        r.warnings.append("已遮蔽不適當用語")
    if feedback and "太長" in feedback:
        r.warnings.append("內容超過長度上限")
