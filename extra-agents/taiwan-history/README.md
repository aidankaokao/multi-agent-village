# 台灣史專家（獨立 agent）

以公開資料（中文維基百科等）建立的台灣史問答 agent：檢索相關段落、由 LLM 整理回答，並附上引用來源。
獨立部署，有自己的簡易問答網頁可以單獨測試；完成後透過 HTTP（A2A 協定）接進 Multi-Agent Village 當作「外地來的村民」。

規劃與進度見 [PLAN.md](PLAN.md)。

## 資料

- 條目清單：`corpus/sources.yaml`（依時期分類，可增減）
- 整理好的文件：`corpus/docs/<時期>/<標題>.md`，開頭記錄來源網址、版本、抓取日期與授權
- 內容來自中文維基百科，授權 **CC BY-SA 4.0**：使用時標示出處（每篇的網址），衍生內容以相同授權分享

需要 Python 3.12+ 與 [uv](https://docs.astral.sh/uv/)：

```bash
cd extra-agents/taiwan-history
uv sync

uv run python scripts/fetch_wiki.py              # 抓還沒抓過的條目
uv run python scripts/fetch_wiki.py --refresh    # 全部重新抓（維基內容更新時）
uv run python scripts/fetch_wiki.py --only 霧社事件 二二八事件
uv run python scripts/corpus_stats.py            # 各時期篇數、字數與品質檢查

uv run pytest                                     # 測試
```
