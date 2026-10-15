"""執行一次問答：村長規劃 → 依序執行步驟 → harness 檢查 → 發事件。

每個動作都發事件到 EventBus，前端的村落動畫與聊天框完全由事件驅動。
"""

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field

from village.core.card import AgentSpec
from village.core.harness import check_output, mask_profanity, normalize, profanity_hits
from village.core.orchestrator import ChatLLM, Plan, Step, direct_reply, make_plan
from village.core.registry import AgentRegistry
from village.events import EventBus
from village.llm.base import ChatMessage, LLMError

log = logging.getLogger("village.executor")

MAYOR = "mayor"
STEP_RETRIES = 1  # 輸出不合格時帶回饋重做的次數


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


class Runner:
    def __init__(self, registry: AgentRegistry, llm: ChatLLM, bus: EventBus, handoff_delay: float = 1.0):
        self.registry = registry
        self.llm = llm
        self.bus = bus
        self.handoff_delay = handoff_delay  # 讓前端有時間播放「走過去交接」的動畫

    async def run(self, run_id: str, message: str) -> dict:
        emit = self.bus.publish
        self.registry.refresh()
        agents = self.registry.all()
        await emit("run.started", run_id=run_id, agent=MAYOR, message=message)
        try:
            plan = await make_plan(message, agents, self.llm)
            await emit("plan.created", run_id=run_id, agent=MAYOR, **plan.to_dict())

            if not plan.steps:
                answer = await direct_reply(message, agents, self.llm)
                await emit("run.finished", run_id=run_id, ok=True, answer=answer, steps=[])
                return {"ok": True, "answer": answer, "plan": plan.to_dict(), "steps": []}

            results = await self._execute(run_id, plan)
            last = results[plan.steps[-1].id]
            ok = all(r.status == "done" for r in results.values())
            answer = last.output if last.status == "done" else ""
            steps = [asdict(r) for r in results.values()]
            await emit("run.finished", run_id=run_id, ok=ok, answer=answer, steps=steps)
            return {"ok": ok, "answer": answer, "plan": plan.to_dict(), "steps": steps}
        except Exception as e:
            log.exception("run %s failed", run_id)
            await emit("run.failed", run_id=run_id, agent=MAYOR, error=str(e))
            return {"ok": False, "error": str(e)}

    async def _execute(self, run_id: str, plan: Plan) -> dict[str, StepResult]:
        results: dict[str, StepResult] = {
            s.id: StepResult(id=s.id, agent=s.agent, instruction=s.instruction, use=s.use) for s in plan.steps
        }
        for step in plan.steps:
            r = results[step.id]
            deps = [results[u] for u in step.use]
            if any(d.status != "done" for d in deps):
                r.status, r.error = "error", "前一步失敗，略過"
                await self.bus.publish("agent.error", run_id=run_id, step_id=step.id, agent=step.agent, error=r.error)
                continue
            for d in deps:
                if d.agent != step.agent:
                    await self.bus.publish("agent.handoff", run_id=run_id, from_agent=d.agent, to_agent=step.agent)
            if deps and self.handoff_delay:
                await asyncio.sleep(self.handoff_delay)
            await self._run_step(run_id, step, r, results)
        return results

    async def _run_step(self, run_id: str, step: Step, r: StepResult, results: dict[str, StepResult]) -> None:
        emit = self.bus.publish
        spec = self.registry.get(step.agent)
        await emit("agent.started", run_id=run_id, step_id=step.id, agent=step.agent, instruction=step.instruction)
        t0 = time.monotonic()
        try:
            if spec is None:
                raise LLMError(f"村民「{step.agent}」不存在")
            await self._run_prompt_agent(run_id, spec, step, r, render_context(step, results, self.registry))
            r.status = "done"
        except LLMError as e:
            r.status, r.error = "error", str(e)
        r.elapsed_ms = int((time.monotonic() - t0) * 1000)
        if r.status == "done":
            await emit(
                "agent.finished", run_id=run_id, step_id=step.id, agent=step.agent,
                output=r.output, model=r.model, elapsed_ms=r.elapsed_ms, warnings=r.warnings,
            )
        else:
            await emit("agent.error", run_id=run_id, step_id=step.id, agent=step.agent, error=r.error)

    async def _run_prompt_agent(self, run_id: str, spec: AgentSpec, step: Step, r: StepResult, context: str) -> None:
        assert spec.prompt is not None
        messages = [ChatMessage("user", render_user(spec.prompt.user_template, step.instruction, context))]
        feedback: str | None = None
        for attempt in range(STEP_RETRIES + 1):
            r.attempts = attempt + 1
            result = await self.llm.chat(
                spec.model.target,
                messages,
                system=spec.prompt.system,
                temperature=spec.model.temperature,
                max_tokens=spec.model.max_tokens,
            )
            text = normalize(result.text)
            r.output, r.model = text, result.model
            feedback = check_output(text, spec.checks)
            if feedback is None:
                return
            await self.bus.publish("agent.retry", run_id=run_id, step_id=step.id, agent=spec.id, reason=feedback)
            messages += [ChatMessage("assistant", text or "（空白）"), ChatMessage("user", feedback)]

        # 重試後仍不合格：髒話直接遮蔽、太長照收但標警告、空白則失敗
        if not r.output:
            raise LLMError(feedback or "輸出為空")
        if profanity_hits(r.output):
            r.output = mask_profanity(r.output)
            r.warnings.append("已遮蔽不適當用語")
        if feedback and "太長" in feedback:
            r.warnings.append("內容超過長度上限")
