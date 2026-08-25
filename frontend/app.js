// Front-end logic for the supervision tool.
// Plain JavaScript, no build step, no framework - calls the FastAPI backend under /api.

const API_BASE = "/api";

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

// Current column sort applied to the global view table.
// column: "num_parc" | "last_seen" | null (no sort = order returned by API)
let sortState = { column: null, direction: "asc" };

// ---------- Vue globale ----------

async function loadVehicles() {
  const res = await fetch(API_BASE + "/vehicles");
  const data = await res.json();
  allVehicles = data.vehicles;

  populateTypeFilter();
  renderVehicleTable();
  document.getElementById("last-refresh").textContent =
    "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");
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

  if (sortState.column) {
    const dir = sortState.direction === "asc" ? 1 : -1;
    rows = [...rows].sort((a, b) => {
      let valA = a[sortState.column];
      let valB = b[sortState.column];
      // "last_seen" is an ISO date string, "num_parc" is numeric - both compare fine with < / >
      if (valA < valB) return -1 * dir;
      if (valA > valB) return 1 * dir;
      return 0;
    });
  }

  updateSortArrows();

  rows.forEach(v => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${v.num_parc}</td>
        <td>${formatDate(v.last_seen)}<br><small>${formatDuration(v.hours_since_last_seen)}</small></td>
        <td>${formatExploitation(v.last_exploitation, v.hours_since_last_exploitation, v.exploitation_case)}</td>
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

function updateSortArrows() {
  document.querySelectorAll("#vehicle-table th.sortable .sort-arrow").forEach(el => el.textContent = "");
  if (!sortState.column) return;
  const headerId = sortState.column === "num_parc" ? "col-vehicle" : "col-last-seen";
  const arrow = sortState.direction === "asc" ? "▲" : "▼";
  document.querySelector(`#${headerId} .sort-arrow`).textContent = arrow;
}

function toggleSort(column) {
  if (sortState.column === column) {
    sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
  } else {
    sortState.column = column;
    sortState.direction = "asc";
  }
  renderVehicleTable();
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
  loadHistory();
}

function renderVehicleDetail(data) {
  document.getElementById("summary-num-parc").textContent = data.num_parc;
  document.getElementById("summary-door-ratio").textContent =
    `${data.door_count_functional} / ${data.door_count_total}`;
  document.getElementById("summary-rolling-stock").textContent = data.rolling_stock_type;

  populateHistoryDoorFilter(data.doors);

  const expText = document.getElementById("last-exploitation-text");
  if (data.exploitation_case === "stale") {
    expText.textContent = "Dernière exploitation : depuis plus de 30 jours (véhicule vu au dépôt sur la période, mais pas en service).";
  } else if (data.last_exploitation) {
    expText.textContent =
      `Dernière exploitation : ${formatDate(data.last_exploitation)} (${formatDuration(data.hours_since_last_exploitation)})`;
  } else {
    expText.textContent = "Dernière exploitation : aucune donnée d'exploitation trouvée sur la période chargée.";
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
  const res = await fetch(`${API_BASE}/vehicles-sae-gps`);
  const data = await res.json();
  allSaeGpsVehicles = data.vehicles;

  populateSaeGpsTypeFilter();
  renderSaeGpsTables();

  document.getElementById("sae-gps-last-refresh").textContent =
    "Dernier rafraîchissement : " + new Date().toLocaleTimeString("fr-FR");
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
  renderFieldTable("sae-table", getFilteredSaeGpsVehicles("sae"), "sae");
  renderFieldTable("gps-table", getFilteredSaeGpsVehicles("gps"), "gps");
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
document.getElementById("col-vehicle").addEventListener("click", () => toggleSort("num_parc"));
document.getElementById("col-last-seen").addEventListener("click", () => toggleSort("last_seen"));
document.getElementById("live-check-btn").addEventListener("click", startLiveCheck);
document.getElementById("history-btn").addEventListener("click", loadHistory);
document.getElementById("history-door-filter").addEventListener("change", loadHistory);
document.getElementById("sae-gps-refresh-btn").addEventListener("click", loadSaeGpsAnomalies);
document.getElementById("sae-gps-status-filter").addEventListener("change", renderSaeGpsTables);
document.getElementById("sae-gps-type-filter").addEventListener("change", renderSaeGpsTables);
document.getElementById("sae-gps-search").addEventListener("input", renderSaeGpsTables);
document.getElementById("sae-gps-history-btn").addEventListener("click", loadSaeGpsHistory);

document.getElementById("tab-btn-global").addEventListener("click", () => switchTab("global-view"));
document.getElementById("tab-btn-detail").addEventListener("click", () => switchTab("detail-view"));
document.getElementById("tab-btn-sae-gps").addEventListener("click", () => switchTab("sae-gps-view"));

document.getElementById("detail-search-btn").addEventListener("click", searchVehicleFromInput);
document.getElementById("detail-vehicle-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchVehicleFromInput();
});

// No automatic load on page start - the user must click "Rafraîchir" explicitly.
document.getElementById("last-refresh").textContent = "Cliquez sur \"Rafraîchir\" pour charger les données.";
document.getElementById("sae-gps-last-refresh").textContent = "Cliquez sur \"Rafraîchir\" pour charger les données.";
