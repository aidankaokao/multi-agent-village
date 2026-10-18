import DOMPurify from "dompurify";
import { marked } from "marked";
import { useMemo } from "react";

/**
 * 把村民 / 駐村專家的回答（常常是 Markdown）顯示成格式化的內容。
 * LLM 的輸出不可信：轉成 HTML 後一律經過 DOMPurify 過濾；不顯示圖片（避免偷偷載入外部網址）、連結一律開新分頁。
 * 單一換行保留成換行（breaks），詩的每一句才不會被接成一行。
 */

marked.setOptions({ gfm: true, breaks: true });

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

const PURIFY = {
  FORBID_TAGS: ["img", "style", "iframe", "form", "input", "button", "video", "audio", "source", "svg", "math"],
  FORBID_ATTR: ["style", "class", "id"],
};

export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html, PURIFY);
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return <div className={`md ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
