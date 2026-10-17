"""索引：SQLite 一個檔案（chunks 表 + 向量 BLOB），查詢時整批載入記憶體。

資料量小（幾千塊 × 1024 維 ≈ 十幾 MB），直接用 numpy 算內積就夠快，不需要另外的向量資料庫。
檢索 = 語意（向量）＋ 關鍵字（中文兩字一組的 bigram，人名、年代、專有名詞比較準）→ 用名次融合（RRF）合併。
"""

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.chunker import Chunk

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    title TEXT PRIMARY KEY, url TEXT, period TEXT, period_name TEXT, digest TEXT, chunks INTEGER
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, doc_title TEXT REFERENCES docs(title) ON DELETE CASCADE,
    url TEXT, period TEXT, period_name TEXT, section TEXT, text TEXT, ordinal INTEGER, vector BLOB
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_title);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

RRF_K = 60
CANDIDATES = 40  # 兩種檢索各取前幾名再融合

_CJK = re.compile(r"[㐀-鿿]+")
_WORD = re.compile(r"[A-Za-z]+|\d+")


def terms(text: str) -> list[str]:
    """關鍵字詞：中文取兩字一組，英文單字與數字（年份）整個取。"""
    out: list[str] = []
    for run in _CJK.findall(text):
        out += [run[i : i + 2] for i in range(len(run) - 1)] or [run]
    out += [w.lower() for w in _WORD.findall(text)]
    return out


@dataclass
class Hit:
    id: int
    doc_title: str
    url: str
    period_name: str
    section: str
    text: str
    score: float
    vector_rank: int | None = None
    keyword_rank: int | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.doc_title, "url": self.url, "period": self.period_name, "section": self.section,
            "text": self.text, "score": round(self.score, 4),
        }


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._loaded = False

    # ---------------------------------------------------------------- 寫入（建索引）

    def doc_digests(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT title, digest FROM docs"))

    def replace_doc(self, title: str, url: str, period: str, period_name: str, digest: str,
                    chunks: list[Chunk], vectors: np.ndarray) -> None:
        with self.db:
            self.db.execute("DELETE FROM docs WHERE title = ?", (title,))
            self.db.execute("INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?)", (title, url, period, period_name, digest, len(chunks)))
            self.db.executemany(
                "INSERT INTO chunks (doc_title, url, period, period_name, section, text, ordinal, vector) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(c.doc_title, c.url, c.period, c.period_name, c.section, c.text, c.ordinal, v.astype(np.float32).tobytes())
                 for c, v in zip(chunks, vectors, strict=True)],
            )
        self._loaded = False

    def remove_docs(self, titles: list[str]) -> None:
        with self.db:
            self.db.executemany("DELETE FROM docs WHERE title = ?", [(t,) for t in titles])
        self._loaded = False

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def stats(self) -> dict:
        docs, chunks = self.db.execute("SELECT (SELECT count(*) FROM docs), (SELECT count(*) FROM chunks)").fetchone()
        return {"docs": docs, "chunks": chunks, "embed_model": self.get_meta("embed_model"), "built_at": self.get_meta("built_at")}

    # ---------------------------------------------------------------- 讀取（檢索）

    def _load(self) -> None:
        rows = self.db.execute(
            "SELECT id, doc_title, url, period_name, section, text, vector FROM chunks ORDER BY id"
        ).fetchall()
        self._rows = [r[:6] for r in rows]
        self._matrix = (np.vstack([np.frombuffer(r[6], dtype=np.float32) for r in rows])
                        if rows else np.zeros((0, 1), dtype=np.float32))
        # 關鍵字：每塊的詞頻與文件頻率（算 idf）
        self._tf = [Counter(terms(f"{r[4]} {r[5]}")) for r in rows]
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = max(len(rows), 1)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self._loaded = True

    def _keyword_scores(self, query: str) -> np.ndarray:
        q = set(terms(query))
        scores = np.zeros(len(self._rows), dtype=np.float32)
        for i, tf in enumerate(self._tf):
            s = 0.0
            for t in q:
                if c := tf.get(t):
                    s += self._idf[t] * (1 + math.log(c))
            scores[i] = s
        return scores

    def search(self, query: str, query_vector: np.ndarray, k: int = 8) -> list[Hit]:
        if not self._loaded:
            self._load()
        if not self._rows:
            return []
        vec_scores = self._matrix @ query_vector.astype(np.float32)
        kw_scores = self._keyword_scores(query)
        vec_rank = np.argsort(-vec_scores)[:CANDIDATES]
        kw_rank = [i for i in np.argsort(-kw_scores)[:CANDIDATES] if kw_scores[i] > 0]
        fused: dict[int, float] = {}
        ranks: dict[int, list[int | None]] = {}
        for r, i in enumerate(vec_rank):
            fused[int(i)] = fused.get(int(i), 0) + 1 / (RRF_K + r + 1)
            ranks.setdefault(int(i), [None, None])[0] = r + 1
        for r, i in enumerate(kw_rank):
            fused[int(i)] = fused.get(int(i), 0) + 1 / (RRF_K + r + 1)
            ranks.setdefault(int(i), [None, None])[1] = r + 1
        best = sorted(fused, key=lambda i: -fused[i])[:k]
        return [
            Hit(*self._rows[i], score=fused[i], vector_rank=ranks[i][0], keyword_rank=ranks[i][1])
            for i in best
        ]

    def close(self) -> None:
        self.db.close()
