import { forwardRef } from "react";
import type { VillageHandle } from "../village/VillageCanvas";
import { WorldCanvas, type WorldCanvasProps } from "../world3d/WorldCanvas";
import { IslandTerrain } from "./IslandTerrain";

const makeTerrain = () => new IslandTerrain();

/** 島嶼版地圖（App 用 React.lazy 載入，切到才下載 Three.js） */
const IslandCanvas = forwardRef<VillageHandle, WorldCanvasProps>(function IslandCanvas(props, ref) {
  return <WorldCanvas ref={ref} {...props} makeTerrain={makeTerrain} />;
});

export default IslandCanvas;
