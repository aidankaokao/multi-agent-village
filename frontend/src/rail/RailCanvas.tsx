import { forwardRef } from "react";
import type { VillageHandle } from "../village/VillageCanvas";
import { WorldCanvas, type WorldCanvasProps } from "../world3d/WorldCanvas";
import { RailTerrain } from "./RailTerrain";

const makeTerrain = () => new RailTerrain();

/** 鐵道小鎮（App 用 React.lazy 載入）；目前用網址 ?view=rail 預覽 */
const RailCanvas = forwardRef<VillageHandle, WorldCanvasProps>(function RailCanvas(props, ref) {
  return <WorldCanvas ref={ref} {...props} makeTerrain={makeTerrain} />;
});

export default RailCanvas;
