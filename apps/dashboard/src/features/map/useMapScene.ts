import { useEffect, useRef } from "react";
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import { routeLayers } from "./routeLayers.js";
import { pathSegments } from "./mapModel.ts";
import type { ChargingMarker, Fix, MapContext } from "./mapModel.ts";

const chargingPins = {
  preferred: { color: "#991d63", text: "●" },
  fallback: { color: "#5e42a3", text: "◇" },
  approved: { color: "#087268", text: "ϟ" },
  reserve: { color: "#a13e23", text: "!" },
  review: { color: "#991d63", text: "◎" },
};
const noChargingMarkers: ChargingMarker[] = [];
export function useMapScene(
  context: MapContext | null,
  points: Fix[],
  maxGap: number,
  position: { coordinates: [number, number]; freshness: string } | null,
  layers: string[],
  follow: boolean,
  charging = noChargingMarkers,
  isCharging = false,
  initialView?: { coordinates: [number, number]; zoom: number },
) {
  const host = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    dynamic = useRef<L.LayerGroup | null>(null);
  const startingView = useRef(initialView);
  startingView.current = initialView;
  const last = points.filter((p) => p.coordinates).at(-1);
  useEffect(() => {
    if (!host.current || !context) return;
    const own = L.map(host.current, {
      scrollWheelZoom: false,
      attributionControl: true,
      zoomControl: false,
      zoomAnimation: false,
      minZoom: 3,
      maxZoom: 20,
    });
    map.current = own;
    own.attributionControl.setPrefix("DriveTwin · local map");
    L.control.zoom({ position: "topright" }).addTo(own);
    L.control.scale({ imperial: false, position: "bottomleft" }).addTo(own);
    own.attributionControl.addAttribution(
      '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors · ODbL-1.0</a>',
    );
    dynamic.current = L.layerGroup().addTo(own);
    own.createPane("street-context").style.zIndex = "210";
    own.createPane("street-labels").style.zIndex = "350";
    own.createPane("charging-detours").style.zIndex = "450";
    own.createPane("route-points").style.zIndex = "480";
    const routeBounds = L.geoJSON(
      context.features.filter((f) => f.properties.layer === "route"),
    ).getBounds();
    const camera = startingView.current;
    if (camera)
      own.setView([camera.coordinates[1], camera.coordinates[0]], camera.zoom);
    else
      own.fitBounds(
        routeBounds.isValid()
          ? routeBounds
          : L.latLngBounds([
              [context.bbox[1], context.bbox[0]],
              [context.bbox[3], context.bbox[2]],
            ]),
        { padding: [35, 35], maxZoom: 16 },
      );
    const observer = new ResizeObserver(() => own.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      own.remove();
      map.current = null;
      dynamic.current = null;
    };
    // Camera initialization and the layer effects share the context lifecycle.
    // Subsequent movement uses the position/follow effect below.
  }, [context]);
  useEffect(() => {
    const own = map.current;
    if (!own || !context) return;
    const montreal =
      context.bbox[0] >= -73.65 &&
      context.bbox[2] <= -73.49 &&
      context.bbox[1] >= 45.45 &&
      context.bbox[3] <= 45.59;
    let active = true;
    let basemap:
      | ReturnType<(typeof import("./richBackdrop.ts"))["richBackdrop"]>
      | undefined;
    const { group, streets } = routeLayers(L, own, context, layers, {
      dashed: true,
      streetLabels: layers.includes("labels") && !montreal,
    });
    if (montreal && (layers.includes("roads") || layers.includes("labels"))) {
      void import("./richBackdrop.ts")
        .then(({ richBackdrop }) => {
          if (!active) return;
          basemap = richBackdrop(own);
          if (layers.includes("roads")) basemap.roads.addTo(own);
          if (layers.includes("labels")) basemap.labels.addTo(own);
        })
        .catch(() => {
          // The independently loaded mission streets remain usable.
        });
    }
    return () => {
      active = false;
      own.removeLayer(group);
      own.removeLayer(streets);
      if (basemap) {
        own.removeLayer(basemap.roads);
        own.removeLayer(basemap.labels);
      }
    };
  }, [context, layers]);
  useEffect(() => {
    const own = map.current;
    if (!own || !context || !layers.includes("stations") || !charging.length)
      return;
    const group = L.layerGroup().addTo(own);
    for (const point of charging) {
      const pin = chargingPins[point.kind];
      const icon = document.createElement("span");
      icon.className = "charging-map-pin";
      icon.style.backgroundColor = pin.color;
      icon.textContent = pin.text;
      const marker = L.marker([point.coordinates[1], point.coordinates[0]], {
        alt: point.label,
        icon: L.divIcon({
          className: "",
          html: icon,
          iconSize: [25, 25],
          iconAnchor: [12, 12],
        }),
      }).addTo(group);
      const tooltip = document.createElement("span");
      tooltip.textContent = point.label;
      marker.bindTooltip(tooltip);
      marker.getElement()?.setAttribute("aria-label", point.label);
      marker.getElement()?.setAttribute("data-charging-marker", point.kind);
    }
    return () => {
      own.removeLayer(group);
    };
  }, [charging, context, layers]);
  const detourBay = charging.find(({ kind }) => kind === "approved")?.bay;
  useEffect(() => {
    const own = map.current;
    if (!own || !context || !detourBay || !layers.includes("route")) return;
    const detour = L.geoJSON(
      context.features.filter(
        ({ properties }) =>
          properties.layer === "detour" && properties.id === detourBay,
      ),
      {
        pane: "charging-detours",
        style: { color: "#067e88", weight: 6, opacity: 0.95, dashArray: "4 7" },
        interactive: false,
        onEachFeature: (_feature, layer) => {
          layer.on("add", () =>
            (layer as L.Path)
              .getElement()
              ?.setAttribute("data-map-layer", "detour"),
          );
        },
      },
    ).addTo(own);
    return () => {
      own.removeLayer(detour);
    };
  }, [context, detourBay, layers]);
  useEffect(() => {
    const own = map.current,
      group = dynamic.current;
    if (!own || !group) return;
    group.clearLayers();
    for (const segment of layers.includes("trajectory")
      ? pathSegments(points, maxGap)
      : []) {
      L.polyline(
        segment.map((p) => [p.coordinates![1], p.coordinates![0]]),
        { color: "#d9277f", weight: 4.5, opacity: 0.95 },
      ).addTo(group);
    }
    const fix = position?.coordinates ?? last?.coordinates;
    if (fix) {
      const freshness = position?.freshness ?? "last valid";
      const chargingNow =
        isCharging && (freshness === "fresh" || freshness === "recorded");
      const marker = L.marker([fix[1], fix[0]], {
        zIndexOffset: chargingNow ? 1000 : 0,
        icon: L.divIcon({
          className: `vehicle-pin ${freshness === "fresh" ? "current" : "historical"}${chargingNow ? " is-charging" : ""}`,
          iconSize: [38, 38],
          iconAnchor: [19, 19],
          html: chargingNow
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m14 2-10 12h7l-1 8 10-12h-7z"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="5" y="3" width="14" height="16" rx="3"/><path d="M5 10h14M8 6h8M8 14h1m6 0h1M7 19v2m10-2v2"/></svg>',
        }),
      }).addTo(group);
      const label = document.createElement("span");
      label.textContent = `${context?.asset_id ?? "Vehicle"} · ${chargingNow ? (freshness === "recorded" ? "Recorded charging" : "Charging") : freshness}`;
      marker.bindTooltip(label, {
        permanent: true,
        direction: "top",
        offset: [0, -20],
        className: "bus-label",
      });
      const element = marker.getElement();
      element?.setAttribute("data-testid", "vehicle-position");
      element?.setAttribute(
        "aria-label",
        chargingNow
          ? freshness === "recorded"
            ? "Recorded vehicle charging at this position"
            : "Vehicle charging at this position"
          : `Vehicle position ${freshness}`,
      );
      if (follow && position) own.panTo([fix[1], fix[0]], { animate: false });
    }
  }, [
    context,
    layers,
    points,
    maxGap,
    position?.coordinates[0],
    position?.coordinates[1],
    position?.freshness,
    follow,
    isCharging,
  ]);
  return { host, map };
}
