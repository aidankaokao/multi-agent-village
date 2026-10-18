import Phaser from "phaser";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { VillageEvent } from "../events";
import { MAP_H, MAP_W, TILE, VillageScene, type SpriteManifest, type VillagerInfo } from "./VillageScene";

export interface VillageHandle {
  dispatch(e: VillageEvent): void;
  say(id: string, text: string): void; // 讓某位村民冒一句話
}

interface Props {
  villagers: VillagerInfo[];
  onVillagerClick?: (id: string) => void;
}

export const VillageCanvas = forwardRef<VillageHandle, Props>(function VillageCanvas({ villagers, onVillagerClick }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const scene = useRef<VillageScene | null>(null);

  const latest = useRef(villagers);
  latest.current = villagers;
  const clickRef = useRef(onVillagerClick);
  clickRef.current = onVillagerClick;

  useImperativeHandle(
    ref,
    () => ({ dispatch: (e) => scene.current?.handleEvent(e), say: (id, text) => scene.current?.say(id, text) }),
    [],
  );

  useEffect(() => {
    scene.current?.syncVillagers(villagers);
  }, [villagers]);

  useEffect(() => {
    let game: Phaser.Game | undefined;
    let cancelled = false;
    fetch("/assets/sprites/manifest.json", { cache: "no-cache" })
      .then((r) => r.json())
      .then((manifest: SpriteManifest) => {
        if (cancelled || !host.current) return;
        scene.current = new VillageScene(manifest);
        scene.current.onVillagerClick = (id) => clickRef.current?.(id);
        if (import.meta.env.DEV) (window as unknown as { __village?: VillageScene }).__village = scene.current; // e2e 測試用
        scene.current.syncVillagers(latest.current, false);
        game = new Phaser.Game({
          type: Phaser.AUTO,
          parent: host.current,
          width: MAP_W * TILE,
          height: MAP_H * TILE,
          pixelArt: true,
          backgroundColor: "#6fae5c",
          // 置中交給 CSS（flex），Phaser 的 autoCenter 在 flex 容器裡會算錯偏移
          scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.NO_CENTER },
          scene: scene.current,
          // 只處理點在地圖上的滑鼠：Phaser 預設也監聽整個網頁，點彈窗 / 問答視窗時會誤判成點到後面的村民
          input: { windowEvents: false },
        });
      });
    return () => {
      cancelled = true;
      game?.destroy(true);
      scene.current = null;
    };
  }, []);

  return <div className="village-canvas" ref={host} />;
});
