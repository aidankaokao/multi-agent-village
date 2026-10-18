import type { AgentInfo } from "../api";

/**
 * 問答附的文件（xlsx / csv / txt / pdf）：村子自己不讀內容，只交給名片上寫著「收這類檔案」的駐村專家（例如文件彙整師）。
 * 上限和後端一樣（core/docfiles.py）；這裡先擋，免得上傳了才被拒絕。
 */

export const MAX_DOCS = 3;
export const MAX_DOC_BYTES = 10 * 1024 * 1024;
export const MAX_DOCS_TOTAL = 20 * 1024 * 1024;

export interface DocFile {
  name: string;
  size: number;
  data: string; // base64（不含 data: 前綴）
}

/** 對話裡顯示的文件（剛送出時只有名字和大小；從紀錄載入時有下載網址） */
export interface DocChip {
  name: string;
  size?: number;
  url?: string;
  audio?: boolean; // 錄音（轉完就刪掉，不能下載）
  note?: string; // 例如「上傳中 45%」
}

/**
 * 錄音：交給聽打員（工具型村民，工具 typist）轉逐字稿。檔案大，先單獨上傳（有進度），問答時只帶 id。
 * 上限和後端一樣（core/audio.py）。
 */
export const AUDIO_EXTS = [".mp3", ".m4a", ".mp4", ".wav", ".webm", ".ogg", ".opus", ".flac", ".aac"];
export const MAX_AUDIO = 3;
export const MAX_AUDIO_BYTES = 200 * 1024 * 1024;

export interface AudioUpload {
  key: string;
  name: string;
  size: number;
  progress: number; // 0～1
  id?: string; // 上傳完成後的檔案 id
  abort: AbortController;
}

export const isAudio = (name: string) => AUDIO_EXTS.includes(extOf(name));

/** 負責轉寫的村民（停用時名冊裡沒有它） */
export const typistOf = (agents: AgentInfo[]) => agents.find((a) => a.tool === "typist");

export function pickAudio(files: File[], current: AudioUpload[]): { ok: File[]; error: string } {
  const ok: File[] = [];
  const errors: string[] = [];
  for (const f of files) {
    if (f.size > MAX_AUDIO_BYTES) {
      errors.push(`「${f.name}」超過每份 ${fmtSize(MAX_AUDIO_BYTES)} 的上限`);
      continue;
    }
    if (current.length + ok.length >= MAX_AUDIO) {
      errors.push(`一次最多附 ${MAX_AUDIO} 份錄音`);
      break;
    }
    ok.push(f);
  }
  return { ok, error: errors.join("；") };
}

export const extOf = (name: string) => {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
};

export const fmtSize = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/** 在村、而且收文件的駐村專家 */
export function docAgents(agents: AgentInfo[]): AgentInfo[] {
  return agents.filter((a) => a.kind === "remote" && a.status === "online" && (a.accepts?.length ?? 0) > 0);
}

export function acceptedExts(agents: AgentInfo[]): string[] {
  return [...new Set(docAgents(agents).flatMap((a) => a.accepts ?? []))];
}

export function readDoc(file: File): Promise<DocFile> {
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok({ name: file.name, size: file.size, data: String(r.result).split(",", 2)[1] ?? "" });
    r.onerror = () => fail(new Error(`「${file.name}」讀不出來`));
    r.readAsDataURL(file);
  });
}

/** 檢查要加入的文件；回傳可以加入的與錯誤訊息 */
export function pickDocs(files: File[], current: DocFile[], exts: string[]): { ok: File[]; error: string } {
  const ok: File[] = [];
  const errors: string[] = [];
  let total = current.reduce((n, d) => n + d.size, 0);
  for (const f of files) {
    if (!exts.includes(extOf(f.name))) {
      errors.push(`「${f.name}」的格式不支援（可以附 ${exts.join("、")}${extOf(f.name) === ".xls" ? "；舊版 .xls 請另存成 .xlsx" : ""}）`);
      continue;
    }
    if (f.size > MAX_DOC_BYTES) {
      errors.push(`「${f.name}」超過每份 ${fmtSize(MAX_DOC_BYTES)} 的上限`);
      continue;
    }
    if (current.length + ok.length >= MAX_DOCS) {
      errors.push(`一次最多附 ${MAX_DOCS} 份文件`);
      break;
    }
    if (total + f.size > MAX_DOCS_TOTAL) {
      errors.push(`文件合計超過 ${fmtSize(MAX_DOCS_TOTAL)} 的上限`);
      break;
    }
    total += f.size;
    ok.push(f);
  }
  return { ok, error: errors.join("；") };
}

/** 下載（帶登入 cookie）；過期或找不到時丟出錯誤，不會下載到一個錯誤頁 */
export async function download(url: string, name: string): Promise<void> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 404) throw new Error("檔案已過期或不存在（報告保存 14 天）");
  if (!r.ok) throw new Error(`下載失敗（HTTP ${r.status}）`);
  const href = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
