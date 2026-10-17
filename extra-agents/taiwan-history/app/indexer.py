"""建索引：corpus 文件 → 切塊 → bge-m3 向量化 → 存進 SQLite。

只處理有變動的文件（內容指紋不同）；corpus 裡刪掉的文件也會從索引移除。
換了 embedding 模型時整個重建（不同模型的向量不能混用）。
"""

from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np

from app.chunker import chunk_doc, load_corpus
from app.config import Settings
from app.embedder import Embedder
from app.store import Store

BATCH = 32


async def build_index(settings: Settings, store: Store, embedder: Embedder, *, rebuild: bool = False,
                      progress: Callable[[str], None] = print) -> dict:
    docs = load_corpus(settings.corpus_dir)
    if store.get_meta("embed_model") not in (None, embedder.model):
        progress(f"embedding 模型改變（{store.get_meta('embed_model')} → {embedder.model}），整個重建")
        rebuild = True
    existing = {} if rebuild else store.doc_digests()
    if rebuild:
        store.remove_docs(list(store.doc_digests()))
    todo = [d for d in docs if existing.get(d.title) != d.digest]
    gone = [t for t in existing if t not in {d.title for d in docs}]
    store.remove_docs(gone)
    total_chunks = 0
    for n, doc in enumerate(todo, 1):
        chunks = chunk_doc(doc)
        vectors = []
        for i in range(0, len(chunks), BATCH):
            vectors.append(await embedder.embed([c.embed_text() for c in chunks[i : i + BATCH]]))
        store.replace_doc(doc.title, doc.url, doc.period, doc.period_name, doc.digest, chunks,
                          np.vstack(vectors) if vectors else np.zeros((0, 1)))
        total_chunks += len(chunks)
        progress(f"[{n}/{len(todo)}] {doc.title}：{len(chunks)} 塊")
    store.set_meta("embed_model", embedder.model)
    store.set_meta("built_at", datetime.now(UTC).isoformat(timespec="seconds"))
    return {"updated": len(todo), "removed": len(gone), "chunks": total_chunks, **store.stats()}
