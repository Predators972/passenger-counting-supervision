/**
 * @file app.js
 * @brief Front-end logic for the supervision tool: fetches data from the
 * FastAPI backend under /api and renders the four tabs (global fleet view,
 * vehicle detail, SAE/GPS anomalies, statistics). Plain JavaScript, no
 * build step, no framework.
 */

const API_BASE = "/api";

/**
 * @brief Show a spinner and an elapsed-time counter next to a button, and
 * disable it while an async action runs.
 *
 * The counter increments live during the action, then stops (without
 * disappearing) once the action completes, showing the final duration
 * until the next call. Nothing is persisted outside the running page.
 *
 * @param buttonId ID of the button element to disable during the action.
 * @param spinnerId ID of the spinner element to show/hide.
 * @param timerId ID of the element used to display the elapsed time.
 * @param asyncFn Async function to run while the button is disabled.
 * @return Promise that resolves once asyncFn has completed.
 */
async function withSpinner(buttonId, spinnerId, timerId, asyncFn) {
  const button = document.getElementById(buttonId);
  const spinner = document.getElementById(spinnerId);
  const timer = document.getElementById(timerId);

  button.disabled = true;
  spinner.classList.remove("hidden");

  const startTime = Date.now();
  timer.textContent = "0 s";
  const intervalId = setInterval(() => {
    timer.textContent = `${Math.floor((Date.now() - startTime) / 1000)} s`;
  }, 200);

  try {
    await asyncFn();
  } finally {
    clearInterval(intervalId);
    button.disabled = false;
    spinner.classList.add("hidden");
    timer.textContent = `Terminé en ${((Date.now() - startTime) / 1000).toFixed(1)} s`;
  }
}


/**
 * @brief Maintenance procedure reminder texts shown on the vehicle detail
 * tab. Which one is shown depends on how many doors are in anomaly, see
 * renderVehicleDetail.
 */
const PROCEDURE_HINTS = {
  allDoors: `
    <strong>Toutes les portes sont en anomalie</strong> - le problème vient probablement
    de la WEBOX elle-même. Voir <strong>Cas 1</strong> de la procédure de maintenance :
    carte SIM, antenne 4G, câble WEBOX ↔ switch, WEBOX plantée - redémarrer ou remplacer si besoin.
  `,
  someDoors: `
    <strong>Certaines portes seulement sont en anomalie</strong> - le problème est
    probablement localisé. Voir <strong>Cas 2</strong> de la procédure de maintenance :
    switch, ports réseau, câbles M12/Molex/alimentation, ou capteur EYES défaillant.
  `,
};

let currentVehicle = null;
let liveCheckTimer = null;
let liveCheckStopAt = null;

/**
 * @brief Fleet data fetched from the API, kept in memory. Status/type/
 * search filters and sorting are applied client-side against this array;
 * only the "Rafraîchir" button re-fetches from the backend.
 */
let allVehicles = [];

// ---------- Generic sortable-table helper (used by all 3 tables) ----------

/**
 * @brief Toggle the sort column/direction of a table and re-render it.
 *
 * @param state Mutable sort state object with column/direction fields.
 * @param column Column key to sort by.
 * @param rerenderFn Function called after updating the sort state.
 * @param defaultDirection Direction applied when switching to a new column.
 */
function toggleSort(state, column, rerenderFn, defaultDirection = "asc") {
  if (state.column === column) {
    state.direction = state.direction === "asc" ? "desc" : "asc";
  } else {
    state.column = column;
    state.direction = defaultDirection;
  }
  rerenderFn();
}

/**
 * @brief Sort an array of rows according to a sort state and a set of key
 * extractor functions.
 *
 * @param rows Array of row objects to sort.
 * @param state Sort state with column/direction fields.
 * @param keyGetters Object mapping column key to a function extracting the
 * sortable value from a row.
 * @return New sorted array (rows is not mutated), or rows unchanged if no
 * column is selected.
 */
function applySort(rows, state, keyGetters) {
  if (!state.column) return rows;
  const getKey = keyGetters[state.column];
  const dir = state.direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const valA = getKey(a);
    const valB = getKey(b);
    if (valA < valB) return -1 * dir;
    if (valA > valB) return 1 * dir;
    return 0;
  });
}

/**
 * @brief Update the sort arrow displayed in each sortable column header of
 * a table.
 *
 * @param state Sort state with column/direction fields.
 * @param headerIdMap Object mapping column key to its header element ID.
 */
function updateSortArrows(state, headerIdMap) {
  Object.values(headerIdMap).forEach(id => {
    const el = document.querySelector(`#${id} .sort-arrow`);
    if (el) el.textContent = "";
  });
  if (!state.column) return;
  const arrow = state.direction === "asc" ? "▲" : "▼";
  const el = document.querySelector(`#${headerIdMap[state.column]} .sort-arrow`);
  if (el) el.textContent = arrow;
}

// ---------- Home page ----------

/**
 * @brief Navigate to the vehicle detail tab for a given vehicle number and
 * load its detail.
 * @param numParc Vehicle number to display.
 */
function goToVehicleDetail(numParc) {
  document.getElementById("detail-vehicle-input").value = numParc;
  switchTab("detail-view");
  showVehicleDetail(numParc);
}

/**
 * @brief Navigate to the SAE/GPS tab and load the history for a given
 * vehicle number.
 * @param numParc Vehicle number to display.
 */
function goToSaeGpsHistory(numParc) {
  document.getElementById("sae-gps-search").value = numParc;
  document.getElementById("sae-gps-type-filter").value = "";
  document.getElementById("sae-gps-history-vehicle-input").value = numParc;
  switchTab("sae-gps-view");
  renderSaeGpsTables();
  loadSaeGpsHistory();
}

/**
 * @brief Show the choice modal, letting the user pick between the vehicle
 * detail (door anomaly) and the SAE/GPS view for a vehicle that has both
 * kinds of anomaly.
 * @param numParc Vehicle number the choice applies to.
 */
function showChoiceModal(numParc) {
  document.getElementById("choice-modal-text").textContent =
    `Le véhicule ${numParc} a une anomalie de porte et une anomalie SAE/GPS. Que voulez-vous consulter ?`;
  document.getElementById("choice-modal-doors-btn").onclick = () => {
    hideChoiceModal();
    goToVehicleDetail(numParc);
  };
  document.getElementById("choice-modal-saegps-btn").onclick = () => {
    hideChoiceModal();
    goToSaeGpsHistory(numParc);
  };
  document.getElementById("choice-modal").classList.remove("hidden");
}

/**
 * @brief Hide the choice modal.
 */
function hideChoiceModal() {
  document.getElementById("choice-modal").classList.add("hidden");
}

/**
 * @brief Refresh the home page: reloads the global fleet and SAE/GPS data
 * in parallel (updating those tabs too), and re-renders the category
 * breakdown.
 * @return Promise that resolves once the home page has been rendered.
 */
async function loadHome() {
  await withSpinner("home-refresh-btn", "home-refresh-spinner", "home-refresh-timer", async () => {
    await Promise.all([loadVehicles(), loadSaeGpsAnomalies()]);

    renderHome();
    document.getElementById("home-last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");
  });
}

/**
 * @brief Compute, for every vehicle with at least one anomaly, its
 * category and which kind(s) of anomaly it has.
 * @return Object mapping num_parc to {category, hasDoorAnomaly,
 * hasSaeGpsAnomaly}.
 */
function computeHomeAnomalies() {
  const map = {};

  allVehicles.forEach(v => {
    if (v.status !== "anomalie") return;
    map[v.num_parc] = map[v.num_parc] || { category: v.rolling_stock_category };
    map[v.num_parc].hasDoorAnomaly = true;
  });

  allSaeGpsVehicles.forEach(v => {
    if (v.sae.status !== "anomalie" && v.gps.status !== "anomalie") return;
    map[v.num_parc] = map[v.num_parc] || { category: v.rolling_stock_category };
    map[v.num_parc].hasSaeGpsAnomaly = true;
  });

  return map;
}

/**
 * @brief Render one category block's badge grid.
 * @param containerId ID of the .home-category element for this category.
 * @param entries Array of [num_parc, info] pairs for this category,
 * already sorted.
 */
function renderHomeCategory(containerId, entries) {
  const container = document.getElementById(containerId);
  container.querySelector(".home-category-count").textContent = `(${entries.length})`;
  const grid = container.querySelector(".home-badge-grid");
  grid.innerHTML = "";

  if (entries.length === 0) {
    grid.innerHTML = "<p>Aucune anomalie.</p>";
    return;
  }

  entries.forEach(([numParc, info]) => {
    const badge = document.createElement("button");
    badge.className = "home-badge";
    badge.textContent = numParc;
    badge.addEventListener("click", () => {
      if (info.hasDoorAnomaly && info.hasSaeGpsAnomaly) {
        showChoiceModal(numParc);
      } else if (info.hasDoorAnomaly) {
        goToVehicleDetail(numParc);
      } else {
        goToSaeGpsHistory(numParc);
      }
    });
    grid.appendChild(badge);
  });
}

/**
 * @brief Render the whole home page from allVehicles and
 * allSaeGpsVehicles. Does nothing if no data has been loaded yet.
 */
function renderHome() {
  if (allVehicles.length === 0 && allSaeGpsVehicles.length === 0) return;

  document.getElementById("home-placeholder").classList.add("hidden");
  document.getElementById("home-content").classList.remove("hidden");

  const anomalies = computeHomeAnomalies();
  const byCategory = { "Bus URBAIN": [], "Bus SUBURBAIN": [], "Tramways": [] };

  Object.entries(anomalies).forEach(([numParc, info]) => {
    if (byCategory[info.category]) {
      byCategory[info.category].push([Number(numParc), info]);
    }
  });

  Object.values(byCategory).forEach(entries => entries.sort((a, b) => a[0] - b[0]));

  renderHomeCategory("home-category-urbain", byCategory["Bus URBAIN"]);
  renderHomeCategory("home-category-suburbain", byCategory["Bus SUBURBAIN"]);
  renderHomeCategory("home-category-tramways", byCategory["Tramways"]);
}

// ---------- Global fleet view ----------

/**
 * @brief Sort state for the global fleet table, initialized to match the
 * order already returned by the API (ascending by vehicle number).
 */
const globalSortState = { column: "num_parc", direction: "asc" };
const globalHeaderIds = {
  num_parc: "col-vehicle",
  last_seen: "col-last-seen",
  last_exploitation: "col-last-exploitation",
  doors: "col-doors",
  status: "col-status",
};
/**
 * @brief Sort key extractors for the global fleet table. "stale" and
 * "unknown" exploitation cases use distinct sentinel strings so they sort
 * as separate groups rather than colliding on an equal key.
 */
const globalKeyGetters = {
  num_parc: v => v.num_parc,
  last_seen: v => v.last_seen || "",
  last_exploitation: v => {
    if (v.exploitation_case === "known") return v.last_exploitation;
    return v.exploitation_case === "stale" ? "1" : "0";
  },
  doors: v => v.door_count_total - v.door_count_functional,
  status: v => v.status,
};

/**
 * @brief Fetch the fleet overview from the API and refresh the global
 * table, type filter and statistics tab.
 * @return Promise that resolves once the table has been rendered.
 */
async function loadVehicles() {
  await withSpinner("refresh-btn", "refresh-spinner", "refresh-timer", async () => {
    const res = await fetch(API_BASE + "/vehicles");
    const data = await res.json();
    allVehicles = data.vehicles;

    renderVehicleTable();
    document.getElementById("last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");

    renderStats();
    renderHome();
  });
}

/**
 * @brief Check whether a vehicle matches the selected category filter.
 * "TaM" is a special value meaning every category except "Bus SUBURBAIN".
 * @param vehicle Vehicle entry with a rolling_stock_category field.
 * @param filterValue Selected filter value ("", "TaM", or an exact
 * category name).
 * @return True if the vehicle matches the filter.
 */
function matchesCategoryFilter(vehicle, filterValue) {
  if (!filterValue) return true;
  if (filterValue === "TaM") return vehicle.rolling_stock_category !== "Bus SUBURBAIN";
  return vehicle.rolling_stock_category === filterValue;
}

/**
 * @brief Render the global fleet table body from allVehicles, applying the
 * current status/type/search filters and sort state.
 */
function renderVehicleTable() {
  const statusFilter = document.getElementById("status-filter").value;
  const typeFilter = document.getElementById("type-filter").value;
  const search = document.getElementById("vehicle-search").value.trim();
  const tbody = document.querySelector("#vehicle-table tbody");
  tbody.innerHTML = "";

  let rows = allVehicles
    .filter(v => !statusFilter || v.status === statusFilter)
    .filter(v => matchesCategoryFilter(v, typeFilter))
    .filter(v => !search || String(v.num_parc).includes(search));

  rows = applySort(rows, globalSortState, globalKeyGetters);
  updateSortArrows(globalSortState, globalHeaderIds);

  rows.forEach(v => {
      const doorsDown = v.door_count_total - v.door_count_functional;
      const doorsClass = v.status_warning ? 'status-warning' : (doorsDown > 0 ? 'status-anomaly' : 'status-ok');

      let exploitationClass = '';
      if (v.status_warning) {
        exploitationClass = 'status-warning';
      } else if (v.status === 'anomalie' && v.exploitation_case === 'unknown') {
        exploitationClass = 'status-anomaly';
      }

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${v.num_parc}</td>
        <td>${formatDate(v.last_seen)}<br><small>${formatDuration(v.hours_since_last_seen)}</small></td>
        <td class="${exploitationClass}">${formatExploitation(v.last_exploitation, v.hours_since_last_exploitation, v.exploitation_case)}</td>
        <td class="${doorsClass}">${doorsDown} / ${v.door_count_total}</td>
        <td class="${v.status === 'anomalie' ? 'status-anomaly' : (v.status_warning ? 'status-warning' : 'status-ok')}">
          ${v.status === 'anomalie' ? 'Anomalie' : 'Fonctionnel'}
        </td>
      `;
      tr.addEventListener("click", () => {
        document.getElementById("detail-vehicle-input").value = v.num_parc;
        switchTab("detail-view");
        showVehicleDetail(v.num_parc);
      });
      tbody.appendChild(tr);
    });
}

/**
 * @brief Format an ISO timestamp string for display in local French format.
 * @param isoString ISO 8601 timestamp string, or null/undefined.
 * @return Localized date/time string, or "Aucune donnée" if isoString is
 * falsy.
 */
function formatDate(isoString) {
  if (!isoString) return "Aucune donnée";
  return new Date(isoString).toLocaleString("fr-FR");
}

/**
 * @brief Format a number of hours as a human-readable elapsed-time string.
 * @param hours Number of hours, or null/undefined.
 * @param precise When true and hours < 1, display minutes instead of
 * "moins d'1 heure".
 * @return Formatted duration string.
 */
function formatDuration(hours, precise) {
  if (hours === null || hours === undefined) return "";
  if (precise && hours < 1) {
    const minutes = Math.round(hours * 60);
    if (minutes < 1) return "il y a moins d'1 minute";
    return `il y a ${minutes} min`;
  }
  if (hours < 1) return "il y a moins d'1 heure";
  if (hours <= 24) return `depuis ${Math.floor(hours)} h`;

  const days = Math.floor(hours / 24);
  if (hours > 24 * 7) return `depuis ${days} jour${days > 1 ? "s" : ""}`;

  const remainderHours = Math.floor(hours % 24);
  return `depuis ${days} jour${days > 1 ? "s" : ""} ${remainderHours} h`;
}

/**
 * @brief Format the "last exploitation" value for display, handling the
 * three possible exploitation cases returned by the API.
 * @param lastExploitation ISO timestamp string, or null.
 * @param hoursSince Number of hours since lastExploitation, or null.
 * @param exploitationCase One of "known", "stale", "unknown".
 * @return HTML string to display in the corresponding table cell.
 */
function formatExploitation(lastExploitation, hoursSince, exploitationCase) {
  if (exploitationCase === "stale") return "Depuis plus de 30 jours";
  if (!lastExploitation) return "Aucune donnée";
  return `${formatDate(lastExploitation)}<br><small>${formatDuration(hoursSince)}</small>`;
}

// ---------- Vehicle detail view ----------

/**
 * @brief Fetch and display the detail of one vehicle, then load its
 * default (last 7 days) reporting history.
 * @param numParc Vehicle number to look up.
 * @return Promise that resolves once the detail and history have been
 * rendered.
 */
async function showVehicleDetail(numParc) {
  currentVehicle = numParc;
  stopLiveCheck();

  await withSpinner("detail-search-btn", "detail-search-spinner", "detail-search-timer", async () => {
    const url = new URL(`${API_BASE}/vehicles/${numParc}`, window.location.origin);

    const res = await fetch(url);
    if (!res.ok) {
      alert("Véhicule introuvable.");
      return;
    }
    const data = await res.json();

    document.getElementById("detail-placeholder").classList.add("hidden");
    document.getElementById("detail-content").classList.remove("hidden");

    renderVehicleDetail(data);

    const today = new Date();
    const weekAgo = new Date(today);
    weekAgo.setDate(today.getDate() - 7);
    document.getElementById("history-end").value = today.toISOString().slice(0, 10);
    document.getElementById("history-start").value = weekAgo.toISOString().slice(0, 10);
    await loadHistory();
  });
}

/**
 * @brief Render the vehicle detail summary, door grid and maintenance
 * procedure reminder from a /api/vehicles/{num_parc} response.
 * @param data Response object returned by the vehicle detail endpoint.
 */
function renderVehicleDetail(data) {
  document.getElementById("summary-num-parc").textContent = data.num_parc;
  document.getElementById("summary-door-ratio").textContent =
    `${data.door_count_functional} / ${data.door_count_total}`;
  document.getElementById("summary-rolling-stock").textContent = data.rolling_stock_type;

  populateHistoryDoorFilter(data.doors);

  const expText = document.getElementById("last-exploitation-text");
  if (data.exploitation_case === "stale") {
    expText.textContent = "Dernier service commercial : depuis plus de 30 jours (véhicule vu au dépôt sur la période, mais pas en service).";
  } else if (data.last_exploitation) {
    expText.textContent =
      `Dernier service commercial : ${formatDate(data.last_exploitation)} (${formatDuration(data.hours_since_last_exploitation)})`;
  } else {
    expText.textContent = "Dernier service commercial : aucune donnée d'exploitation trouvée sur la période chargée.";
  }

  const grid = document.getElementById("door-grid");
  grid.innerHTML = "";
  let anomalyDoorCount = 0;

  data.doors.forEach(d => {
    if (d.status === "anomalie") anomalyDoorCount++;
    const cell = document.createElement("div");
    cell.className = `door-cell ${d.status === 'anomalie' ? 'door-anomaly' : 'door-ok'}`;
    cell.innerHTML = `
      <span class="door-label">Porte ${d.porte_physique}</span>
      <span class="door-timestamp">${formatDate(d.last_seen)}<br>${formatDuration(d.hours_since_last_seen, true)}</span>
    `;
    grid.appendChild(cell);
  });

  const hintEl = document.getElementById("procedure-hint");
  const totalDoors = data.doors.length;

  if (anomalyDoorCount === 0) {
    hintEl.classList.add("hidden");
  } else if (anomalyDoorCount === totalDoors) {
    hintEl.innerHTML = PROCEDURE_HINTS.allDoors;
    hintEl.classList.remove("hidden");
  } else {
    hintEl.innerHTML = PROCEDURE_HINTS.someDoors;
    hintEl.classList.remove("hidden");
  }
}

// ---------- Post-intervention check (targeted polling) ----------

/**
 * @brief Start polling the live-check endpoint every 30 seconds to detect
 * when the current vehicle's anomaly has been resolved, stopping
 * automatically after 15 minutes.
 */
function startLiveCheck() {
  const statusEl = document.getElementById("live-check-status");
  liveCheckStopAt = Date.now() + 15 * 60 * 1000;
  statusEl.textContent = "En attente d'une nouvelle remontée...";

  liveCheckTimer = setInterval(async () => {
    if (Date.now() > liveCheckStopAt) {
      stopLiveCheck();
      statusEl.textContent = "Vérification arrêtée (délai dépassé). Relancez si besoin.";
      return;
    }

    const res = await fetch(`${API_BASE}/vehicles/${currentVehicle}/live`);
    const data = await res.json();
    renderVehicleDetail(data);

    const stillAnomaly = data.status === "anomalie" || data.doors.some(d => d.status === "anomalie");
    if (!stillAnomaly) {
      statusEl.textContent = `Résolu - nouvelle remontée détectée à ${formatDate(data.last_seen)}`;
      stopLiveCheck();
    }
  }, 30 * 1000);
}

/**
 * @brief Stop the live-check polling interval, if running.
 */
function stopLiveCheck() {
  if (liveCheckTimer) {
    clearInterval(liveCheckTimer);
    liveCheckTimer = null;
  }
}

// ---------- History ----------

/**
 * @brief Rebuild the door filter options for the vehicle history section
 * from the doors of the currently displayed vehicle.
 * @param doors Array of door status dicts, as returned by the vehicle
 * detail endpoint.
 */
function populateHistoryDoorFilter(doors) {
  const select = document.getElementById("history-door-filter");
  const previousValue = select.value;

  select.innerHTML = '<option value="">Toutes</option>';
  doors
    .map(d => d.porte_physique)
    .sort((a, b) => a - b)
    .forEach(porte => {
      const option = document.createElement("option");
      option.value = porte;
      option.textContent = `Porte ${porte}`;
      select.appendChild(option);
    });

  if ([...select.options].some(o => o.value === previousValue)) {
    select.value = previousValue;
  }
}

/**
 * @brief Fetch and display the door reporting history of the currently
 * selected vehicle, for the selected date range and door filter.
 * @return Promise that resolves once the list has been rendered.
 */
async function loadHistory() {
  await withSpinner("history-btn", "history-spinner", "history-timer", async () => {
    const start = document.getElementById("history-start").value;
    const end = document.getElementById("history-end").value;
    const door = document.getElementById("history-door-filter").value;

    const url = new URL(`${API_BASE}/history/${currentVehicle}`, window.location.origin);
    if (start) url.searchParams.set("start_date", start);
    if (end) url.searchParams.set("end_date", end);
    if (door) url.searchParams.set("door", door);

    const res = await fetch(url);
    const data = await res.json();

    const list = document.getElementById("history-list");
    list.innerHTML = "";

    if (data.reports.length === 0) {
      list.innerHTML = "<li>Aucune remontée sur cette période.</li>";
      return;
    }

    data.reports.forEach(r => {
      const li = document.createElement("li");
      li.textContent = `${formatDate(r.timestamp)} — Porte ${r.porte}`;
      list.appendChild(li);
    });
  });
}

/**
 * @brief Read the vehicle number typed in the detail search field and
 * trigger showVehicleDetail, alerting the user if the field is empty.
 */
function searchVehicleFromInput() {
  const value = document.getElementById("detail-vehicle-input").value.trim();
  if (!value) {
    alert("Veuillez saisir un numéro de véhicule.");
    return;
  }
  showVehicleDetail(value);
}

// ---------- SAE / GPS anomalies ----------

/**
 * @brief SAE/GPS fleet data fetched from the API, kept in memory. Filters
 * and sorting are applied client-side against this array.
 */
let allSaeGpsVehicles = [];

/**
 * @brief Fetch the SAE/GPS status of the fleet from the API and refresh
 * the two tables, type filter and statistics tab.
 * @return Promise that resolves once the tables have been rendered.
 */
async function loadSaeGpsAnomalies() {
  await withSpinner("sae-gps-refresh-btn", "sae-gps-refresh-spinner", "sae-gps-refresh-timer", async () => {
    const res = await fetch(`${API_BASE}/vehicles-sae-gps`);
    const data = await res.json();
    allSaeGpsVehicles = data.vehicles;

    renderSaeGpsTables();

    document.getElementById("sae-gps-last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");

    renderStats();
    renderHome();
  });
}

const saeSortState = { column: "num_parc", direction: "asc" };
const gpsSortState = { column: "num_parc", direction: "asc" };

const saeHeaderIds = {
  num_parc: "sae-col-vehicle", last_seen: "sae-col-last-seen",
  last_exploitation: "sae-col-last-exploitation", ratio: "sae-col-ratio", status: "sae-col-status",
};
const gpsHeaderIds = {
  num_parc: "gps-col-vehicle", last_seen: "gps-col-last-seen",
  last_exploitation: "gps-col-last-exploitation", ratio: "gps-col-ratio", status: "gps-col-status",
};

/**
 * @brief Build the sort key extractors for either the SAE or GPS table.
 * @param field Either "sae" or "gps", selecting which sub-object of each
 * vehicle entry to read ratio/status from.
 * @return Object mapping column key to a key extractor function.
 */
function fieldKeyGetters(field) {
  return {
    num_parc: v => v.num_parc,
    last_seen: v => v.last_seen || "",
    last_exploitation: v => {
      if (v.exploitation_case === "known") return v.last_exploitation;
      return v.exploitation_case === "stale" ? "1" : "0";
    },
    ratio: v => v[field].missing_ratio,
    status: v => v[field].status,
  };
}

/**
 * @brief Filter allSaeGpsVehicles for either the SAE or GPS table,
 * applying the shared status/type/search filters.
 * @param field Either "sae" or "gps", selecting which sub-object's status
 * the status filter applies to.
 * @return Filtered array of vehicle entries.
 */
function getFilteredSaeGpsVehicles(field) {
  const statusFilter = document.getElementById("sae-gps-status-filter").value;
  const typeFilter = document.getElementById("sae-gps-type-filter").value;
  const search = document.getElementById("sae-gps-search").value.trim();

  return allSaeGpsVehicles
    .filter(v => !statusFilter || v[field].status === statusFilter)
    .filter(v => matchesCategoryFilter(v, typeFilter))
    .filter(v => !search || String(v.num_parc).includes(search));
}

/**
 * @brief Filter, sort and render both the SAE and GPS tables from
 * allSaeGpsVehicles.
 */
function renderSaeGpsTables() {
  const saeRows = applySort(getFilteredSaeGpsVehicles("sae"), saeSortState, fieldKeyGetters("sae"));
  const gpsRows = applySort(getFilteredSaeGpsVehicles("gps"), gpsSortState, fieldKeyGetters("gps"));

  updateSortArrows(saeSortState, saeHeaderIds);
  updateSortArrows(gpsSortState, gpsHeaderIds);

  renderFieldTable("sae-table", saeRows, "sae");
  renderFieldTable("gps-table", gpsRows, "gps");
}

/**
 * @brief Render one of the SAE/GPS table bodies from a list of vehicle
 * entries.
 * @param tableId ID of the table element to render into.
 * @param vehicles Array of vehicle entries to render, already filtered
 * and sorted.
 * @param field Either "sae" or "gps", selecting which sub-object to read
 * ratio/status from.
 */
function renderFieldTable(tableId, vehicles, field) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = "";

  vehicles.forEach(v => {
    const info = v[field];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${v.num_parc}</td>
      <td>${formatDate(v.last_seen)}<br><small>${formatDuration(v.hours_since_last_seen)}</small></td>
      <td>${formatExploitation(v.last_exploitation, v.hours_since_last_exploitation, v.exploitation_case)}</td>
      <td>${info.missing_ratio}%</td>
      <td class="${info.status === 'anomalie' ? 'status-anomaly' : 'status-ok'}">
        ${info.status === 'anomalie' ? 'Anomalie' : 'Fonctionnel'}
      </td>
    `;
    tr.addEventListener("click", () => {
      document.getElementById("sae-gps-history-vehicle-input").value = v.num_parc;
      loadSaeGpsHistory();
    });
    tbody.appendChild(tr);
  });
}

// ---------- SAE / GPS history ----------

/**
 * @brief Fetch and display the SAE/GPS presence history of the vehicle
 * typed in the history vehicle field, for the selected date range.
 * @return Promise that resolves once the two history lists have been
 * rendered, or immediately if the vehicle field is empty.
 */
async function loadSaeGpsHistory() {
  const numParc = document.getElementById("sae-gps-history-vehicle-input").value.trim();
  if (!numParc) {
    alert("Veuillez saisir un numéro de véhicule.");
    return;
  }

  await withSpinner("sae-gps-history-btn", "sae-gps-history-spinner", "sae-gps-history-timer", async () => {
    const start = document.getElementById("sae-gps-history-start").value;
    const end = document.getElementById("sae-gps-history-end").value;

    const url = new URL(`${API_BASE}/history-sae-gps/${numParc}`, window.location.origin);
    if (start) url.searchParams.set("start_date", start);
    if (end) url.searchParams.set("end_date", end);

    const res = await fetch(url);
    if (!res.ok) {
      alert("Véhicule introuvable.");
      return;
    }
    const data = await res.json();

    renderPresenceHistory("sae-history-list", data.reports, "sae_present");
    renderPresenceHistory("gps-history-list", data.reports, "gps_present");
  });
}

/**
 * @brief Render a presence/absence history list (SAE or GPS) from a list
 * of report entries.
 * @param listId ID of the list element to render into.
 * @param reports Array of {timestamp, sae_present, gps_present} entries.
 * @param presentField Name of the boolean field to read from each report
 * ("sae_present" or "gps_present").
 */
function renderPresenceHistory(listId, reports, presentField) {
  const list = document.getElementById(listId);
  list.innerHTML = "";

  if (reports.length === 0) {
    list.innerHTML = "<li>Aucune remontée sur cette période.</li>";
    return;
  }

  reports.forEach(r => {
    const li = document.createElement("li");
    const present = r[presentField];
    li.innerHTML = `${formatDate(r.timestamp)} — <span class="${present ? 'status-ok' : 'status-anomaly'}">${present ? 'Présent' : 'Absent'}</span>`;
    list.appendChild(li);
  });
}

// ---------- Statistics ----------

/**
 * @brief List of lingering-anomaly vehicle numbers fetched from
 * /api/stats/lingering, or null if not fetched yet. Only refreshed by the
 * statistics tab's own "Rafraîchir" button.
 */
let lingeringVehicles = null;

/**
 * @brief Refresh the statistics tab: reloads the global fleet and SAE/GPS
 * data (updating those tabs too), fetches the lingering-anomaly list, and
 * re-renders all five sections.
 * @return Promise that resolves once the statistics have been rendered.
 */
async function loadStats() {
  await withSpinner("stats-refresh-btn", "stats-refresh-spinner", "stats-refresh-timer", async () => {
    const [, , lingeringRes] = await Promise.all([
      loadVehicles(),
      loadSaeGpsAnomalies(),
      fetch(`${API_BASE}/stats/lingering`),
    ]);
    const data = await lingeringRes.json();
    lingeringVehicles = data.vehicles;

    renderStats();
    document.getElementById("stats-last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");
  });
}

/**
 * @brief Compute and render the five statistics sections from allVehicles
 * and lingeringVehicles. Does nothing if no fleet data has been loaded yet.
 */
function renderStats() {
  if (allVehicles.length === 0) return;

  document.getElementById("stats-placeholder").classList.add("hidden");
  document.getElementById("stats-content").classList.remove("hidden");

  // 1. Fleet status
  const totalVehicles = allVehicles.length;
  const anomalieVehicles = allVehicles.filter(v => v.status === "anomalie").length;
  const pctVehicles = totalVehicles ? (anomalieVehicles / totalVehicles * 100).toFixed(1) : "0.0";
  document.getElementById("stats-vehicles-summary").textContent =
    `Véhicules en anomalie : ${anomalieVehicles} / ${totalVehicles} (${pctVehicles}%)`;

  const totalDoors = allVehicles.reduce((sum, v) => sum + v.door_count_total, 0);
  const anomalieDoors = allVehicles.reduce((sum, v) => sum + (v.door_count_total - v.door_count_functional), 0);
  const pctDoors = totalDoors ? (anomalieDoors / totalDoors * 100).toFixed(1) : "0.0";
  document.getElementById("stats-doors-summary").textContent =
    `Portes en anomalie : ${anomalieDoors} / ${totalDoors} (${pctDoors}%)`;

  // 2. Breakdown by vehicle type - Bus SUBURBAIN subtypes are merged into
  // a single row (too many distinct models to list individually here)
  const byType = {};
  allVehicles.forEach(v => {
    const key = v.rolling_stock_category === "Bus SUBURBAIN" ? "Bus SUBURBAIN" : v.rolling_stock_type;
    if (!byType[key]) byType[key] = { anomalie: 0, total: 0 };
    byType[key].total++;
    if (v.status === "anomalie") byType[key].anomalie++;
  });
  const typeTbody = document.querySelector("#stats-type-table tbody");
  typeTbody.innerHTML = "";
  Object.keys(byType).sort().forEach(type => {
    const { anomalie, total } = byType[type];
    const pct = total ? (anomalie / total * 100).toFixed(1) : "0.0";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${type}</td><td>${anomalie} / ${total}</td><td>${pct}%</td>`;
    typeTbody.appendChild(tr);
  });

  // 3. New anomalies (less than 7 days)
  const newAnomalies = allVehicles.filter(v =>
    v.status === "anomalie" && v.hours_since_last_seen !== null && v.hours_since_last_seen <= 7 * 24
  );
  document.getElementById("stats-new-summary").textContent =
    `${newAnomalies.length} véhicule(s) en anomalie depuis moins de 7 jours.`;
  document.getElementById("stats-new-list").textContent =
    newAnomalies.length ? newAnomalies.map(v => v.num_parc).join(", ") : "—";

  // 4. Lingering anomalies (more than 30 days)
  if (lingeringVehicles === null) {
    document.getElementById("stats-lingering-summary").textContent =
      "Pas encore chargé - cliquez sur \"Rafraîchir\" sur cet onglet.";
    document.getElementById("stats-lingering-list").textContent = "";
  } else {
    document.getElementById("stats-lingering-summary").textContent =
      `${lingeringVehicles.length} véhicule(s) en anomalie depuis plus de 30 jours.`;
    document.getElementById("stats-lingering-list").textContent =
      lingeringVehicles.length ? lingeringVehicles.join(", ") : "—";
  }

  // 5. Average duration of current anomalies (estimate; vehicles with no
  // known last-seen date are excluded from this specific average)
  const durations = allVehicles
    .filter(v => v.status === "anomalie" && v.hours_since_last_seen !== null)
    .map(v => Math.max(0, (v.hours_since_last_seen - 48) / 24));
  if (durations.length > 0) {
    const avg = durations.reduce((a, b) => a + b, 0) / durations.length;
    document.getElementById("stats-avg-duration").textContent =
      `${avg.toFixed(1)} jour(s) en moyenne, sur ${durations.length} véhicule(s) en anomalie avec une date connue (estimation).`;
  } else {
    document.getElementById("stats-avg-duration").textContent =
      "Aucune anomalie en cours avec une durée calculable.";
  }
}

// ---------- Tab navigation ----------

/**
 * @brief Show the given tab panel and hide the others, updating the active
 * tab button and stopping live-check polling when leaving the detail tab.
 * @param tabId ID of the tab panel element to activate.
 */
function switchTab(tabId) {
  document.querySelectorAll(".tab-panel").forEach(panel => {
    panel.classList.toggle("active", panel.id === tabId);
  });
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });

  if (tabId !== "detail-view") {
    stopLiveCheck();
  }
}

// ---------- Navigation & event listeners ----------

document.getElementById("refresh-btn").addEventListener("click", loadVehicles);
document.getElementById("status-filter").addEventListener("change", renderVehicleTable);
document.getElementById("type-filter").addEventListener("change", renderVehicleTable);
document.getElementById("vehicle-search").addEventListener("input", renderVehicleTable);
document.getElementById("col-vehicle").addEventListener("click", () => toggleSort(globalSortState, "num_parc", renderVehicleTable, "asc"));
document.getElementById("col-last-seen").addEventListener("click", () => toggleSort(globalSortState, "last_seen", renderVehicleTable, "desc"));
document.getElementById("col-last-exploitation").addEventListener("click", () => toggleSort(globalSortState, "last_exploitation", renderVehicleTable, "desc"));
document.getElementById("col-doors").addEventListener("click", () => toggleSort(globalSortState, "doors", renderVehicleTable, "desc"));
document.getElementById("col-status").addEventListener("click", () => toggleSort(globalSortState, "status", renderVehicleTable, "asc"));
document.getElementById("live-check-btn").addEventListener("click", startLiveCheck);
document.getElementById("history-btn").addEventListener("click", loadHistory);
document.getElementById("history-door-filter").addEventListener("change", loadHistory);
document.getElementById("sae-gps-refresh-btn").addEventListener("click", loadSaeGpsAnomalies);
document.getElementById("sae-gps-status-filter").addEventListener("change", renderSaeGpsTables);
document.getElementById("sae-gps-type-filter").addEventListener("change", renderSaeGpsTables);
document.getElementById("sae-gps-search").addEventListener("input", renderSaeGpsTables);

document.getElementById("sae-col-vehicle").addEventListener("click", () => toggleSort(saeSortState, "num_parc", renderSaeGpsTables, "asc"));
document.getElementById("sae-col-last-seen").addEventListener("click", () => toggleSort(saeSortState, "last_seen", renderSaeGpsTables, "desc"));
document.getElementById("sae-col-last-exploitation").addEventListener("click", () => toggleSort(saeSortState, "last_exploitation", renderSaeGpsTables, "desc"));
document.getElementById("sae-col-ratio").addEventListener("click", () => toggleSort(saeSortState, "ratio", renderSaeGpsTables, "desc"));
document.getElementById("sae-col-status").addEventListener("click", () => toggleSort(saeSortState, "status", renderSaeGpsTables, "asc"));

document.getElementById("gps-col-vehicle").addEventListener("click", () => toggleSort(gpsSortState, "num_parc", renderSaeGpsTables, "asc"));
document.getElementById("gps-col-last-seen").addEventListener("click", () => toggleSort(gpsSortState, "last_seen", renderSaeGpsTables, "desc"));
document.getElementById("gps-col-last-exploitation").addEventListener("click", () => toggleSort(gpsSortState, "last_exploitation", renderSaeGpsTables, "desc"));
document.getElementById("gps-col-ratio").addEventListener("click", () => toggleSort(gpsSortState, "ratio", renderSaeGpsTables, "desc"));
document.getElementById("gps-col-status").addEventListener("click", () => toggleSort(gpsSortState, "status", renderSaeGpsTables, "asc"));
document.getElementById("sae-gps-history-btn").addEventListener("click", loadSaeGpsHistory);

document.getElementById("tab-btn-home").addEventListener("click", () => switchTab("home-view"));
document.getElementById("tab-btn-global").addEventListener("click", () => switchTab("global-view"));
document.getElementById("tab-btn-detail").addEventListener("click", () => switchTab("detail-view"));
document.getElementById("tab-btn-sae-gps").addEventListener("click", () => switchTab("sae-gps-view"));
document.getElementById("tab-btn-stats").addEventListener("click", () => switchTab("stats-view"));
document.getElementById("home-refresh-btn").addEventListener("click", loadHome);
document.getElementById("stats-refresh-btn").addEventListener("click", loadStats);
document.getElementById("choice-modal-cancel-btn").addEventListener("click", hideChoiceModal);

document.getElementById("detail-search-btn").addEventListener("click", searchVehicleFromInput);
document.getElementById("detail-vehicle-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchVehicleFromInput();
});

// No automatic load on page start - the user must click "Rafraîchir" explicitly.
document.getElementById("last-refresh").textContent = "Cliquez sur \"Rafraîchir\" pour charger les données.";
document.getElementById("sae-gps-last-refresh").textContent = "Cliquez sur \"Rafraîchir\" pour charger les données.";
