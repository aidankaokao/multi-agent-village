import { forwardRef } from "react";
import { WorldCanvas, type VillageHandle, type WorldCanvasProps } from "../world3d/WorldCanvas";
import { RailTerrain } from "./RailTerrain";

const makeTerrain = () => new RailTerrain();

/** 鐵道小鎮（App、登入頁用 React.lazy 載入，Three.js 不放進主程式） */
const RailCanvas = forwardRef<VillageHandle, WorldCanvasProps>(function RailCanvas(props, ref) {
  return <WorldCanvas ref={ref} {...props} makeTerrain={makeTerrain} />;
});

export default RailCanvas;
