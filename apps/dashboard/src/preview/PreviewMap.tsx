import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import roadXml from "../../../../data/public/montreal-core-v1/roads.osm.xml?raw";
import { Icon } from "../ui/Icon.tsx";
import { number, vehicleName } from "./data.ts";
import type { Vehicle } from "./data.ts";

import { route, routePosition } from "./route.ts";
import type { Position } from "./route.ts";
const stops = [route[0]!, route[2]!, route[4]!, route[7]!];
const stations: Position[] = [
  [45.5081, -73.5753],
  [45.52, -73.5658],
];
const markerIcon = (className: string, content: string) =>
  L.divIcon({
    className,
    html: content,
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });

export function PreviewMap({
  time,
  vehicle,
  stale,
  available = true,
  compact = false,
}: {
  time: number;
  vehicle: Vehicle;
  stale: boolean;
  available?: boolean;
  compact?: boolean;
}) {
  const element = useRef<HTMLDivElement>(null),
    instance = useRef<L.Map | null>(null);
  const layersRef = useRef<Record<string, L.Layer>>({}),
    busRef = useRef<L.Marker | null>(null),
    traceRef = useRef<L.Polyline | null>(null);
  const [layers, setLayers] = useState({
    trajectory: true,
    route: true,
    stops: true,
    stations: true,
    labels: true,
  });
  const [follow, setFollow] = useState(false);
  const [zoom, setZoom] = useState(14);
  useEffect(() => {
    if (!element.current) return;
    const map = L.map(element.current, {
      zoomControl: false,
      attributionControl: true,
      minZoom: 13,
      maxZoom: 18,
      maxBounds: [
        [45.492, -73.6],
        [45.537, -73.54],
      ],
      maxBoundsViscosity: 0.8,
    });
    instance.current = map;
    map.fitBounds(L.latLngBounds(route), { padding: [65, 65] });
    L.control.zoom({ position: "topright" }).addTo(map);
    map.attributionControl.setPrefix(false);
    map.attributionControl.addAttribution(
      '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a> · Sample route',
    );
    map.createPane("land").style.zIndex = "200";
    map.createPane("roads").style.zIndex = "300";
    map.createPane("labels").style.zIndex = "350";
    // Approximate contextual areas for the design preview; road coordinates come from the reviewed local input.
    const parks: Position[][] = [
      [
        [45.5165, -73.582],
        [45.5225, -73.59],
        [45.523, -73.578],
        [45.5205, -73.576],
      ],
      [
        [45.5177, -73.57],
        [45.519, -73.5687],
        [45.5184, -73.5671],
        [45.5171, -73.5683],
      ],
      [
        [45.5095, -73.5724],
        [45.5109, -73.5712],
        [45.5102, -73.5698],
        [45.5088, -73.571],
      ],
    ];
    parks.forEach((shape) =>
      L.polygon(shape, {
        pane: "land",
        stroke: false,
        fillColor: "#d9e7cf",
        fillOpacity: 0.9,
      }).addTo(map),
    );
    L.polygon(
      [
        [45.492, -73.56],
        [45.505, -73.548],
        [45.516, -73.54],
        [45.492, -73.54],
      ],
      { pane: "land", stroke: false, fillColor: "#bcdde9", fillOpacity: 1 },
    ).addTo(map);
    const xml = new DOMParser().parseFromString(roadXml, "text/xml");
    const nodes = new Map(
      Array.from(xml.querySelectorAll("node")).map((n) => [
        n.getAttribute("id")!,
        [
          Number(n.getAttribute("lat")),
          Number(n.getAttribute("lon")),
        ] as Position,
      ]),
    );
    const labelsLayer = L.layerGroup(),
      named = new Set<string>();
    for (const way of Array.from(xml.querySelectorAll("way"))) {
      const tags = Object.fromEntries(
        Array.from(way.querySelectorAll("tag")).map((t) => [
          t.getAttribute("k"),
          t.getAttribute("v"),
        ]),
      );
      const coords = Array.from(way.querySelectorAll("nd")).flatMap((n) => {
        const p = nodes.get(n.getAttribute("ref")!);
        return p ? [p] : [];
      });
      if (coords.length < 2) continue;
      const major = ["primary", "secondary", "tertiary"].includes(
        tags.highway ?? "",
      );
      const width = major ? 5 : 2.5;
      L.polyline(coords, {
        pane: "roads",
        color: major ? "#d9d9d2" : "#e1e3e7",
        weight: width + 2,
        opacity: 1,
        interactive: false,
      }).addTo(map);
      L.polyline(coords, {
        pane: "roads",
        color: major ? "#fffdf6" : "#ffffff",
        weight: width,
        opacity: 1,
        interactive: false,
      }).addTo(map);
      if (major && tags.name && !named.has(tags.name) && coords.length > 3) {
        named.add(tags.name);
        const span = document.createElement("span");
        span.textContent = tags.name;
        L.marker(coords[Math.floor(coords.length / 2)]!, {
          pane: "labels",
          interactive: false,
          keyboard: false,
          icon: L.divIcon({
            className: "street-label",
            html: span,
            iconSize: [140, 20],
          }),
        }).addTo(labelsLayer);
      }
    }
    const districts: [Position, string][] = [
      [[45.5148, -73.558], "QUARTIER LATIN"],
      [[45.509, -73.579], "CENTRE-VILLE"],
      [[45.521, -73.577], "LE PLATEAU"],
      [[45.516, -73.579], "MONTRÉAL"],
    ];
    districts.forEach(([point, text]) =>
      L.marker(point, {
        pane: "labels",
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: "district-label",
          html: text,
          iconSize: [150, 24],
        }),
      }).addTo(labelsLayer),
    );
    const planned = L.polyline(route, {
      color: "#8b67c9",
      weight: 4,
      dashArray: "8 9",
      opacity: 0.75,
    }).addTo(map);
    const trace = L.polyline([], {
      color: "#d9277f",
      weight: 5,
      lineCap: "round",
    }).addTo(map);
    traceRef.current = trace;
    const stopLayer = L.layerGroup(
      stops.map((point, i) =>
        L.circleMarker(point, {
          radius: 6,
          color: "#8b67c9",
          fillColor: "white",
          fillOpacity: 1,
          weight: 2,
        }).bindPopup(`Stop ${i + 1}`),
      ),
    ).addTo(map);
    const stationLayer = L.layerGroup(
      stations.map((point, i) =>
        L.marker(point, {
          icon: markerIcon("charger-marker", `<span>${i ? "B" : "A"}</span>`),
        }).bindPopup(
          `Station ${i ? "B" : "A"} · Charging bay<br>150 kW · Availability assumed`,
        ),
      ),
    ).addTo(map);
    labelsLayer.addTo(map);
    layersRef.current = {
      trajectory: trace,
      route: planned,
      stops: stopLayer,
      stations: stationLayer,
      labels: labelsLayer,
    };
    busRef.current = L.marker(route[0]!, {
      icon: markerIcon("bus-marker", "<span>▣</span>"),
      zIndexOffset: 900,
    }).addTo(map);
    map.on("zoomend", () => setZoom(map.getZoom()));
    const observer = new ResizeObserver(() => {
      map.invalidateSize();
      if (!map.getContainer().dataset.framed) {
        map.fitBounds(L.latLngBounds(route), { padding: [65, 65] });
        map.getContainer().dataset.framed = "true";
      }
    });
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      map.remove();
      instance.current = null;
    };
  }, []);
  useEffect(() => {
    const map = instance.current;
    if (!map) return;
    Object.entries(layers).forEach(([key, on]) => {
      const layer = layersRef.current[key];
      if (layer) {
        if (on) map.addLayer(layer);
        else map.removeLayer(layer);
      }
    });
  }, [layers]);
  useEffect(() => {
    const position = routePosition(time);
    const map = instance.current,
      marker = busRef.current;
    if (map && marker) {
      if (available) map.addLayer(marker);
      else {
        marker.unbindTooltip();
        map.removeLayer(marker);
      }
    }
    if (available)
      busRef.current
        ?.setLatLng(position)
        .bindTooltip(
          `${vehicleName(vehicle)}${stale ? " · Last valid position" : ""}`,
          {
            permanent: true,
            direction: "top",
            offset: [0, -18],
            className: "bus-label",
          },
        );
    busRef.current?.setOpacity(stale ? 0.55 : 1);
    const length = ((time % 600) / 600) * (route.length - 1);
    traceRef.current?.setLatLngs(
      available ? [...route.slice(0, Math.floor(length) + 1), position] : [],
    );
    if (follow && available)
      instance.current?.panTo(position, { animate: true });
  }, [time, vehicle, stale, follow, available]);
  const pos = routePosition(time);
  return (
    <div className={`map-layout ${compact ? "compact-map" : ""}`}>
      <section className="map-card">
        <div
          ref={element}
          className="preview-map"
          aria-label="Interactive Montréal street map"
        />
        <button
          className="map-fit"
          title="Fit entire route"
          aria-label="Fit entire route"
          onClick={() =>
            instance.current?.fitBounds(L.latLngBounds(route), {
              padding: [65, 65],
            })
          }
        >
          <Icon name="locate" />
        </button>
        <div className="map-key">
          <span>
            <i style={{ background: "#d9277f" }} />
            Observed trajectory
          </span>
          <span>
            <i className="dashed" />
            Planned route
          </span>
          <span>
            <b className="station-dot">A</b>Charging bay
          </span>
        </div>
      </section>
      {!compact && (
        <aside className="stack">
          <section className="panel gradient cyan position-card">
            <Icon name="Map" size={27} />
            <h2>
              {!available
                ? "Position unavailable"
                : stale
                  ? "Last valid position"
                  : "Current position"}
            </h2>
            <strong>
              {available ? number(pos[0], 5) + "° N" : "—"}
              <br />
              {available ? number(-pos[1], 5) + "° W" : ""}
            </strong>
            <span>
              {vehicleName(vehicle)} ·{" "}
              {!available ? "Awaiting readings" : stale ? "Stale" : "Fresh"}
            </span>
          </section>
          <section className="panel">
            <h2>Map layers</h2>
            <div className="layer-list">
              {Object.entries({
                trajectory: "Observed trajectory",
                route: "Planned route",
                stops: "Stops",
                stations: "Charging bays",
                labels: "Street labels",
              }).map(([key, label]) => (
                <label key={key}>
                  <span>{label}</span>
                  <input
                    type="checkbox"
                    role="switch"
                    checked={layers[key as keyof typeof layers]}
                    onChange={(e) =>
                      setLayers({ ...layers, [key]: e.target.checked })
                    }
                  />
                </label>
              ))}
            </div>
          </section>
          <section className="panel gradient charcoal">
            <h2>Route context</h2>
            <dl className="key-values">
              <dt>Vehicle</dt>
              <dd>{vehicleName(vehicle)}</dd>
              <dt>Heading</dt>
              <dd>{available ? "5.65 rad" : "Unavailable"}</dd>
              <dt>Map zoom</dt>
              <dd>{zoom}</dd>
              <dt>Position</dt>
              <dd>
                {!available
                  ? "Unavailable"
                  : stale
                    ? "Last observed"
                    : "Current"}
              </dd>
            </dl>
            <label className="follow-control">
              <input
                type="checkbox"
                checked={follow}
                onChange={(e) => setFollow(e.target.checked)}
              />
              Follow vehicle
            </label>
          </section>
        </aside>
      )}
    </div>
  );
}
