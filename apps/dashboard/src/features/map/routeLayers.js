// Shared by the launcher and dashboard. GeoJSON coordinates are longitude, latitude.
export function routeLayers(L, map, context, visible, options = {}) {
  const group = L.layerGroup().addTo(map);
  const streetPane =
    map.getPane("prepared-streets") ?? map.createPane("prepared-streets");
  streetPane.style.zIndex = "220";
  const streets = L.canvas({ padding: 0.25, pane: streetPane });
  const features = context.features.filter((feature) =>
    visible.includes(feature.properties.layer),
  );
  const roads = features.filter((f) => f.properties.layer === "roads");
  for (const [color, weight] of [
    ["#d1d9df", 4.6],
    ["#fffdf7", 3],
  ]) {
    L.geoJSON(roads, {
      renderer: streets,
      style: { color, weight, opacity: 1, interactive: false },
    }).addTo(group);
  }
  const labels = new Set();
  const placed = [];
  if (options.streetLabels) {
    for (const road of roads) {
      const name = road.properties.name;
      const coordinates = road.geometry.coordinates;
      if (
        !name ||
        name === "Road" ||
        labels.has(name) ||
        coordinates.length < 3
      )
        continue;
      const [lon, lat] = coordinates[Math.floor(coordinates.length / 2)];
      if (
        placed.some(
          ([x, y]) => Math.abs(lon - x) < 0.006 && Math.abs(lat - y) < 0.003,
        )
      )
        continue;
      const label = document.createElement("span");
      label.textContent = name;
      L.marker([lat, lon], {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: "street-label",
          html: label,
          iconSize: [112, 16],
          iconAnchor: [56, 8],
        }),
      }).addTo(group);
      placed.push([lon, lat]);
      labels.add(name);
      if (labels.size >= 24) break;
    }
  }
  const route = L.geoJSON(
    features.filter((f) => f.properties.layer === "route"),
    {
      style: {
        color: "#9060c4",
        weight: 4,
        opacity: 0.95,
        dashArray: options.dashed ? "8 6" : undefined,
      },
      onEachFeature: (_feature, layer) => {
        layer.on("add", () =>
          layer.getElement()?.setAttribute("data-map-layer", "route"),
        );
      },
    },
  ).addTo(group);
  L.geoJSON(
    features.filter((f) => f.geometry.type === "Point"),
    {
      pointToLayer: (feature, position) => {
        const { id, layer } = feature.properties;
        const [fillColor, color, radius] =
          layer === "stops"
            ? ["#ffd154", "#89621b", 4.5]
            : layer === "stations"
              ? ["#e9a846", "#fff", 7]
              : id === "departure"
                ? ["#128f88", "#fff", 7]
                : ["#d9277f", "#fff", 7];
        return L.circleMarker(position, {
          pane: map.getPane("route-points") ? "route-points" : "overlayPane",
          fillColor,
          color,
          radius,
          fillOpacity: 1,
          weight: 2.4,
        });
      },
      onEachFeature: (feature, layer) => {
        const {
          layer: kind,
          name,
          hypothetical,
          power_limit_W,
          duration_s,
        } = feature.properties;
        layer.on("add", () => {
          const marker = layer.getElement();
          marker?.setAttribute("data-map-layer", kind);
          marker?.setAttribute("tabindex", "0");
          marker?.setAttribute("aria-label", name);
        });
        const content = document.createElement("div");
        content.textContent = hypothetical
          ? `Charging bay · ${name.replace(/^detour-bay-(\d+)$/i, "Off-route $1").replace(/^bay-(\d+)$/i, "$1")} · ${Number(power_limit_W) / 1000} kW · Availability unverified`
          : kind === "stops" && duration_s != null
            ? `${name} · scheduled dwell ${duration_s} s`
            : name;
        layer.bindTooltip(content);
      },
    },
  ).addTo(group);
  return { group, route, streets };
}
