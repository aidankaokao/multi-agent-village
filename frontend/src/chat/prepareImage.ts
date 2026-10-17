/**
 * 附圖前處理：縮圖（長邊 ≤ 1568px，視覺模型再大也看不出差別，只會多花 token）並重新編碼成 JPEG。
 * 重新畫到 canvas 再輸出，照片裡的 GPS、拍攝時間等 EXIF 資訊就不會跟著送出去；
 * createImageBitmap 會依 EXIF 轉正方向（手機直拍的照片不會變橫的）。
 */

const MAX_SIDE = 1568;
const MAX_INPUT = 25 * 1024 * 1024; // 原始檔上限（還沒縮圖前）
const QUALITY = 0.88;

export async function prepareImage(file: File): Promise<string> {
  if (!file.type.startsWith("image/")) throw new Error(`「${file.name}」不是圖片`);
  if (file.size > MAX_INPUT) throw new Error(`「${file.name}」太大（上限 25MB）`);
  let bmp: ImageBitmap;
  try {
    bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new Error(`「${file.name}」讀不出來（瀏覽器不支援這種圖片格式，例如 HEIC 請先轉成 JPEG）`);
  }
  const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  const w = Math.max(1, Math.round(bmp.width * scale));
  const h = Math.max(1, Math.round(bmp.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext("2d")!;
  g.fillStyle = "#fff"; // 透明背景（PNG 截圖）轉 JPEG 時填白色，不要變黑
  g.fillRect(0, 0, w, h);
  g.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  return canvas.toDataURL("image/jpeg", QUALITY);
}

/** 從貼上 / 拖曳的資料裡挑出圖片檔 */
export function imageFiles(list: DataTransferItemList | FileList | null | undefined): File[] {
  if (!list) return [];
  const files: File[] = [];
  for (const item of Array.from(list as ArrayLike<DataTransferItem | File>)) {
    const f = item instanceof File ? item : item.kind === "file" ? item.getAsFile() : null;
    if (f && f.type.startsWith("image/")) files.push(f);
  }
  return files;
}
