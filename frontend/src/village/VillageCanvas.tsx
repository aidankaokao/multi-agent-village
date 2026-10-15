import Phaser from "phaser";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { VillageEvent } from "../events";
import { MAP_H, MAP_W, TILE, VillageScene, type SpriteManifest } from "./VillageScene";

export interface VillageHandle {
  dispatch(e: VillageEvent): void;
}

export const VillageCanvas = forwardRef<VillageHandle>(function VillageCanvas(_, ref) {
  const host = useRef<HTMLDivElement>(null);
  const scene = useRef<VillageScene | null>(null);

  useImperativeHandle(ref, () => ({ dispatch: (e) => scene.current?.handleEvent(e) }), []);

  useEffect(() => {
    let game: Phaser.Game | undefined;
    let cancelled = false;
    fetch("/assets/sprites/manifest.json")
      .then((r) => r.json())
      .then((manifest: SpriteManifest) => {
        if (cancelled || !host.current) return;
        scene.current = new VillageScene(manifest);
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
