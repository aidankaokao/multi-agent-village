"""RAG 本體：切塊、混合檢索、增量建索引、問答（引用、退路、LLM 設定的安全性）。不連 Ollama、不連真的 LLM。"""

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app import llm as llm_mod
from app.answer import ask
from app.chunker import Doc, chunk_doc
from app.config import Settings
from app.indexer import build_index
from app.llm import LLMConfig, LLMError, check_url
from app.store import Store, terms

DOC_A = """---
title: 霧社事件
url: https://zh.wikipedia.org/zh-tw/霧社事件
revision: 1
fetched: '2026-10-17'
period: japanese
period_name: 日治時期
license: CC BY-SA 4.0
---

# 霧社事件

霧社事件是1930年發生在臺灣的原住民抗日事件，由莫那·魯道率領賽德克族起事。

## 事件經過

起事者趁霧社公學校舉行運動會時襲擊日本人，日方隨後調集軍警鎮壓。
"""

DOC_B = """---
title: 鄭成功
url: https://zh.wikipedia.org/zh-tw/鄭成功
revision: 1
fetched: '2026-10-17'
period: koxinga
period_name: 明鄭時期
license: CC BY-SA 4.0
---

# 鄭成功

鄭成功於1661年率軍攻臺，次年迫使荷蘭人投降，建立明鄭政權。
"""


class FakeEmbedder:
    """用關鍵字決定向量：含「霧社」→ 方向 0，含「鄭成功」→ 方向 1，其他 → 方向 2。"""

    model = "fake-embed"

    def __init__(self):
        self.calls = 0

    async def embed(self, texts):
        self.calls += len(texts)
        out = np.zeros((len(texts), 3), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, 0 if "霧社" in t else 1 if "鄭成功" in t else 2] = 1
        return out

    async def aclose(self):
        pass


@pytest.fixture
def corpus(tmp_path) -> Settings:
    docs = tmp_path / "docs"
    (docs / "japanese").mkdir(parents=True)
    (docs / "koxinga").mkdir()
    (docs / "japanese" / "霧社事件.md").write_text(DOC_A, encoding="utf-8")
    (docs / "koxinga" / "鄭成功.md").write_text(DOC_B, encoding="utf-8")
    return Settings(corpus_dir=docs, data_dir=tmp_path / "data")


def test_chunk_keeps_section_path():
    from app.chunker import load_doc

    p = Path("/tmp/_th_test_doc.md")
    p.write_text(DOC_A, encoding="utf-8")
    chunks = chunk_doc(load_doc(p))
    # 導言太短（< 120 字）：併到下一塊，並標上原本的章節
    assert [c.section for c in chunks] == ["霧社事件 › 事件經過"]
    assert chunks[0].text.startswith("【霧社事件】霧社事件是1930年") and "運動會" in chunks[0].text
    assert chunks[0].embed_text().startswith("霧社事件 › 事件經過\n")


def test_long_paragraph_is_split():
    body = "# 長文\n\n" + "這是一句很長的話。" * 200
    doc = Doc("長文", "u", "p", "P", None, body, Path("x"))
    chunks = chunk_doc(doc)
    assert len(chunks) > 1 and all(len(c.text) <= 900 for c in chunks)


def test_terms_handles_short_chinese_names_and_years():
    t = terms("鄭經在1662年")
    assert "鄭經" in t and "1662" in t


async def test_incremental_index_and_hybrid_search(corpus):
    store, emb = Store(corpus.index_path), FakeEmbedder()
    r = await build_index(corpus, store, emb, progress=lambda _: None)
    assert r["docs"] == 2 and r["updated"] == 2
    calls = emb.calls
    r = await build_index(corpus, store, emb, progress=lambda _: None)
    assert r["updated"] == 0 and emb.calls == calls  # 沒變動：不重新向量化

    (corpus.corpus_dir / "koxinga" / "鄭成功.md").write_text(DOC_B.replace("1661", "1661年4月"), encoding="utf-8")
    r = await build_index(corpus, store, emb, progress=lambda _: None)
    assert r["updated"] == 1  # 只重做改過的那篇

    (corpus.corpus_dir / "koxinga" / "鄭成功.md").unlink()
    r = await build_index(corpus, store, emb, progress=lambda _: None)
    assert r["removed"] == 1 and r["docs"] == 1

    hits = store.search("霧社事件的經過", (await emb.embed(["霧社"]))[0], k=2)
    assert hits[0].doc_title == "霧社事件" and hits[0].vector_rank and hits[0].keyword_rank


def test_llm_url_allowlist():
    hosts = ["openrouter.ai", "localhost"]
    check_url("https://openrouter.ai/api/v1", hosts)
    check_url("http://localhost:11434/v1", hosts)
    for bad in ("http://169.254.169.254/latest", "https://evil.example.com/v1", "file:///etc/passwd", "openrouter.ai"):
        with pytest.raises(LLMError):
            check_url(bad, hosts)


def test_api_key_never_in_repr():
    assert "sk-secret" not in repr(LLMConfig("https://openrouter.ai/api/v1", "m", "sk-secret"))


async def _indexed(corpus):
    store, emb = Store(corpus.index_path), FakeEmbedder()
    await build_index(corpus, store, emb, progress=lambda _: None)
    return store, emb


def _mock_llm(monkeypatch, handler):
    monkeypatch.setattr(llm_mod, "_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))


async def test_ask_cites_sources(corpus, monkeypatch):
    store, emb = await _indexed(corpus)
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "m-1", "choices": [{"message": {"content": "霧社事件發生於1930年[1]。"}}]})

    _mock_llm(monkeypatch, handler)
    a = await ask("霧社事件是哪一年？", store, emb, LLMConfig("https://openrouter.ai/api/v1", "m", "sk-secret"), k=3)
    assert a.answer == "霧社事件發生於1930年[1]。" and a.model == "m-1"
    assert [c["title"] for c in a.citations] == ["霧社事件"] and a.citations[0]["url"].endswith("霧社事件")
    assert seen["auth"] == "Bearer sk-secret"
    user = seen["body"]["messages"][1]["content"]
    assert "[1] 《霧社事件》" in user and "問題：霧社事件是哪一年？" in user


async def test_ask_falls_back_to_sources_and_hides_key(corpus, monkeypatch):
    store, emb = await _indexed(corpus)
    _mock_llm(monkeypatch, lambda r: httpx.Response(401, text="invalid key sk-secret"))
    a = await ask("霧社事件", store, emb, LLMConfig("https://openrouter.ai/api/v1", "m", "sk-secret"), k=3)
    assert "LLM 無法使用" in a.answer and "《霧社事件》" in a.answer
    assert "sk-secret" not in a.answer and "sk-secret" not in (a.llm_error or "")


async def test_ask_adapts_max_tokens(corpus, monkeypatch):
    store, emb = await _indexed(corpus)
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "答案[1]"}}]})

    _mock_llm(monkeypatch, handler)
    a = await ask("鄭成功", store, emb, LLMConfig("https://api.openai.com/v1", "gpt-5"), k=2)
    assert a.answer == "答案[1]" and "max_completion_tokens" in bodies[-1]


async def test_http_api(corpus, monkeypatch):
    from app.main import create_app

    await _indexed(corpus)
    app = create_app(corpus, embedder=FakeEmbedder())
    _mock_llm(monkeypatch, lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "鄭成功於1661年攻臺[1]。"}}]}))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/health")).json()["ok"] is True
            assert (await c.get("/search", params={"q": "鄭成功"})).json()["hits"][0]["title"] == "鄭成功"
            headers = {"X-LLM-Base-URL": "https://openrouter.ai/api/v1", "X-LLM-Model": "m", "X-LLM-API-Key": "sk-secret"}
            r = (await c.post("/ask", json={"question": "鄭成功何時攻臺？"}, headers=headers)).json()
            assert r["answer"].startswith("鄭成功於1661年") and r["citations"][0]["title"] == "鄭成功"
            assert "sk-secret" not in json.dumps(r)
            bad = await c.post("/ask", json={"question": "x"}, headers={**headers, "X-LLM-Base-URL": "http://10.0.0.5/v1"})
            assert bad.status_code == 400 and "不允許" in bad.json()["detail"]
            r = (await c.post("/ask", json={"question": "霧社事件"})).json()  # 沒帶 LLM：只給資料
            assert r["llm_error"] and r["sources"]
