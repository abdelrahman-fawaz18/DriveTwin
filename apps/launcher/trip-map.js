import { routeLayers } from "/route-layers.js";

export function tripMap(host, message, api) {
  let map;
  let selected;
  let request;
  let observer;
  return async function load(trip, retry = false) {
    if (!retry && trip === selected) return;
    selected = trip;
    request?.abort();
    observer?.disconnect();
    map?.remove();
    map = undefined;
    host.hidden = true;
    message.hidden = false;
    const spinner = document.createElement("span");
    spinner.className = "loading-spinner";
    spinner.setAttribute("aria-hidden", "true");
    message.replaceChildren(spinner, " Loading the local street map…");
    const controller = new AbortController();
    request = controller;
    try {
      const L = window.L;
      if (!L)
        throw Error(
          "Install the dashboard dependencies to enable the local map.",
        );
      if (!retry) {
        await new Promise((resolve) => setTimeout(resolve, 180));
        if (controller.signal.aborted) return;
      }
      const context = await api(
        "/api/map?" + new URLSearchParams({ trip }),
        undefined,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      host.hidden = false;
      map = L.map(host, {
        scrollWheelZoom: false,
        zoomAnimation: false,
        maxZoom: 20,
      });
      map.attributionControl.setPrefix(false);
      map.attributionControl.addAttribution(
        '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors · ODbL-1.0</a>',
      );
      L.control.scale({ imperial: false }).addTo(map);
      const { route } = routeLayers(
        L,
        map,
        context,
        ["roads", "route", "stops", "endpoints"],
        {
          streetLabels: true,
        },
      );
      map.fitBounds(route.getBounds(), { padding: [22, 22], maxZoom: 16 });
      observer = new ResizeObserver(() => map?.invalidateSize());
      observer.observe(host);
      message.hidden = true;
    } catch (error) {
      if (controller.signal.aborted) return;
      map?.remove();
      map = undefined;
      host.hidden = true;
      message.textContent = error.message;
      const button = document.createElement("button");
      button.className = "secondary";
      button.textContent = "Reload map";
      button.addEventListener("click", () => void load(trip, true));
      message.append(" ", button);
    }
  };
}
