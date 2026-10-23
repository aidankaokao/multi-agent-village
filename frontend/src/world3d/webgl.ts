/** 瀏覽器能不能畫 3D（WebGL）：舊電腦、關掉硬體加速時不行（App、登入頁都會先檢查） */
export function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}
