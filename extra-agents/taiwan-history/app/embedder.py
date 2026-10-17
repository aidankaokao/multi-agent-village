"""向量化：固定用本地 Ollama 的 bge-m3（資料與問題必須用同一個模型）。"""

import httpx
import numpy as np


class EmbedError(RuntimeError):
    pass


class Embedder:
    def __init__(self, base_url: str, model: str, timeout: float = 300.0):
        self.model = model
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def embed(self, texts: list[str]) -> np.ndarray:
        """回傳 (n, dim) 的 float32 矩陣，每列已正規化（內積 = 餘弦相似度）。"""
        try:
            r = await self._http.post("/api/embed", json={"model": self.model, "input": texts})
        except httpx.HTTPError as e:
            raise EmbedError(f"連不上 Ollama（{self._http.base_url}）：{e}") from None
        if r.status_code >= 400:
            raise EmbedError(f"Ollama 向量化失敗（HTTP {r.status_code}）：{r.text[:300]}")
        vecs = np.asarray(r.json()["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.where(norms == 0, 1, norms)

    async def aclose(self) -> None:
        await self._http.aclose()
