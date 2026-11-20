import * as L from "leaflet";
import geography from "../../../../../data/public/montreal-backdrop-v1/backdrop.json";
import { layoutMapLabels, type LabelBox } from "./mapLabelLayout.ts";

export function richBackdrop(map: L.Map) {
  const roads = L.layerGroup(),
    labels = L.layerGroup();
  const renderer = L.canvas({ pane: "street-context" });
  const toLatLng = (points: number[][]): L.LatLngTuple[] =>
    points.map((p) => [p[1]!, p[0]!]);
  for (const [kind, color] of [
    ["water", "#b4ddeb"],
    ["parks", "#d7ead3"],
  ] as const) {
    for (const [points] of geography.context[kind]) {
      L.polygon(toLatLng(points as number[][]), {
        renderer,
        pane: "street-context",
        color,
        fillColor: color,
        fillOpacity: 1,
        weight: 0,
        interactive: false,
      }).addTo(roads);
    }
  }
  const candidates: {
    name: string;
    at: L.LatLngTuple;
    place: boolean;
    minimumZoom: number;
    priority: number;
  }[] = [];
  for (const road of geography.roads) {
    const [points, kind, name] = road as [number[][], string, string];
    if (points.length < 2) continue;
    const major = ["primary", "secondary", "tertiary", "trunk"].includes(kind),
      width = major ? 5 : 2.2;
    const coords = toLatLng(points);
    L.polyline(coords, {
      renderer,
      pane: "street-context",
      color: major ? "#dedcd2" : "#dfe3e7",
      weight: width + 1.3,
      interactive: false,
    }).addTo(roads);
    L.polyline(coords, {
      renderer,
      pane: "street-context",
      color: major ? "#fffdf5" : "#ffffff",
      weight: width,
      interactive: false,
    }).addTo(roads);
    if (major && name) {
      candidates.push({
        name,
        at: coords[Math.floor(coords.length / 2)]!,
        place: false,
        minimumZoom: kind === "tertiary" ? 15 : kind === "secondary" ? 14 : 13,
        priority: kind === "tertiary" ? 3 : kind === "secondary" ? 2 : 1,
      });
    }
  }
  for (const [name, lon, lat] of geography.context.places)
    candidates.push({
      name: String(name),
      at: [Number(lat), Number(lon)],
      place: true,
      minimumZoom: 10,
      priority: 0,
    });
  const measure = document.createElement("canvas").getContext("2d")!;
  let previousView = "";
  function drawLabels() {
    if (!map.hasLayer(labels)) return;
    const size = map.getSize(),
      center = map.getCenter(),
      zoom = map.getZoom();
    const view = `${center.lat},${center.lng},${zoom},${size.x},${size.y}`;
    if (view === previousView) return;
    previousView = view;
    labels.clearLayers();
    const host = map.getContainer(),
      frame = host.getBoundingClientRect();
    const obstacles: LabelBox[] = [
      ...host.querySelectorAll(".leaflet-control, .vehicle-pin, .bus-label"),
      ...(host.closest(".map-card")?.querySelectorAll(".map-tools") ?? []),
    ].map((el) => {
      const rect = el.getBoundingClientRect();
      return {
        left: rect.left - frame.left,
        top: rect.top - frame.top,
        right: rect.right - frame.left,
        bottom: rect.bottom - frame.top,
      };
    });
    const positioned = candidates
      .filter((c) => zoom >= c.minimumZoom)
      .map((c) => {
        const at = map.latLngToContainerPoint(c.at);
        measure.font = c.place
          ? "600 13px Inter, sans-serif"
          : "450 10px Inter, sans-serif";
        return {
          ...c,
          key: `${c.place ? "place" : "street"}:${c.name}`,
          x: at.x,
          y: at.y,
          width: Math.ceil(measure.measureText(c.name).width) + 8,
          height: c.place ? 22 : 18,
        };
      });
    for (const c of layoutMapLabels(positioned, size.x, size.y, obstacles))
      addLabel(c.name, c.at, c.place, c.width, c.height);
  }
  function addLabel(
    name: string,
    at: L.LatLngTuple,
    place: boolean,
    width: number,
    height: number,
  ) {
    const span = document.createElement("span");
    span.textContent = name;
    L.marker(at, {
      pane: "street-labels",
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: place
          ? "place-label rich-map-label"
          : "street-label rich-map-label rich-street-label",
        html: span,
        iconSize: [width, height],
        iconAnchor: [width / 2, height / 2],
      }),
    }).addTo(labels);
  }
  labels.on("add", () => {
    previousView = "";
    map.on("moveend zoomend resize", drawLabels);
    drawLabels();
    void document.fonts.ready.then(() => {
      previousView = "";
      drawLabels();
    });
  });
  labels.on("remove", () => {
    map.off("moveend zoomend resize", drawLabels);
    labels.clearLayers();
    previousView = "";
  });
  return { roads, labels };
}
