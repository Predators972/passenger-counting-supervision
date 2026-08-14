// Front-end logic for the supervision tool.
// Plain JavaScript, no build step, no framework - calls the FastAPI backend under /api.

const API_BASE = "/api";

// Quick reference to the relevant case in Procedure_maintenance_WB, shown to the
// maintainer once an anomaly is identified, so they don't have to search for it.
const PROCEDURE_HINTS = {
  vehicle: `
    <strong>Anomalie véhicule (WEBOX) :</strong> aucune remontée depuis plus de 2 jours.
    Voir "Cas 1 - Pas de communication 4G" dans la procédure de maintenance :
    vérifier l'antenne 4G, la carte SIM, redémarrer la WEBOX, sinon la remplacer.
  `,
  door: `
    <strong>Anomalie porte (EYES) :</strong> une porte ne remonte plus alors que
    les autres fonctionnent. Voir "Cas 1 - Pas de signal dans CARE3" (EYES) dans
    la procédure de maintenance : vérifier la LED de l'EYES, les câbles M12/Molex,
    le port du switch correspondant.
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
        <td>${v.last_exploitation ? formatDate(v.last_exploitation) + '<br><small>' + formatDuration(v.hours_since_last_exploitation) + '</small>' : 'Aucune donnée'}</td>
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
  if (data.last_exploitation) {
    expText.textContent =
      `Dernière exploitation : ${formatDate(data.last_exploitation)} (${formatDuration(data.hours_since_last_exploitation)})`;
  } else {
    expText.textContent = "Dernière exploitation : aucune donnée d'exploitation trouvée sur la période chargée.";
  }

  const grid = document.getElementById("door-grid");
  grid.innerHTML = "";
  let hasDoorAnomaly = false;

  data.doors.forEach(d => {
    if (d.status === "anomalie") hasDoorAnomaly = true;
    const cell = document.createElement("div");
    cell.className = `door-cell ${d.status === 'anomalie' ? 'door-anomaly' : 'door-ok'}`;
    cell.innerHTML = `
      <span class="door-label">Porte ${d.porte_physique}</span>
      <span class="door-timestamp">${formatDate(d.last_seen)}<br>${formatDuration(d.hours_since_last_seen, true)}</span>
    `;
    grid.appendChild(cell);
  });

  const hintEl = document.getElementById("procedure-hint");
  if (data.status === "anomalie" && !hasDoorAnomaly) {
    hintEl.innerHTML = PROCEDURE_HINTS.vehicle;
    hintEl.classList.remove("hidden");
  } else if (hasDoorAnomaly) {
    hintEl.innerHTML = PROCEDURE_HINTS.door;
    hintEl.classList.remove("hidden");
  } else {
    hintEl.classList.add("hidden");
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

document.getElementById("tab-btn-global").addEventListener("click", () => switchTab("global-view"));
document.getElementById("tab-btn-detail").addEventListener("click", () => switchTab("detail-view"));

document.getElementById("detail-search-btn").addEventListener("click", searchVehicleFromInput);
document.getElementById("detail-vehicle-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchVehicleFromInput();
});

// Initial load
loadVehicles();
