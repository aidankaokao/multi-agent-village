"""執行一次問答：（有附圖時鑑定師先看圖）→ 村長規劃 → 執行步驟（互不依賴的可平行）→ harness 檢查 → 審核員 → 發事件、寫執行紀錄。

每個動作都發事件到 EventBus，前端的村落動畫與聊天框完全由事件驅動。
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

from village.core.appraiser import APPRAISER_ID, Appraisal, appraise
from village.core.card import AgentSpec
from village.core import remote
from village.core.painter import PaintEnv, painting
from village.core.context import PREV, Turn
from village.core import loop as loops
from village.core.docfiles import DocFile
from village.core.harness import check_output, mask_profanity, normalize, preview, profanity_hits
from village.core.orchestrator import (
    ChatLLM, Plan, PlanError, Step, _extract_json, direct_reply, make_plan, render_documents,
)
from village.core.registry import AgentRegistry
from village.core.scribe import render_memories
from village.core.reviewer import REVIEWER_ID, Leaf, Review, review
from village.events import EventBus
from village.llm.base import ChatMessage, ImagePart, LLMError, TextCallback
from village.llm.service import llm_user
from village.store.runs import TraceStore
from village.store.settings import RulesStore, VillageRules
from village.tools import TOOLS, ToolError, ToolResult
from village.tools.base import Attachment

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
    round: int = 1  # 迴圈的第幾輪
    verdict: dict | None = None  # 迴圈的審查步驟：{passed, feedback, how}
    history: list[dict] = field(default_factory=list)  # 迴圈前幾輪的成果 {round, output, verdict}


def render_context(
    step: Step, results: dict[str, StepResult], registry: AgentRegistry, previous: str = ""
) -> str:
    parts = []
    if memories := _MEMORIES.get():
        parts.append(memories)
    if appraisal := _APPRAISAL.get():
        parts.append(appraisal)
    if PREV in step.use and previous:
        parts.append(f"參考資料（上一輪的回答）：\n{previous}")
    if state := _LOOP.get():
        if note := state.note_for(step.id):
            parts.append(note)
    for ref in step.use:
        r = results.get(ref)
        if r and r.output:
            spec = registry.get(r.agent)
            parts.append(f"參考資料（來自{spec.name if spec else r.agent}）：\n{r.output}")
    return "\n\n".join(parts)


def render_user(template: str, instruction: str, context: str) -> str:
    return template.replace("{instruction}", instruction).replace("{context}", context).strip()


def leaf_steps(plan: Plan) -> list[Step]:
    """最後交給使用者的步驟：沒有其他步驟用到它的成果（例如「同時翻成英文和日文」就有兩個）。

    迴圈的審查步驟不算：交給使用者的是被審查的成果（審查意見只顯示在執行過程）。
    """
    used = {u for s in plan.steps for u in s.use}
    by_id = {s.id: s for s in plan.steps}
    out: list[Step] = []
    for s in plan.steps:
        if s.id in used:
            continue
        if plan.loop and s.id == plan.loop.check:
            out += [by_id[u] for u in s.use if u in by_id and by_id[u] not in out]
        elif s not in out:
            out.append(s)
    return out


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


# 上一輪的回答：Runner 是全村共用的，同時有好幾個人在問答，所以放 context variable（每個問答 task 各自一份）
_PREVIOUS: ContextVar[str] = ContextVar("previous_answer", default="")
_MEMORIES: ContextVar[str] = ContextVar("memories", default="")  # 書記記下的長期記憶（已排成文字）
_APPRAISAL: ContextVar[str] = ContextVar("appraisal", default="")  # 鑑定師看圖的結果（已排成文字）
_LOOP: ContextVar[loops.LoopState | None] = ContextVar("loop", default=None)  # 迴圈進度（第幾輪、審查意見）


@dataclass
class _DocNote:
    docs: list[DocFile]
    fresh: bool  # True = 這次附的；False = 前幾輪附的（追問時才可能用到）
    agent: str | None = None  # 追問上一份報告：一定要交給這位


_DOCS: ContextVar[_DocNote] = ContextVar("docs", default=_DocNote([], False, None))
DOC_STEP_TIMEOUT_S = 900  # 收文件的駐村專家（產生報告）比一般步驟久：至少給這麼久

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
        directory: remote.RemoteDirectory | None = None,
    ):
        self.registry = registry
        self.llm = llm
        self.bus = bus
        self.handoff_delay = handoff_delay  # 讓前端有時間播放「走過去交接」的動畫
        self.rules = rules
        self.trace = trace
        self.directory = directory  # 駐村專家的狀態與 token（沒有時不派工給駐村專家）

    async def run(
        self, run_id: str, message: str, traced: bool = False, user_id: str | None = None,
        history: list[Turn] | None = None, memories: list[str] | None = None,
        images: list[ImagePart] | None = None, relook: bool = False, paint: PaintEnv | None = None,
        docs: list[DocFile] | None = None, fresh_docs: bool = False, doc_agent: str | None = None,
        save_file: Callable[[str, str, bytes], Awaitable[str]] | None = None,
    ) -> dict:
        """traced=True 代表呼叫端已經建立了執行紀錄（POST /api/chat）；user_id：提問者，事件內容只送給他。

        images：要給鑑定師看的圖（這次附的，或 relook=True 時是前面對話附的）。
        paint：畫家的作畫環境（誰、哪段對話、張數上限）；沒有就不能畫圖。
        docs：文件（這次附的，fresh_docs=True；或前幾輪附的，追問時再交給收文件的駐村專家）。
        doc_agent：追問上一份報告時，一定要交給的駐村專家（上一輪做報告的那位）。
        save_file：存駐村專家回傳的檔案（例如報告 PDF），回傳檔案 id。
        """
        self.bus.claim(run_id, user_id)
        try:
            env = (
                remote.RemoteEnv(self.directory, self.llm, user_id, docs=docs or [], save_file=save_file)
                if self.directory else None
            )
            with llm_user(user_id), painting(paint), remote.remote_calls(env):  # 這次問答的 LLM 呼叫都用提問者自己的設定與額度
                _MEMORIES.set(render_memories(memories) if memories else "")
                _APPRAISAL.set("")
                _DOCS.set(_DocNote(docs or [], fresh_docs, doc_agent))
                _LOOP.set(None)
                return await self._run(run_id, message, traced, user_id, history or [], images or [], relook)
        finally:
            self.bus.release(run_id)

    async def _run(
        self, run_id: str, message: str, traced: bool, user_id: str | None, history: list[Turn],
        images: list[ImagePart], relook: bool,
    ) -> dict:
        _PREVIOUS.set(history[-1].answer if history else "")  # use 裡的 "prev" 指這個（每次問答各自一份）
        emit = self.bus.publish
        rules = self.rules.current if self.rules else DEFAULT_RULES
        # 駐村專家只派「在村」的（健康檢查每分鐘更新）；外出中的不出現在村長的名冊
        online = await self.directory.online_ids() if self.directory else set()
        agents = [a for a in self.registry.all() if a.kind != "remote" or a.id in online]
        t0 = time.monotonic()
        plan: Plan | None = None
        results: dict[str, StepResult] = {}
        reviews: list[Review] = []
        looked: Appraisal | None = None
        if self.trace:
            self.trace.keep = rules.keep_runs
            if not traced:
                await self.trace.start(run_id, message, user_id)

        async def record(status: str, **fields) -> None:
            if self.trace:
                plan_out = plan.to_dict() if plan else None
                if plan_out is not None and looked:
                    plan_out["appraisal"] = looked.to_dict()  # 鑑定師看圖的結果也記在執行紀錄
                await self.trace.finish(
                    run_id, status=status, plan=plan_out,
                    steps=[asdict(r) for r in results.values()], reviews=[rv.to_dict() for rv in reviews],
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                    extra_tokens_in=sum(rv.tokens_in for rv in reviews) + (looked.tokens_in if looked else 0),
                    extra_tokens_out=sum(rv.tokens_out for rv in reviews) + (looked.tokens_out if looked else 0),
                    **fields,
                )

        await emit("run.started", run_id=run_id, agent=MAYOR, message=message)
        try:
            async def on_note(note: str) -> None:
                await emit("plan.note", run_id=run_id, agent=MAYOR, note=note)

            if images:
                looked = await self._appraise(run_id, message, images, relook, history)
                _APPRAISAL.set(looked.render())

            note = _DOCS.get()
            doc_agents = [a for a in agents if a.kind == "remote" and a.remote and a.remote.accepts]
            documents = render_documents(note.docs, note.fresh, doc_agents) if note.docs else ""
            plan = await make_plan(
                message, agents, self.llm, on_note=on_note, history=history, appraisal=_APPRAISAL.get(), documents=documents,
                disabled=self.registry.disabled_text(),
            )
            if note.docs and (note.fresh or note.agent) and doc_agents and not any(
                    s.agent in {a.id for a in doc_agents} for s in plan.steps):
                # 這次附了文件（或在追問上一份報告），村長卻沒派給收文件的專家（弱模型常見）：補上這一步
                why = "附了文件" if note.fresh else "追問上一份報告"
                agent = next((a for a in doc_agents if a.id == note.agent), None) or next(
                    (a for a in doc_agents if remote.files_for(a)), doc_agents[0])
                plan = Plan(steps=[Step(id="s1", agent=agent.id, instruction=message)],
                            source="llm" if plan.source == "direct" else plan.source,
                            notes=plan.notes + [f"{why}：交給{agent.name}處理"])
                await on_note(f"{why}：交給{agent.name}處理")
            await emit("plan.created", run_id=run_id, agent=MAYOR, **plan.to_dict())

            if not plan.steps:
                await emit("agent.started", run_id=run_id, step_id=None, agent=MAYOR, instruction=message)
                on_text = DeltaEmitter(self.bus, run_id=run_id, step_id=None, agent=MAYOR)
                answer = await direct_reply(
                    message, agents, self.llm, on_text=on_text, history=history, memories=_MEMORIES.get(),
                    appraisal=_APPRAISAL.get(), appraisal_text=looked.text if looked and looked.ok else "",
                    disabled=self.registry.disabled_text(),
                )
                await emit("agent.finished", run_id=run_id, step_id=None, agent=MAYOR, output=answer)
                await emit("run.finished", run_id=run_id, ok=True, answer=answer, steps=[], reviews=[])
                await record("done", answer=answer)
                return {"ok": True, "answer": answer, "plan": plan.to_dict(), "steps": [], "reviews": []}

            results = {s.id: StepResult(id=s.id, agent=s.agent, instruction=s.instruction, use=s.use) for s in plan.steps}
            await self._execute(run_id, plan, results, rules)
            answer, ok = compose_answer(plan, results, self.registry)
            answer = _loop_footnote(answer, plan, results, self.registry)
            if ok and rules.reviewer_enabled:
                await self._review_loop(run_id, message, plan, results, reviews, rules, history)
                answer, ok = compose_answer(plan, results, self.registry)
                answer = _loop_footnote(answer, plan, results, self.registry)

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
        """依相依關係一批一批執行：同一批互不依賴，開啟平行時同時跑（最多 max_parallel 個）。

        有迴圈時：審查步驟做完就判定；不通過且還有輪數 → 從 back_to 到審查步驟重做（帶著上一版與審查意見）。
        """
        sem = asyncio.Semaphore(rules.max_parallel if rules.parallel else 1)
        remaining = list(plan.steps)
        state = None
        if plan.loop:
            ids = [s.id for s in plan.steps]
            span = ids[ids.index(plan.loop.back_to) : ids.index(plan.loop.check) + 1]
            limit = min(plan.loop.max_rounds or rules.loop_max_rounds, rules.loop_max_rounds)
            checker = self._name(results[plan.loop.check].agent)
            state = loops.LoopState(plan.loop, 1, max(1, limit), span, checker=checker)
            _LOOP.set(state)
        executed = 0
        while remaining:
            ready = [s for s in remaining if all(results[u].status != "pending" for u in s.use if u != PREV)]
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
            executed += len(ready)
            if state and state.loop and any(s.id == state.loop.check for s in ready):
                if await self._judge_round(run_id, plan, results, state, executed):
                    redo = [s for s in plan.steps if s.id in state.span]
                    remaining = redo + [s for s in remaining if s not in redo]

    async def _judge_round(
        self, run_id: str, plan: Plan, results: dict[str, StepResult], state: loops.LoopState, executed: int
    ) -> bool:
        """迴圈的審查步驟做完：判定通過與否；要重做就把範圍內的步驟重設成下一輪，回傳 True。"""
        loop = state.loop
        r = results[loop.check]
        if r.status != "done":
            return False
        verdict = await loops.judge(self.llm, r.output)
        r.verdict = verdict.to_dict()
        back = results[loop.back_to]
        retry = not verdict.passed and state.round < state.max_rounds
        stop = ""
        if retry and executed + len(state.span) > loops.MAX_TOTAL_STEPS:
            retry, stop = False, f"這次問答已經執行 {executed} 步，再重做會超過 {loops.MAX_TOTAL_STEPS} 步的上限"
        elif not verdict.passed and not retry:
            stop = f"已經到第 {state.round}/{state.max_rounds} 輪"
        if verdict.how != "結論":
            r.warnings.append(f"審查結論：{verdict.how}")
        if stop:
            r.warnings.append(f"審查沒有通過，但{stop}，交出最後一版")
        await self.bus.publish(
            "loop.verdict", run_id=run_id, step_id=loop.check, agent=r.agent, passed=verdict.passed,
            feedback=verdict.feedback[:500], how=verdict.how, round=state.round, max_rounds=state.max_rounds,
            back_to=loop.back_to, back_agent=back.agent, retry=retry, stop=stop or None,
        )
        if not retry:
            return False
        state.feedback = verdict.feedback or r.output
        state.previous = {sid: results[sid].output for sid in state.span}
        state.round += 1
        for sid in state.span:  # 這一輪的成果收進歷史，步驟重設成下一輪
            old = results[sid]
            old.history.append({"round": old.round, "output": old.output, "verdict": old.verdict, "elapsed_ms": old.elapsed_ms})
            results[sid] = StepResult(
                id=old.id, agent=old.agent, instruction=old.instruction, use=old.use, round=state.round,
                history=old.history, tokens_in=old.tokens_in, tokens_out=old.tokens_out,
            )
        if r.agent != back.agent:  # 地圖：審查者把稿子交還給被退回的村民
            await self.bus.publish("agent.handoff", run_id=run_id, from_agent=r.agent, to_agent=back.agent)
        return True

    async def _start_step(self, run_id: str, step: Step, results: dict[str, StepResult], rules: VillageRules) -> None:
        r = results[step.id]
        deps = [results[u] for u in step.use if u != PREV]  # prev = 上一輪的回答，不是這次的步驟
        if any(d.status != "done" for d in deps):
            r.status, r.error = "error", "前一步失敗，略過"
            await self.bus.publish("agent.error", run_id=run_id, step_id=step.id, agent=step.agent, error=r.error)
            return
        for d in deps:
            if d.agent != step.agent:
                await self.bus.publish("agent.handoff", run_id=run_id, from_agent=d.agent, to_agent=step.agent)
        if deps and self.handoff_delay:
            await asyncio.sleep(self.handoff_delay)
        context = render_context(step, results, self.registry, _PREVIOUS.get())
        state = _LOOP.get()
        is_check = bool(state and state.loop and step.id == state.loop.check)
        instruction = step.instruction + (loops.VERDICT_SUFFIX if is_check else "")
        await self._run_step(run_id, step, r, rules, instruction, context)

    async def _run_step(
        self, run_id: str, step: Step, r: StepResult, rules: VillageRules, instruction: str, context: str
    ) -> None:
        emit = self.bus.publish
        spec = self.registry.get(step.agent)
        await emit("agent.started", run_id=run_id, step_id=step.id, agent=step.agent, instruction=step.instruction, round=r.round)
        t0 = time.monotonic()
        timeout = rules.step_timeout_s
        if spec is not None and remote.files_for(spec):  # 要處理文件、產生報告：放寬時間上限
            timeout = max(timeout, DOC_STEP_TIMEOUT_S)
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
                timeout=timeout,
            )
            r.status, r.error = "done", None
        except TimeoutError:
            r.status, r.error = "error", f"逾時：超過 {timeout} 秒沒有完成（可在「村規」調整）"
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

    async def _appraise(
        self, run_id: str, message: str, images: list[ImagePart], relook: bool, history: list[Turn]
    ) -> Appraisal:
        """鑑定師看圖；看完走去找村長交接（看不了也照樣交代，村長才不會亂猜圖片內容）。"""
        await self.bus.publish("appraise.started", run_id=run_id, agent=APPRAISER_ID, count=len(images), relook=relook)
        looked = await appraise(self.llm, message, images, relook, history[-1] if history else None)
        await self.bus.publish("appraise.finished", run_id=run_id, agent=APPRAISER_ID, **looked.to_dict())
        await self.bus.publish("agent.handoff", run_id=run_id, from_agent=APPRAISER_ID, to_agent=MAYOR)
        return looked

    async def _review_loop(
        self, run_id: str, message: str, plan: Plan, results: dict[str, StepResult], reviews: list[Review],
        rules: VillageRules, history: list[Turn] | None = None,
    ) -> None:
        """審核員檢查最終成果；不合格就把理由交給負責的村民重做（最多 review_redo 次），之後照實交出。"""
        emit = self.bus.publish
        by_id = {s.id: s for s in plan.steps}
        for round_ in range(rules.review_redo + 1):
            leaves = [
                Leaf(s.id, self._name(s.agent), s.instruction, results[s.id].output) for s in leaf_steps(plan)
            ]
            await emit("review.started", run_id=run_id, agent=REVIEWER_ID, round=round_ + 1)
            rv = await review(
                self.llm, message, leaves, rules.reviewer_target, history[-1:] if history else None, _APPRAISAL.get()
            )
            if rv is None:
                await emit("review.finished", run_id=run_id, agent=REVIEWER_ID, skipped=True, reason="審核員無法使用，略過審核")
                return
            reviews.append(rv)
            await emit("review.finished", run_id=run_id, agent=REVIEWER_ID, **rv.to_dict())
            if rv.passed or round_ == rules.review_redo:
                return
            if self._costly(rv.step and by_id[rv.step].agent):
                return  # 例如畫家：重畫要再花一次錢，審核結果照實記錄、不退件
            step, r = by_id[rv.step], results[rv.step]
            r.rejected.append({"output": r.output, "reason": rv.reason})
            await emit("agent.retry", run_id=run_id, step_id=step.id, agent=step.agent, reason=f"審核員退回：{rv.reason}")
            await emit("agent.handoff", run_id=run_id, from_agent=REVIEWER_ID, to_agent=step.agent)
            instruction = f"{step.instruction}\n\n審核員退回了你上一次的成果。原因：{rv.reason}\n請這樣修正：{rv.fix or rv.reason}"
            context = render_context(step, results, self.registry, _PREVIOUS.get())
            context = f"{context}\n\n你上一次的成果（被退回）：\n{r.output}".strip()
            await self._run_step(run_id, step, r, rules, instruction, context)
            if r.status != "done":
                return

    def _costly(self, agent_id: str | None) -> bool:
        spec = self.registry.get(agent_id) if agent_id else None
        return bool(spec and spec.kind == "tool" and spec.tool and spec.tool.id in TOOLS and TOOLS[spec.tool.id].costly)

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
    if spec.kind == "remote":
        await run_remote_agent(spec, instruction, context, r, on_text=on_text)
        return
    if spec.kind == "tool":
        await run_tool_agent(llm, spec, instruction, context, r, on_text=on_text, on_retry=on_retry, target=target)
    else:
        await run_prompt_agent(llm, spec, instruction, context, r, on_text=on_text, on_retry=on_retry, target=target)


async def run_remote_agent(
    spec: AgentSpec, instruction: str, context: str, r: StepResult, *, on_text: TextCallback | None = None
) -> None:
    """駐村專家：用 A2A 把指示（＋前一步成果）交給對方，回答附上引用來源。對方自己處理檢索與 LLM。"""
    r.attempts += 1
    r.input = remote.render_message(instruction, context)  # 執行紀錄只記訊息內容；token、key 只在 header
    if files := remote.files_for(spec):
        r.input += "\n\n附上的文件：" + "、".join(f.describe() for f in files)
    reply, warnings = await remote.call(spec, instruction, context, on_text=on_text)  # 支援的話邊收邊顯示
    sources = remote.render_citations(reply.data)
    text = reply.text or "（沒有文字說明，請看附件）"
    r.output = f"{text}\n\n{sources}" if sources else text
    r.model = f"{reply.data['model']}（{spec.name}）" if reply.data.get("model") else spec.name
    r.warnings += warnings
    env = remote.REMOTE_ENV.get()
    for f in reply.files:  # 對方回傳的檔案（例如報告 PDF）：存起來，回答附上下載連結
        if env is None or env.save_file is None:
            r.warnings.append(f"收到檔案「{f.name}」，但這裡沒辦法保存")
            continue
        file_id = await env.save_file(f.name, f.mime, f.data)
        r.attachments.append(Attachment(kind="file", name=f.name, title=f.name, url=f"/api/files/{file_id}").to_dict())
    if on_text:
        await on_text(r.output)  # 最後補上資料來源


def _loop_footnote(answer: str, plan: Plan, results: dict[str, StepResult], registry: AgentRegistry) -> str:
    """迴圈到最後都沒通過：在回答後面註明（使用者才知道這一版還有問題）。"""
    if not plan.loop or plan.loop.check not in results:
        return answer
    r = results[plan.loop.check]
    if r.status != "done" or not r.verdict or r.verdict.get("passed"):
        return answer
    spec = registry.get(r.agent)
    reason = (r.verdict.get("feedback") or "").strip()
    reason = reason if len(reason) <= 300 else reason[:300] + "…"
    return f"{answer}\n\n（{spec.name if spec else r.agent}到第 {r.round} 輪仍未通過，以上是最後一版。最後的審查意見：{reason}）"


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
            result, r.tool_args = await tool.arun(args), args
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
                result, r.tool_args = await tool.arun(args), args
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
