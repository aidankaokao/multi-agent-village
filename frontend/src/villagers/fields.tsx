import { useEffect, useState, type ReactNode } from "react";
import type { SpriteManifest } from "../village/VillageScene";

/** 欄位：標題 + 說明 + 輸入元件 */
export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

const SPLIT_INLINE = /[,，、]/;

/** 清單輸入：一般用逗號 / 頓號分隔，multiline 則一行一個。保留使用者正在打的原始文字。 */
export function ListInput({
  value,
  onChange,
  multiline = false,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  multiline?: boolean;
  placeholder?: string;
}) {
  const join = (v: string[]) => v.join(multiline ? "\n" : "、");
  const [raw, setRaw] = useState(join(value));
  const parse = (t: string) =>
    t
      .split(multiline ? "\n" : SPLIT_INLINE)
      .map((x) => x.trim())
      .filter(Boolean);
  // 外部改了值（例如匯入、還原）才同步回輸入框
  useEffect(() => {
    if (JSON.stringify(parse(raw)) !== JSON.stringify(value)) setRaw(join(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  const change = (t: string) => {
    setRaw(t);
    onChange(parse(t));
  };
  return multiline ? (
    <textarea rows={Math.max(3, value.length + 1)} value={raw} placeholder={placeholder} onChange={(e) => change(e.target.value)} />
  ) : (
    <input value={raw} placeholder={placeholder} onChange={(e) => change(e.target.value)} />
  );
}

/** 可留空的數字欄位：空白 = null */
export function NumInput({
  value,
  onChange,
  min,
  max,
  step,
  placeholder,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
}) {
  return (
    <input
      type="number"
      value={value ?? ""}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
    />
  );
}

// ---------------------------------------------------------------- sprites

let manifestPromise: Promise<SpriteManifest> | null = null;

export function useManifest(): SpriteManifest | null {
  const [m, setM] = useState<SpriteManifest | null>(null);
  useEffect(() => {
    manifestPromise ??= fetch("/assets/sprites/manifest.json").then((r) => r.json());
    manifestPromise.then(setM).catch(() => {});
  }, []);
  return m;
}

/** 角色縮圖：取 spritesheet 第一列中間那格（面向前方站立） */
export function SpriteThumb({ sprite, manifest, scale = 2 }: { sprite: string | null; manifest: SpriteManifest | null; scale?: number }) {
  const c = manifest && sprite ? manifest.characters[sprite] : undefined;
  const w = (manifest?.frameWidth ?? 32) * scale;
  const h = (manifest?.frameHeight ?? 32) * scale;
  return (
    <span
      className="sprite-thumb"
      style={{
        width: w,
        height: h,
        backgroundImage: c ? `url(/assets/sprites/${c.path})` : undefined,
        backgroundSize: `${w * 3}px ${h * 4}px`,
        backgroundPosition: `-${w}px 0`,
      }}
    />
  );
}
