// Front-end logic for the supervision tool.
// Plain JavaScript, no build step, no framework - calls the FastAPI backend under /api.

const API_BASE = "/api";

// Shows a spinner + an elapsed-time counter next to a button, and disables
// it while an async action runs - so the user gets visual feedback and
// can't spam-click while a request is in flight. The counter keeps running
// live during the load, then STOPS (but stays visible, showing the final
// duration) once done - reset to 0 on the next click. Nothing is persisted
// anywhere (no browser storage) - purely in-memory for the current action.
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


// Quick reference to the relevant case in Procedure_maintenance_WB, shown to the
// maintainer once a door anomaly is identified, so they don't have to search for it.
// Which case is shown depends on how many doors are affected - see renderVehicleDetail.
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

// Vehicles fetched from the API are kept in memory here. Status and search
// filters are then applied client-side (see renderVehicleTable), with no
// extra API/DB call - only the "Rafraîchir" button re-fetches from BDD3.
let allVehicles = [];

// ---------- Generic sortable-table helper (used by all 3 tables) ----------

function toggleSort(state, column, rerenderFn, defaultDirection = "asc") {
  if (state.column === column) {
    state.direction = state.direction === "asc" ? "desc" : "asc";
  } else {
    state.column = column;
    state.direction = defaultDirection;
  }
  rerenderFn();
}

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

// ---------- Vue globale ----------

// Data already comes sorted by num_parc ascending from the API - initialize
// the sort state to match, so the "Véhicule" column shows its arrow by
// default (a visual hint that columns are clickable to sort).
const globalSortState = { column: "num_parc", direction: "asc" };
const globalHeaderIds = {
  num_parc: "col-vehicle",
  last_seen: "col-last-seen",
  last_exploitation: "col-last-exploitation",
  doors: "col-doors",
  status: "col-status",
};
const globalKeyGetters = {
  num_parc: v => v.num_parc,
  last_seen: v => v.last_seen || "",
  // Distinct sentinel keys so "stale" and "unknown" don't collide (equal
  // keys left them in original array order, looking like a broken sort).
  // Sentinels sort before any real ISO date string either way.
  last_exploitation: v => {
    if (v.exploitation_case === "known") return v.last_exploitation;
    return v.exploitation_case === "stale" ? "1" : "0";
  },
  doors: v => v.door_count_total - v.door_count_functional,
  status: v => v.status,
};

async function loadVehicles() {
  await withSpinner("refresh-btn", "refresh-spinner", "refresh-timer", async () => {
    const res = await fetch(API_BASE + "/vehicles");
    const data = await res.json();
    allVehicles = data.vehicles;

    populateTypeFilter();
    renderVehicleTable();
    document.getElementById("last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");

    renderStats();
  });
}

function populateTypeFilter() {
  const select = document.getElementById("type-filter");
  const previousValue = select.value;
  const types = [...new Set(allVehicles.map(v => v.rolling_stock_type))].sort();

  select.innerHTML = '<option value="">Tous</option>';
  types.forEach(type => {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = type;
    select.appendChild(option);
  });

  // Keep the previous selection if it's still a valid option after refresh
  if (types.includes(previousValue)) select.value = previousValue;
}

function renderVehicleTable() {
  const statusFilter = document.getElementById("status-filter").value;
  const typeFilter = document.getElementById("type-filter").value;
  const search = document.getElementById("vehicle-search").value.trim();
  const tbody = document.querySelector("#vehicle-table tbody");
  tbody.innerHTML = "";

  let rows = allVehicles
    .filter(v => !statusFilter || v.status === statusFilter)
    .filter(v => !typeFilter || v.rolling_stock_type === typeFilter)
    .filter(v => !search || String(v.num_parc).includes(search));

  rows = applySort(rows, globalSortState, globalKeyGetters);
  updateSortArrows(globalSortState, globalHeaderIds);

  rows.forEach(v => {
      const doorsDown = v.door_count_total - v.door_count_functional;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${v.num_parc}</td>
        <td>${formatDate(v.last_seen)}<br><small>${formatDuration(v.hours_since_last_seen)}</small></td>
        <td>${formatExploitation(v.last_exploitation, v.hours_since_last_exploitation, v.exploitation_case)}</td>
        <td class="${doorsDown > 0 ? 'status-anomaly' : 'status-ok'}">${doorsDown} / ${v.door_count_total}</td>
        <td class="${v.status === 'anomalie' ? 'status-anomaly' : 'status-ok'}">
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

function formatDate(isoString) {
  if (!isoString) return "Aucune donnée";
  return new Date(isoString).toLocaleString("fr-FR");
}

function formatDuration(hours, precise) {
  if (hours === null || hours === undefined) return "";
  if (precise && hours < 1) {
    const minutes = Math.round(hours * 60);
    if (minutes < 1) return "il y a moins d'1 minute";
    return `il y a ${minutes} min`;
  }
  if (hours < 1) return "il y a moins d'1 heure";
  if (hours < 48) return `depuis ${Math.floor(hours)} h`;
  const days = Math.floor(hours / 24);
  return `depuis ${days} jour${days > 1 ? "s" : ""}`;
}

// "known": real date + duration. "stale": operation_state seen (e.g. depot)
// but never 1/2 in the window - the vehicle almost certainly last ran
// before the window started. "unknown": operation_state never seen at all.
function formatExploitation(lastExploitation, hoursSince, exploitationCase) {
  if (exploitationCase === "stale") return "Depuis plus de 30 jours";
  if (!lastExploitation) return "Aucune donnée";
  return `${formatDate(lastExploitation)}<br><small>${formatDuration(hoursSince)}</small>`;
}

// ---------- Vue détail véhicule ----------

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

    // Default history range: last 7 days
    const today = new Date();
    const weekAgo = new Date(today);
    weekAgo.setDate(today.getDate() - 7);
    document.getElementById("history-end").value = today.toISOString().slice(0, 10);
    document.getElementById("history-start").value = weekAgo.toISOString().slice(0, 10);
    await loadHistory();
  });
}

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

  // The procedure reminder is driven purely by door status (including
  // "Aucune donnée" doors, which count as anomalie) - never by the
  // vehicle-level WEBOX status alone, so it doesn't show for a vehicle
  // whose doors are all fine.
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

// ---------- Vérification post-intervention (polling ciblé) ----------

function startLiveCheck() {
  const statusEl = document.getElementById("live-check-status");
  liveCheckStopAt = Date.now() + 15 * 60 * 1000; // stop automatically after 15 min
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
  }, 30 * 1000); // poll every 30 seconds
}

function stopLiveCheck() {
  if (liveCheckTimer) {
    clearInterval(liveCheckTimer);
    liveCheckTimer = null;
  }
}

// ---------- Historique ----------

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

function searchVehicleFromInput() {
  const value = document.getElementById("detail-vehicle-input").value.trim();
  if (!value) {
    alert("Veuillez saisir un numéro de véhicule.");
    return;
  }
  showVehicleDetail(value);
}

// ---------- Anomalies SAE / GPS ----------

// Fetched once per "Rafraîchir" click, then filtered client-side - same
// pattern as allVehicles for the global view.
let allSaeGpsVehicles = [];

async function loadSaeGpsAnomalies() {
  await withSpinner("sae-gps-refresh-btn", "sae-gps-refresh-spinner", "sae-gps-refresh-timer", async () => {
    const res = await fetch(`${API_BASE}/vehicles-sae-gps`);
    const data = await res.json();
    allSaeGpsVehicles = data.vehicles;

    populateSaeGpsTypeFilter();
    renderSaeGpsTables();

    document.getElementById("sae-gps-last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");

    renderStats();
  });
}

function populateSaeGpsTypeFilter() {
  const select = document.getElementById("sae-gps-type-filter");
  const previousValue = select.value;
  const types = [...new Set(allSaeGpsVehicles.map(v => v.rolling_stock_type))].sort();

  select.innerHTML = '<option value="">Tous</option>';
  types.forEach(type => {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = type;
    select.appendChild(option);
  });

  if (types.includes(previousValue)) select.value = previousValue;
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

function getFilteredSaeGpsVehicles(field) {
  const statusFilter = document.getElementById("sae-gps-status-filter").value;
  const typeFilter = document.getElementById("sae-gps-type-filter").value;
  const search = document.getElementById("sae-gps-search").value.trim();

  return allSaeGpsVehicles
    .filter(v => !statusFilter || v[field].status === statusFilter)
    .filter(v => !typeFilter || v.rolling_stock_type === typeFilter)
    .filter(v => !search || String(v.num_parc).includes(search));
}

function renderSaeGpsTables() {
  const saeRows = applySort(getFilteredSaeGpsVehicles("sae"), saeSortState, fieldKeyGetters("sae"));
  const gpsRows = applySort(getFilteredSaeGpsVehicles("gps"), gpsSortState, fieldKeyGetters("gps"));

  updateSortArrows(saeSortState, saeHeaderIds);
  updateSortArrows(gpsSortState, gpsHeaderIds);

  renderFieldTable("sae-table", saeRows, "sae");
  renderFieldTable("gps-table", gpsRows, "gps");
}

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

// ---------- Historique SAE / GPS ----------

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

// ---------- Statistiques ----------

// null = not yet fetched (section 4 shows a placeholder). Only refreshed
// via the stats tab's own "Rafraîchir" button, since it needs its own
// dedicated (60-day) backend query - unlike the other 4 sections, which
// are computed purely from allVehicles, already shared with the other tabs.
let lingeringVehicles = null;

async function loadStats() {
  await withSpinner("stats-refresh-btn", "stats-refresh-spinner", "stats-refresh-timer", async () => {
    // Reuses the exact same functions as the "Rafraîchir" buttons on the
    // Vue globale and Anomalies SAE/GPS tabs - so those tabs are also
    // up to date afterward, without needing a separate click there.
    await loadVehicles();
    await loadSaeGpsAnomalies();

    const res = await fetch(`${API_BASE}/stats/lingering`);
    const data = await res.json();
    lingeringVehicles = data.vehicles;

    renderStats();
    document.getElementById("stats-last-refresh").textContent =
      "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");
  });
}

function renderStats() {
  // Nothing loaded yet (page just opened, no tab refreshed) - keep the
  // placeholder instead of showing empty/misleading numbers.
  if (allVehicles.length === 0) return;

  document.getElementById("stats-placeholder").classList.add("hidden");
  document.getElementById("stats-content").classList.remove("hidden");

  // 1. État du parc
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

  // 2. Répartition par type de véhicule
  const byType = {};
  allVehicles.forEach(v => {
    if (!byType[v.rolling_stock_type]) byType[v.rolling_stock_type] = { anomalie: 0, total: 0 };
    byType[v.rolling_stock_type].total++;
    if (v.status === "anomalie") byType[v.rolling_stock_type].anomalie++;
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

  // 3. Nouvelles anomalies (7 derniers jours) - anomalie AND dernière
  // remontée datant de moins de 7 jours (168h). Simple liste, comme demandé.
  const newAnomalies = allVehicles.filter(v =>
    v.status === "anomalie" && v.hours_since_last_seen !== null && v.hours_since_last_seen <= 7 * 24
  );
  document.getElementById("stats-new-summary").textContent =
    `${newAnomalies.length} véhicule(s) en anomalie depuis moins de 7 jours.`;
  document.getElementById("stats-new-list").textContent =
    newAnomalies.length ? newAnomalies.map(v => v.num_parc).join(", ") : "—";

  // 4. Anomalies qui traînent (> 30 jours) - depuis l'endpoint dédié
  // /api/stats/lingering (fenêtre 30-60j, vérification approfondie).
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

  // 5. Durée moyenne des anomalies actuelles - estimation basée sur
  // "depuis quand n'a-t-on plus de bon signal", moins le seuil de 48h.
  // Les véhicules sans date connue (aucune donnée) ne peuvent pas être
  // moyennés numériquement - ils sont exclus de ce calcul uniquement.
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

// ---------- Navigation par onglets ----------

function switchTab(tabId) {
  document.querySelectorAll(".tab-panel").forEach(panel => {
    panel.classList.toggle("active", panel.id === tabId);
  });
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });

  // Stop the post-intervention polling if we navigate away from the detail tab
  if (tabId !== "detail-view") {
    stopLiveCheck();
  }
}

// ---------- Navigation & événements ----------

// Only the refresh button hits the backend/BDD3. Filters just re-render
// the data already in memory (allVehicles) - instant, no network call.
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

document.getElementById("tab-btn-global").addEventListener("click", () => switchTab("global-view"));
document.getElementById("tab-btn-detail").addEventListener("click", () => switchTab("detail-view"));
document.getElementById("tab-btn-sae-gps").addEventListener("click", () => switchTab("sae-gps-view"));
document.getElementById("tab-btn-stats").addEventListener("click", () => switchTab("stats-view"));
document.getElementById("stats-refresh-btn").addEventListener("click", loadStats);

document.getElementById("detail-search-btn").addEventListener("click", searchVehicleFromInput);
document.getElementById("detail-vehicle-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchVehicleFromInput();
});

// No automatic load on page start - the user must click "Rafraîchir" explicitly.
document.getElementById("last-refresh").textContent = "Cliquez sur \"Rafraîchir\" pour charger les données.";
document.getElementById("sae-gps-last-refresh").textContent = "Cliquez sur \"Rafraîchir\" pour charger les données.";
