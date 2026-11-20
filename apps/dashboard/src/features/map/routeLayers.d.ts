import type * as Leaflet from "leaflet";
import type { MapContext } from "./mapModel.ts";

export function routeLayers(
  leaflet: typeof Leaflet,
  map: Leaflet.Map,
  context: Pick<MapContext, "features">,
  visible: string[],
  options?: { streetLabels?: boolean; dashed?: boolean },
): {
  group: Leaflet.LayerGroup;
  route: Leaflet.GeoJSON;
  streets: Leaflet.Canvas;
};
