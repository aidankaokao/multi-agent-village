import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { VillageEvent } from "../events";
import { APPRAISER, CRAFTSMAN, MAYOR, REVIEWER, SCRIBE } from "../village/layout";
import { parseTime, type Terrain } from "./common";
import { Hud, TIME_MINUTES, entry, loadPrefs, savePrefs, stamp, type LogEntry, type Prefs } from "./Hud";
import { World3D, type WorldVillager } from "./World3D";

/** App / 登入頁操作地圖的方式：事件進來、讓某位村民冒一句話 */
export interface VillageHandle {
  dispatch(e: VillageEvent): void;
  say(id: string, text: string): void;
}

export interface WorldCanvasProps {
  villagers: WorldVillager[];
  onVillagerClick?: (id: string) => void;
  hud?: boolean; // 工具列與日誌（登入頁的示範村落不要）
  shiftX?: number; // 畫面往左偏幾 px（登入頁：右邊有登入卡片）
}

interface Props extends WorldCanvasProps {
  makeTerrain: () => Terrain; // 場景（島嶼、鐵道小鎮…）
}

const MAX_LOG = 120;
const SYSTEM = [MAYOR, CRAFTSMAN, REVIEWER, SCRIBE, APPRAISER];

/** 3D 地圖：村民名單、事件進來，點了誰出去；場景由 makeTerrain 決定 */
export const WorldCanvas = forwardRef<VillageHandle, Props>(function WorldCanvas({ villagers, onVillagerClick, makeTerrain, hud = true, shiftX = 0 }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const world = useRef<World3D | null>(null);
  const latest = useRef(villagers);
  latest.current = villagers;
  const clickRef = useRef(onVillagerClick);
  clickRef.current = onVillagerClick;
  const urlTime = useRef(parseTime(new URLSearchParams(location.search).get("time"))); // 網址指定的時段（預覽用）
  const [prefs, setPrefsState] = useState<Prefs>(loadPrefs);
  const [paused, setPaused] = useState(false);
  const [bird, setBird] = useState(false);
  const [follow, setFollow] = useState(false);
  const [canFollow, setCanFollow] = useState(false);
  const [log, setLog] = useState<LogEntry[]>([]);
  const skipBird = useRef(false); // 日誌點村民時關掉鳥瞰：鏡頭飛去村民，不要飛回預設角度

  const setPrefs = (p: Prefs) => {
    if (p.time !== prefs.time) urlTime.current = null; // 在工具列選了時段，網址的就不算了
    setPrefsState(p);
    savePrefs(p);
  };

  const nameOf = (id?: string) =>
    (id && ([...SYSTEM, ...latest.current].find((v) => v.id === id)?.name ?? id)) || "某位村民";

  useImperativeHandle(
    ref,
    () => ({
      dispatch: (e: VillageEvent) => {
        const w = world.current;
        if (!w) return;
        w.handleEvent(e);
        const l = entry(e, nameOf, (id) => w.carryIcon(id));
        if (l) setLog((xs) => [l, ...xs].slice(0, MAX_LOG));
      },
      say: (id, text) => world.current?.say(id, text),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  useEffect(() => {
    world.current?.syncVillagers(villagers);
  }, [villagers]);

  useEffect(() => {
    if (!host.current) return;
    const w = new World3D(host.current, makeTerrain());
    w.onVillagerClick = (id) => clickRef.current?.(id);
    w.onLog = (icon, text, agent, kind = "info") => setLog((xs) => [stamp({ icon, text, agent, kind }), ...xs].slice(0, MAX_LOG));
    w.syncVillagers(latest.current, false);
    world.current = w;
    setCanFollow(w.canFollow);
    if (import.meta.env.DEV) (window as unknown as { __island?: World3D }).__island = w; // e2e 測試用
    return () => {
      w.dispose();
      world.current = null;
    };
  }, []);

  // 工具列的開關套到 3D 世界
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.setTime(urlTime.current ?? (prefs.time === "auto" ? null : TIME_MINUTES[prefs.time]));
    w.setFlowsVisible(prefs.flows);
    w.setLife(prefs.life);
  }, [prefs.time, prefs.flows, prefs.life]);
  useEffect(() => world.current?.setPaused(paused), [paused]);
  useEffect(() => {
    if (shiftX) world.current?.setViewShift(shiftX, 1.18);
  }, [shiftX]);
  useEffect(() => {
    if (skipBird.current) {
      skipBird.current = false;
      return;
    }
    if (bird) setFollow(false);
    world.current?.setBirdView(bird);
  }, [bird]);
  useEffect(() => {
    if (follow && bird) {
      skipBird.current = true; // 跟車時關掉鳥瞰：鏡頭飛去火車，不要飛回預設角度
      setBird(false);
    }
    world.current?.setFollow(follow);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [follow]);

  const cls = ["island-canvas", prefs.names ? "" : "hide-names", prefs.carry ? "" : "hide-carry", prefs.life ? "" : "hide-life", paused ? "paused" : ""].join(" ");
  return (
    <div className={cls}>
      <div className="island-stage" ref={host} />
      {hud && (
      <Hud
        prefs={prefs}
        setPrefs={setPrefs}
        paused={paused}
        setPaused={setPaused}
        bird={bird}
        setBird={setBird}
        canFollow={canFollow}
        follow={follow}
        setFollow={setFollow}
        log={log}
        onFocus={(id) => {
          setFollow(false); // 日誌點村民：鏡頭飛去村民，不再跟車
          if (bird) {
            skipBird.current = true;
            setBird(false);
          }
          world.current?.focus(id);
        }}
        onClearLog={() => setLog([])}
      />
      )}
    </div>
  );
});

