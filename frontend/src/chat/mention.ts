import type { AgentInfo } from "../api";

/**
 * 輸入框的 @ 自動完成：游標前面正在打的「@xxx」→ 村民清單 → 選了換成「@id 」。
 * 只有在開頭、空白或標點後面的 @ 才算（email 的 a@b 不算）；可以用英文 id 或中文名字篩選。
 */

export interface MentionQuery {
  start: number; // @ 的位置
  end: number; // 游標位置
  query: string;
}

const BEFORE_AT = /(^|[\s，、。！？,.;；:：→>＞（(「])@([\w㐀-鿿-]*)$/;

export function findMention(text: string, caret: number): MentionQuery | null {
  const m = BEFORE_AT.exec(text.slice(0, caret));
  if (!m) return null;
  const query = m[2];
  return { start: caret - query.length - 1, end: caret, query };
}

export function matchAgents(agents: AgentInfo[], query: string): AgentInfo[] {
  const q = query.toLowerCase();
  const hit = agents.filter((a) => !q || a.id.toLowerCase().includes(q) || a.name.toLowerCase().includes(q));
  // id 或名字開頭就符合的排前面；外出中的駐村專家排最後
  const rank = (a: AgentInfo) =>
    (a.kind === "remote" && a.status !== "online" ? 10 : 0) + (a.id.toLowerCase().startsWith(q) || a.name.startsWith(query) ? 0 : 1);
  return hit.sort((a, b) => rank(a) - rank(b));
}

export function insertMention(text: string, m: MentionQuery, id: string): { text: string; caret: number } {
  const after = text.slice(m.end).replace(/^[\w-]*/, ""); // 游標後面如果還連著半個 id 一起換掉
  const insert = `@${id} `;
  const rest = after.startsWith(" ") ? after.slice(1) : after;
  return { text: text.slice(0, m.start) + insert + rest, caret: m.start + insert.length };
}

export const isAway = (a: AgentInfo) => a.kind === "remote" && a.status !== "online";
