/** 介面風格：預設柔和，帳號選單可以換回像素（登入頁照同一個設定；localStorage ui-theme） */
export type UiTheme = "pixel" | "soft";

export function initialTheme(): UiTheme {
  try {
    return localStorage.getItem("ui-theme") === "pixel" ? "pixel" : "soft";
  } catch {
    return "soft";
  }
}

export const THEME_LABEL: Record<UiTheme, string> = { soft: "柔和", pixel: "像素" };
