import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { VillageHandle } from "../village/VillageCanvas";
import { IslandWorld, type IslandVillager } from "./IslandWorld";

interface Props {
  villagers: IslandVillager[];
  onVillagerClick?: (id: string) => void;
}

/** 島嶼版地圖：介面和像素版的 VillageCanvas 一樣（村民名單、事件進來，點了誰出去），App 選要用哪一個 */
const IslandCanvas = forwardRef<VillageHandle, Props>(function IslandCanvas({ villagers, onVillagerClick }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const world = useRef<IslandWorld | null>(null);
  const latest = useRef(villagers);
  latest.current = villagers;
  const clickRef = useRef(onVillagerClick);
  clickRef.current = onVillagerClick;

  useImperativeHandle(
    ref,
    () => ({ dispatch: (e) => world.current?.handleEvent(e), say: (id, text) => world.current?.say(id, text) }),
    [],
  );

  useEffect(() => {
    world.current?.syncVillagers(villagers);
  }, [villagers]);

  useEffect(() => {
    if (!host.current) return;
    const w = new IslandWorld(host.current);
    w.onVillagerClick = (id) => clickRef.current?.(id);
    w.syncVillagers(latest.current, false);
    world.current = w;
    if (import.meta.env.DEV) (window as unknown as { __island?: IslandWorld }).__island = w; // e2e 測試用
    return () => {
      w.dispose();
      world.current = null;
    };
  }, []);

  return <div className="island-canvas" ref={host} />;
});

export default IslandCanvas;
