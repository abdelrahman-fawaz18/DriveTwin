import { tripMap } from "/trip-map.js";

const $ = (id) => document.getElementById(id);
const showTripMap = tripMap($("route-outline"), $("route-map-message"), api);
const fragment = location.hash.slice(1);
if (/^[A-Za-z0-9_-]{40,80}$/.test(fragment)) {
  sessionStorage.setItem("drivetwin-launch-access", fragment);
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("drivetwin-launch-access");
const launcherApiVersion = 16;
const pendingDashboardKey = "drivetwin-pending-dashboard";
let openingDashboard = false;
let trips = [],
  selected = null,
  region = "all",
  latest = null,
  busy = false,
  closed = false;
let toastTimer;
let controlRevision = 0;
let stopIdentity;
let historyOffset = 0,
  historyRequest = 0,
  historyUpdated = 0,
  searchTimer;
let historySignature = "";
let preferences = {};
try {
  const stored = JSON.parse(
    localStorage.getItem("drivetwin-launch-preferences") || "{}",
  );
  if (stored && typeof stored === "object" && !Array.isArray(stored))
    preferences = stored;
} catch {
  /* Browser storage can be disabled; launching still works. */
}
const states = {
  checking: "Checking host",
  building: "Building dashboard",
  starting: "Starting services",
  running: "Running",
  history: "Browsing history",
  stopping: "Saving and cleaning up",
  arrived: "Complete",
  stopped_incomplete: "Stopped early",
  failed_or_incomplete: "Needs review",
  failed_before_start: "Could not start",
  blocked_by_host_preflight: "Host check blocked",
  cleanup_unverified: "Cleanup needs review",
  recovering_cleanup: "Recovering interrupted trip",
  incomplete: "Incomplete",
  results_unavailable: "Results unavailable",
  preview_complete: "Preview complete",
};
const serviceNames = {
  monitoring: "Monitoring",
  energy: "Energy Forecast",
  replay: "Replay",
  charging: "Charging Advisor",
};
const speeds = [0.5, 1, 2, 5, 10, 20];
const selectedSpeed = () => Number($("trip-speed").value);
const advisorDelays = [0, 30, 60, 120];
const selectedAdvisorDelay = () => Number($("charging-startup-delay").value);
function setAdvisorDelay(value) {
  $("charging-startup-delay").value = String(
    advisorDelays.includes(value) ? value : 0,
  );
}

function setStartingCharge(override) {
  const value = override ?? selected.initial_charge_percent;
  $("starting-charge").value = String(value);
  $("starting-charge-slider").value = String(value);
  showChargeNote();
}

function showChargeNote() {
  const input = $("starting-charge");
  const preset = selected?.initial_charge_percent;
  $("charge-note").textContent = !input.validity.valid
    ? "Enter 0.1% to 100% in increments of 0.1 percentage point."
    : `Preset starts at ${preset}%.${input.valueAsNumber < 10 ? " This run starts below the 10% reserve." : ""}`;
}

function savePreferences() {
  preferences = {
    trip: selected?.id,
    energy: $("energy").checked,
    replay: $("replay").checked,
    charging: $("charging").checked,
    charging_startup_delay_working_s: selectedAdvisorDelay(),
  };
  try {
    localStorage.setItem(
      "drivetwin-launch-preferences",
      JSON.stringify(preferences),
    );
  } catch {
    /* Optional preference storage. */
  }
}

function notify(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 5000);
}
function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}
async function api(path, value, signal) {
  const options = {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  };
  if (value !== undefined)
    Object.assign(options, {
      method: "POST",
      headers: { ...options.headers, "Content-Type": "application/json" },
      body: JSON.stringify(value),
    });
  const response = await fetch(path, { ...options, signal });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}
function estimatedDuration(trip, speed = 1) {
  return `≈ ${(trip.minutes / speed).toLocaleString("en-US", { maximumFractionDigits: 1 })} min`;
}
function drawTripList() {
  const shown = trips.filter(
    (trip) => region === "all" || trip.region === region,
  );
  $("trip-list").replaceChildren();
  $("trip-select").replaceChildren();
  for (const trip of shown) {
    const duration = estimatedDuration(trip);
    const realDuration = estimatedDuration(trip, selectedSpeed());
    const option = text(
      "option",
      `${trip.title} · Working time ${duration} · Real-life driving ${realDuration}`,
    );
    option.value = trip.id;
    option.selected = trip.id === selected?.id;
    $("trip-select").append(option);
    const button = document.createElement("button");
    button.className = "trip-choice";
    button.setAttribute("aria-pressed", String(trip.id === selected?.id));
    button.disabled = Boolean(latest?.run?.active || busy);
    button.append(
      text("span", trip.region, "city"),
      text("strong", trip.title),
    );
    const metrics = text("span", "", "metrics");
    metrics.append(text("span", `${trip.distance_km} km`));
    if (trip.id === selected?.id)
      metrics.append(text("span", "Selected", "selected-label"));
    button.append(metrics);
    const times = text("span", "", "trip-times");
    for (const label of ["Working time", "Real-life driving"]) {
      const row = text("span", "", "trip-time");
      row.append(
        text("span", label),
        text(
          "span",
          label === "Working time" ? duration : realDuration,
          "trip-time-value",
        ),
      );
      times.append(row);
    }
    button.append(times);
    button.addEventListener("click", () => chooseTrip(trip.id));
    $("trip-list").append(button);
  }
  $("trip-count").textContent = `${shown.length} presets`;
}
function chooseTrip(id, charge) {
  const previous = selected;
  selected = trips.find((trip) => trip.id === id) || trips[0];
  if (!selected) return;
  if (selected !== previous) $("charging-operator").checked = false;
  if (selected !== previous || charge !== undefined) setStartingCharge(charge);
  $("region").textContent = selected.region;
  $("trip-title").textContent = selected.title;
  $("trip-description").textContent = selected.conditions;
  $("distance").textContent = `${selected.distance_km} km`;
  $("duration").textContent = estimatedDuration(selected);
  $("real-duration").textContent = estimatedDuration(selected, selectedSpeed());
  $("stops").textContent = selected.stops;
  $("assumptions").textContent =
    `Vehicle mass: ${selected.mass_kg.toLocaleString()} kg. Equipment demand: ${selected.equipment_kw} kW.`;
  $("energy").disabled =
    !selected.available_services.includes("energy") ||
    Boolean(latest?.run?.active);
  if (!selected.available_services.includes("energy"))
    $("energy").checked = false;
  $("energy-reason").textContent = selected.energy_unavailable_reason || "";
  $("energy-reason").hidden = !selected.energy_unavailable_reason;
  updateChargingControl(Boolean(latest?.run?.active));
  $("timing-note").textContent =
    `Target speed: ${selectedSpeed()}×. Real-life driving estimates use working duration divided by speed; laptop slowdowns can increase them.`;
  $("speed-note").textContent =
    selectedSpeed() >= 10
      ? "High-speed viewing. Every reading is recorded; the screen may skip intermediate updates. Forecasts use the newest available input and can become stale if computation falls behind. Choose a lower speed for closer observation."
      : "Choose a speed before starting. Working time and vehicle behavior stay unchanged.";
  void showTripMap(selected.id);
  drawTripList();
  savePreferences();
}
function updateChargingControl(locked) {
  let reason = "Advisory mode · Simulated station availability.";
  const available = selected?.available_services.includes("charging");
  const supported = available && $("energy").checked && selectedSpeed() === 1;
  if (!available)
    reason =
      "A reviewed charging station assignment is required for this trip.";
  else if (!$("energy").checked)
    reason = "Enable Energy Forecast to use Charging Advisor.";
  else if (selectedSpeed() !== 1) reason = "Select 1× to use Charging Advisor.";
  $("charging").disabled = locked || !supported;
  if (!supported) $("charging").checked = false;
  const advisorEnabled = Boolean(supported && $("charging").checked);
  $("charging-startup-option").hidden = !advisorEnabled;
  $("charging-startup-delay").disabled = locked || !advisorEnabled;
  const approval = Boolean(
    supported && $("charging").checked && selected?.charging_operator_available,
  );
  $("charging-approval-option").hidden = !approval;
  $("charging-operator").disabled = locked || !approval;
  if (!approval) $("charging-operator").checked = false;
  if (supported && $("charging").checked)
    reason = approval
      ? $("charging-operator").checked
        ? "Operator confirmation enabled · Simulated station availability."
        : "Operator confirmation is available for this trip. Enable it below to approve a stop."
      : "Advice only for this trip · Charging stops can be reviewed during the drive.";
  const layout = {
    "on-route": "On-route bays",
    "off-route": "Off-route bays only",
    mixed: "On-route and off-route bays",
  }[
    $("charging-operator").checked &&
    !selected?.charging_detour_operator_available
      ? "on-route"
      : selected?.charging_layout
  ];
  $("charging-layout").textContent = layout
    ? `${layout} · Simulated stations`
    : "";
  $("charging-layout").hidden = !layout;
  $("charging-reason").textContent = reason;
  updateLaunchSummary();
}
function updateLaunchSummary() {
  if (!selected) return;
  const charge = $("starting-charge");
  $("launch-selection").textContent = selected.title;
  $("launch-charge").textContent = charge.validity.valid
    ? `${charge.value}%`
    : "Check starting charge";
  $("launch-working").textContent = estimatedDuration(selected);
  $("launch-real-life").textContent =
    `${estimatedDuration(selected, selectedSpeed())} at ${selectedSpeed()}×`;
  $("launch-services").textContent = [
    "Monitoring",
    ...["energy", "charging", "replay"]
      .filter((id) => $(id).checked)
      .map((id) =>
        id === "charging" && $("charging-operator").checked
          ? "Charging Advisor with approval"
          : serviceNames[id],
      ),
  ].join(" · ");
}
function drawStages(run) {
  const stage = {
    checking: 0,
    building: 1,
    starting: 1,
    running: 2,
    history: 2,
    stopping: 3,
    recovering_cleanup: 3,
  }[run.state];
  $("run-stages").replaceChildren();
  [
    "Check host",
    "Prepare services",
    "Drive & review",
    "Save & clean up",
  ].forEach((name, index) => {
    const finished =
      run.state === "arrived" ||
      (!run.active && run.cleanup_verified && index === 3);
    const done =
      finished || (run.active && stage !== undefined && index < stage);
    const current =
      (run.active || run.state === "recovering_cleanup") && index === stage;
    const item = text("li", "", done ? "done" : current ? "current" : "");
    const marker = text("span", done ? "✓" : String(index + 1));
    if (current && [0, 1, 3].includes(index)) {
      const spinner = text("i", "", "loading-spinner");
      spinner.setAttribute("aria-hidden", "true");
      marker.replaceChildren(spinner);
      item.setAttribute("aria-busy", "true");
    }
    item.append(marker, text("strong", name));
    if (current) item.setAttribute("aria-current", "step");
    $("run-stages").append(item);
  });
}
function setAccess(access, operatorRun, runId) {
  const input = $("monitor-key");
  if (input.dataset.session !== runId) {
    setMonitorVisibility(false);
    input.dataset.session = runId;
  }
  input.placeholder = operatorRun ? "Select Copy key" : "";
  if (!operatorRun || input.dataset.run !== operatorRun) {
    input.value = operatorRun ? "" : access.monitoring_token || "";
    input.dataset.run = operatorRun || "";
  }
  if (access.monitoring_url === "http://127.0.0.1:18765")
    $("monitor-link").href =
      /^http:\/\/127\.0\.0\.1:18765\/#trip-control=[\w-]{43}$/.test(
        access.dashboard_url,
      )
        ? access.dashboard_url
        : access.monitoring_url;
  else $("monitor-link").removeAttribute("href");
}
function render(data) {
  latest = data;
  const run = data.run;
  const active = Boolean(run?.active);
  const recovering = run?.state === "recovering_cleanup";
  const compatible = data.launcher_api_version === launcherApiVersion;
  const metadataError = data.session_metadata_error;
  $("launcher-update").hidden = compatible && !metadataError;
  $("launcher-update").textContent = compatible
    ? metadataError || ""
    : active
      ? "The launch service needs an update. Stop the active trip and wait for cleanup, then double-click DriveTwin.cmd to reopen it."
      : "The launch service needs an update. Double-click DriveTwin.cmd to reopen it, then close this older tab.";
  $("start").disabled =
    !compatible ||
    Boolean(metadataError) ||
    busy ||
    active ||
    recovering ||
    !selected ||
    !$("starting-charge").validity.valid ||
    run?.state === "cleanup_unverified";
  $("start").textContent = active
    ? run.state === "history"
      ? "Review window open"
      : run.state === "stopping"
        ? "Saving recording"
        : "Trip in progress"
    : "▶ Start trip";
  if (busy && !active && run?.state !== "cleanup_unverified") {
    const spinner = text("span", "", "loading-spinner");
    spinner.setAttribute("aria-hidden", "true");
    $("start").replaceChildren(spinner, " Starting trip…");
  }
  $("close-app").disabled = active || busy || recovering;
  $("trip-select").disabled = active || busy;
  $("trip-speed").disabled = active || busy;
  for (const id of [
    "starting-charge",
    "starting-charge-slider",
    "reset-charge",
  ])
    $(id).disabled = active || busy;
  for (const button of document.querySelectorAll("[data-region]"))
    button.disabled = active || busy;
  $("select-all").disabled = active || busy;
  $("energy").disabled =
    active || busy || !selected?.available_services.includes("energy");
  $("replay").disabled = active || busy;
  updateChargingControl(active || busy);
  for (const button of document.querySelectorAll(".trip-choice"))
    button.disabled = active || busy;
  for (const button of document.querySelectorAll(".reuse-trip"))
    button.disabled = active || busy;
  const pause = run?.pause;
  $("state").textContent =
    pause?.available && pause.paused
      ? "Paused"
      : run?.cleanup_recovered && !recovering
        ? "Recovered"
        : run
          ? states[run.state] || run.state
          : metadataError
            ? "Cleanup needs review"
            : "Idle";
  $("idle").hidden = Boolean(run) || Boolean(metadataError);
  $("run").hidden = !run;
  if (!active) $("dashboard-opening").hidden = true;
  if ($("stop-dialog").open && (!active || run?.id !== stopIdentity))
    $("stop-dialog").close();
  if (!run) return;
  drawStages(run);
  $("run-title").textContent = run.title;
  $("run-message").textContent = run.message;
  $("completed-recording").hidden =
    run.state !== "arrived" || run.recording_saved !== true;
  $("pause").hidden = !pause?.available;
  $("pause").disabled = busy || Boolean(pause?.pending);
  $("pause").textContent = pause?.pending
    ? pause.requested_paused
      ? "Pausing…"
      : "Resuming…"
    : pause?.paused
      ? "Resume trip"
      : "Pause trip";
  $("pause-status").hidden =
    !pause?.available || !(pause.pending || pause.paused);
  $("pause-status").textContent = pause?.pending
    ? pause.confirmation_delayed
      ? "The simulator has not confirmed the request yet. Stop and save is available."
      : "Waiting for the simulator to confirm the request."
    : pause?.paused
      ? `Paused at ${pause.working_s} working seconds. The session time limit still applies.`
      : "";
  if (pause?.available && pause.paused)
    $("run-message").textContent =
      "The simulator clock is paused. Monitoring and recorded readings remain available.";
  $("run-error").textContent = run.error || "";
  $("run-error").hidden = !run.error;
  $("elapsed").textContent = active
    ? `Real-life elapsed: ${Math.floor((Date.now() / 1000 - run.created) / 60)} min`
    : "Run ended";
  $("run-services").textContent =
    run.services.map((s) => serviceNames[s] || s).join(" · ") +
    (run.charging_operator ? " · Operator confirmation" : "") +
    (run.charging_startup_delay_working_s
      ? ` · Advisor delay ${run.charging_startup_delay_working_s} working s`
      : "") +
    ` · ${run.time_scale ?? 1}× target speed` +
    (run.initial_charge_percent == null
      ? ""
      : ` · Started at ${run.initial_charge_percent}%`);
  $("cleanup").textContent =
    run.cleanup_verified === true
      ? "✓ Cleanup verified"
      : run.cleanup_verified === false
        ? "Cleanup not verified"
        : "";
  $("result-path").textContent = `Recording and result: ${run.output}`;
  $("log-path").textContent = `Launcher log: ${run.log}`;
  $("stop").disabled = !active || busy || run.state === "stopping";
  $("recover").hidden = !compatible || run.state !== "cleanup_unverified";
  $("recover").disabled = busy;
  $("stop").textContent =
    run.state === "stopping"
      ? "Saving and cleaning up…"
      : "Stop and save recording";
  const access = run.access || {};
  const dashboardAvailable = Boolean(
    access.monitoring_url &&
      (run.charging_operator
        ? run.charging_operator_ready
        : access.monitoring_token),
  );
  $("access").hidden = !dashboardAvailable;
  $("monitor-access").hidden = !dashboardAvailable;
  $("monitor-help").hidden = !dashboardAvailable;
  $("dashboard-key-access").textContent = run.charging_operator
    ? "Includes charging approval"
    : "Read-only monitoring";
  $("dashboard-history").hidden =
    !run.charging_operator || run.state !== "history";
  setAccess(access, run.charging_operator_ready ? run.id : "", run.id);
}
function drawHistory(data) {
  const list = $("history-list");
  list.replaceChildren();
  $("history-count").textContent =
    `${data.total} session${data.total === 1 ? "" : "s"}`;
  if (!data.items.length)
    list.append(
      text("p", "No sessions match this selection.", "history-empty"),
    );
  for (const entry of data.items) {
    const card = text("article", "", "history-entry");
    const header = text("div", "", "history-entry-heading");
    const heading = text("div", "", "history-name");
    heading.append(
      text("h3", entry.title),
      text(
        "p",
        new Date(entry.created * 1000).toLocaleString(undefined, {
          dateStyle: "medium",
          timeStyle: "short",
        }) + " · local time",
      ),
    );
    const state = text("span", states[entry.state] || entry.state, "pill");
    if (entry.state === "arrived") state.classList.add("success");
    else if (entry.state === "stopped_incomplete")
      state.classList.add("partial");
    else if (!entry.active) state.classList.add("attention");
    header.append(heading, state);
    const facts = text("div", "", "history-facts");
    facts.append(
      text("span", `${entry.time_scale ?? 1}× speed`),
      text("span", entry.services.map((s) => serviceNames[s] || s).join(" · ")),
      text(
        "span",
        entry.recording_saved
          ? "✓ Database recording saved"
          : entry.active
            ? "Recording in progress"
            : "No verified database backup",
        entry.recording_saved ? "saved-ok" : "",
      ),
    );
    if (entry.cleanup_verified === true)
      facts.append(text("span", "✓ Cleanup verified"));
    if (entry.initial_charge_percent != null)
      facts.append(text("span", `Started at ${entry.initial_charge_percent}%`));
    if (entry.charging_operator)
      facts.append(text("span", "Operator confirmation"));
    if (entry.charging_startup_delay_working_s)
      facts.append(
        text(
          "span",
          `Advisor delay ${entry.charging_startup_delay_working_s} working s`,
        ),
      );
    const actions = text("div", "", "history-actions");
    const open = text("button", "Open results folder ↗", "secondary");
    open.disabled = !entry.folder_available;
    open.addEventListener("click", async () => {
      open.disabled = true;
      try {
        await api("/api/open-results", { id: entry.id });
        notify("Results folder opened in File Explorer.");
      } catch (error) {
        notify(error.message);
      } finally {
        open.disabled = !entry.folder_available;
      }
    });
    actions.append(open);
    if (trips.some((trip) => trip.id === entry.trip_id)) {
      const reuse = text(
        "button",
        "Use these settings",
        "secondary reuse-trip",
      );
      reuse.disabled = Boolean(latest?.run?.active || busy);
      reuse.addEventListener("click", () => {
        if (latest?.run?.active || busy) return;
        region = "all";
        document
          .querySelectorAll("[data-region]")
          .forEach((button) =>
            button.setAttribute(
              "aria-pressed",
              String(button.dataset.region === region),
            ),
          );
        $("energy").checked = entry.services.includes("energy");
        $("replay").checked = entry.services.includes("replay");
        $("charging").checked = entry.services.includes("charging");
        setAdvisorDelay(entry.charging_startup_delay_working_s);
        $("charging-operator").checked = false;
        $("trip-speed").value = String(
          speeds.includes(entry.time_scale) ? entry.time_scale : 1,
        );
        chooseTrip(entry.trip_id, entry.starting_charge_percent ?? null);
        $("trips").scrollIntoView({ behavior: "smooth", block: "start" });
        notify(
          "Trip, starting charge, speed, and services selected. Click Start trip when ready.",
        );
      });
      actions.append(reuse);
    }
    card.append(header, facts, actions);
    if (entry.absolute_path) {
      const details = text("details", "", "history-location");
      details.append(
        text("summary", "Recording location"),
        text("p", entry.absolute_path),
      );
      const copyPath = text("button", "Copy folder path", "text-button");
      copyPath.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(entry.absolute_path);
          notify("Folder path copied.");
        } catch {
          notify("Select the displayed folder path and press Ctrl+C.");
        }
      });
      details.append(copyPath);
      card.append(details);
    }
    list.append(card);
  }
  $("history-prev").disabled = data.offset === 0;
  $("history-next").disabled = data.offset + data.items.length >= data.total;
  $("history-page").textContent = data.total
    ? `${data.offset + 1}–${data.offset + data.items.length} of ${data.total}`
    : "";
}
async function loadHistory(refresh = false) {
  const request = ++historyRequest;
  if (refresh || !historyUpdated) $("history-loading").hidden = false;
  const query = new URLSearchParams({
    offset: String(historyOffset),
    search: $("history-search").value,
    outcome: $("history-outcome").value,
    refresh: refresh ? "1" : "0",
  });
  try {
    const data = await api(`/api/history?${query}`);
    if (request !== historyRequest || closed) return;
    // A changed filter or external cleanup can remove the current page.
    if (!data.items.length && data.total && historyOffset) {
      historyOffset = 0;
      return loadHistory();
    }
    const signature = JSON.stringify([data.items, data.offset, data.total]);
    if (signature !== historySignature) {
      drawHistory(data);
      historySignature = signature;
    }
    $("history-error").hidden = true;
    historyUpdated = Date.now();
  } catch (error) {
    if (request !== historyRequest || closed) return;
    $("history-error").textContent =
      `Saved sessions could not be loaded: ${error.message}`;
    $("history-error").hidden = false;
    historyUpdated = Date.now();
  } finally {
    if (request === historyRequest) $("history-loading").hidden = true;
  }
}
async function action(path, value) {
  if (busy) return null;
  controlRevision++;
  busy = true;
  showError("");
  if (latest) render(latest);
  try {
    const result = await api(path, value);
    render(result);
    if (path !== "/api/pause") void loadHistory();
    return result;
  } catch (error) {
    showError(error.message);
    $("error").focus();
    return null;
  } finally {
    busy = false;
    if (latest) render(latest);
  }
}
async function openPendingDashboard(data) {
  if (data.launcher_api_version !== launcherApiVersion) return;
  const pending = sessionStorage.getItem(pendingDashboardKey);
  if (!pending || openingDashboard) return;
  const run = data.run;
  if (
    !run ||
    run.id !== pending ||
    !run.active ||
    run.state === "stopping" ||
    (run.charging_operator && run.state === "history")
  ) {
    sessionStorage.removeItem(pendingDashboardKey);
    $("dashboard-opening").hidden = true;
    return;
  }
  $("dashboard-opening").hidden = false;
  $("dashboard-opening").textContent =
    "Preparing your trip. The dashboard will open automatically when ready. Keep this page open.";
  if (run.charging_operator && !run.charging_operator_ready) {
    $("dashboard-opening").textContent =
      "Preparing your dashboard key with charging approval. The dashboard will open automatically when ready.";
    return;
  }
  if (
    !run.access?.monitoring_url ||
    (!run.charging_operator && !run.access.monitoring_token)
  )
    return;
  openingDashboard = true;
  // Remove before requesting: a failed browser launch gets an explicit manual fallback,
  // not repeated new tabs or repeated clipboard replacement on every status poll.
  sessionStorage.removeItem(pendingDashboardKey);
  try {
    const result = await api("/api/open-dashboard", { id: pending });
    $("dashboard-opening").textContent = result.key_copied
      ? "Dashboard opened. Paste the copied key into Dashboard key and click Connect to monitoring."
      : "Dashboard opened. Use Copy key below, then paste it into the dashboard to sign in.";
  } catch (error) {
    $("dashboard-opening").textContent =
      `${error.message} You can use Copy key & open dashboard below.`;
  } finally {
    openingDashboard = false;
  }
}
async function poll() {
  if (closed) return;
  const revision = controlRevision;
  try {
    if (busy) return;
    const data = await api("/api/status");
    if (closed || revision !== controlRevision) return;
    $("connection").textContent = "Local connection";
    $("connection-dot").classList.remove("offline");
    if (!trips.length) {
      trips = data.trips;
      const activeRun = data.run?.active ? data.run : null;
      $("trip-speed").value = String(activeRun?.time_scale ?? 1);
      $("energy").checked = activeRun
        ? activeRun.services.includes("energy")
        : preferences.energy !== false;
      $("replay").checked = activeRun
        ? activeRun.services.includes("replay")
        : preferences.replay !== false;
      $("charging").checked = activeRun
        ? activeRun.services.includes("charging")
        : preferences.charging === true;
      setAdvisorDelay(
        (activeRun ?? preferences).charging_startup_delay_working_s,
      );
      chooseTrip(
        activeRun?.trip_id ||
          preferences.trip ||
          data.run?.trip_id ||
          trips[0]?.id,
        activeRun?.starting_charge_percent,
      );
      if (!trips.length)
        showError(
          "No prepared trips are installed. Import the retained trip catalog using the local launch guide.",
        );
    }
    $("workspace").hidden = false;
    $("launcher-loading").hidden = true;
    render(data);
    await openPendingDashboard(data);
    if (Date.now() - historyUpdated > 15000) await loadHistory();
  } catch (error) {
    if (closed || revision !== controlRevision) return;
    $("connection").textContent = "Disconnected";
    $("launcher-loading").hidden = true;
    $("connection-dot").classList.add("offline");
    $("start").disabled = true;
    showError(
      error.message === "Failed to fetch"
        ? "The local launcher is unavailable. Double-click DriveTwin.cmd to reopen it; an existing run will be recovered."
        : error.message,
    );
  } finally {
    if (!closed) setTimeout(poll, 2000);
  }
}
document.querySelectorAll("[data-region]").forEach((button) =>
  button.addEventListener("click", () => {
    region = button.dataset.region;
    document
      .querySelectorAll("[data-region]")
      .forEach((other) =>
        other.setAttribute("aria-pressed", String(other === button)),
      );
    const shown = trips.filter(
      (trip) => region === "all" || trip.region === region,
    );
    if (!shown.some((trip) => trip.id === selected?.id))
      chooseTrip(shown[0]?.id);
    else drawTripList();
  }),
);
$("trip-select").addEventListener("change", (event) =>
  chooseTrip(event.target.value),
);
$("trip-speed").addEventListener("change", () => chooseTrip(selected?.id));
$("starting-charge").addEventListener("input", () => {
  if ($("starting-charge").validity.valid)
    $("starting-charge-slider").value = $("starting-charge").value;
  showChargeNote();
  if (latest) render(latest);
});
$("starting-charge-slider").addEventListener("input", (event) => {
  setStartingCharge(Number(event.target.value));
  if (latest) render(latest);
});
$("reset-charge").addEventListener("click", () => {
  setStartingCharge(null);
  if (latest) render(latest);
});
$("select-all").addEventListener("click", () => {
  $("energy").checked = selected.available_services.includes("energy");
  $("replay").checked = selected.available_services.includes("replay");
  updateChargingControl(false);
  $("charging").checked = !$("charging").disabled;
  updateChargingControl(false);
  savePreferences();
});
for (const id of ["energy", "replay", "charging", "charging-operator"])
  $(id).addEventListener("change", () => {
    updateChargingControl(false);
    savePreferences();
  });
$("charging-startup-delay").addEventListener("change", savePreferences);
$("start").addEventListener("click", async () => {
  const charge = $("starting-charge");
  if (!selected || !charge.reportValidity()) return;
  const services = ["energy", "replay", "charging"].filter(
    (id) => $(id).checked && !$(id).disabled,
  );
  const result = await action("/api/start", {
    trip_id: selected.id,
    services,
    time_scale: selectedSpeed(),
    ...(services.includes("charging") && selectedAdvisorDelay()
      ? { charging_startup_delay_working_s: selectedAdvisorDelay() }
      : {}),
    ...($("charging-operator").checked && !$("charging-operator").disabled
      ? { charging_operator: true }
      : {}),
    ...(charge.valueAsNumber !== selected.initial_charge_percent
      ? { starting_charge_percent: charge.valueAsNumber }
      : {}),
  });
  if (!result?.run?.active) return;
  $("charging-operator").checked = false;
  updateChargingControl(true);
  sessionStorage.setItem(pendingDashboardKey, result.run.id);
  $("session").scrollIntoView({ behavior: "smooth", block: "start" });
  await openPendingDashboard(result);
});
$("stop").addEventListener("click", () => {
  if (!latest?.run?.active || busy) return;
  stopIdentity = latest.run.id;
  $("stop-dialog").showModal();
});
$("recover").addEventListener("click", async () => {
  const run = latest?.run;
  if (!run || run.state !== "cleanup_unverified" || busy) return;
  await action("/api/recover", { id: run.id });
});
$("pause").addEventListener("click", async () => {
  const run = latest?.run;
  if (!run?.pause?.available || run.pause.pending || busy) return;
  await action("/api/pause", {
    id: run.id,
    revision: run.pause.revision,
    paused: !run.pause.paused,
  });
});
$("cancel-stop").addEventListener("click", () => $("stop-dialog").close());
$("confirm-stop").addEventListener("click", async () => {
  $("stop-dialog").close();
  const value = latest.launcher_api_version >= 4 ? { id: stopIdentity } : {};
  await action("/api/stop", value);
});
$("refresh-history").addEventListener("click", () => loadHistory(true));
$("history-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    historyOffset = 0;
    loadHistory();
  }, 250);
});
$("history-outcome").addEventListener("change", () => {
  historyOffset = 0;
  loadHistory();
});
$("history-prev").addEventListener("click", () => {
  historyOffset = Math.max(0, historyOffset - 20);
  loadHistory();
});
$("history-next").addEventListener("click", () => {
  historyOffset += 20;
  loadHistory();
});
document.querySelectorAll(".nav").forEach((link) =>
  link.addEventListener("click", () => {
    document
      .querySelectorAll(".nav")
      .forEach((other) => other.classList.toggle("active", other === link));
  }),
);
$("close-app").addEventListener("click", async () => {
  try {
    await api("/api/close", {});
    closed = true;
    $("workspace").hidden = true;
    $("locked").hidden = false;
    $("locked").replaceChildren(
      text("h2", "DriveTwin is closed"),
      text(
        "p",
        "You can close this tab. Double-click DriveTwin.cmd to open the workspace again.",
      ),
    );
    $("connection").textContent = "Closed";
    $("close-app").disabled = true;
    sessionStorage.removeItem("drivetwin-launch-access");
  } catch (error) {
    showError(error.message);
  }
});
async function monitorKey(run) {
  const input = $("monitor-key");
  if (run.charging_operator_ready) {
    const access = await api("/api/charging-operator-key", { id: run.id });
    if (latest?.run?.id !== run.id || !latest.run.charging_operator_ready)
      return "";
    input.value = access.charging_operator_token;
  }
  return input.value;
}
function setMonitorVisibility(visible) {
  $("monitor-key").type = visible ? "text" : "password";
  $("show-monitor").setAttribute("aria-pressed", String(visible));
  $("show-monitor").textContent = visible ? "Hide key" : "Show key";
}
async function copy(open) {
  const input = $("monitor-key");
  const url = $("monitor-link").getAttribute("href");
  const run = latest?.run;
  const operator = run?.charging_operator_ready;
  if (run?.charging_operator && !operator) return;
  if ((!operator && !input.value) || !url) return;
  // Open within the user gesture, before the asynchronous clipboard operation.
  if (open) window.open(url, "_blank", "noopener,noreferrer");
  try {
    const key = await monitorKey(run);
    if (!key) return;
    await navigator.clipboard.writeText(key);
    notify("Key copied. Paste it into the dashboard sign-in.");
  } catch {
    if (!input.value) {
      notify("The dashboard key could not be loaded. Try Copy key again.");
      return;
    }
    setMonitorVisibility(true);
    input.focus();
    input.select();
    notify(
      "Key selected. Press Ctrl+C to copy, then paste it into the dashboard.",
    );
  }
}
$("copy-monitor").addEventListener("click", () => copy(false));
$("open-monitor").addEventListener("click", () => copy(true));
$("show-monitor").addEventListener("click", async () => {
  if ($("monitor-key").type === "text") {
    setMonitorVisibility(false);
    return;
  }
  const run = latest?.run;
  if (!run?.active) return;
  try {
    if (await monitorKey(run)) setMonitorVisibility(true);
  } catch {
    notify(
      "The dashboard key could not be loaded. Try again during the active trip.",
    );
  }
});
$("monitor-key").addEventListener("click", (event) => event.target.select());
if (token) {
  $("launcher-loading").hidden = false;
  poll();
} else {
  $("locked").hidden = false;
  $("connection").textContent = "Access key required";
  $("close-app").disabled = true;
}
