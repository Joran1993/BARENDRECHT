/* De Bouwkringloop PWA */

const token = localStorage.getItem("token");
if (!token) location.href = "/login?redirect=/";

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) }
  });
  if (res.status === 401) { localStorage.clear(); location.href = "/login"; return null; }
  return res;
}
function logout() { localStorage.clear(); location.href = "/login"; }

// Dashboard-link verbergen voor scanners
const _role = localStorage.getItem("role") || "user";
if (_role === "user") {
  const dl = document.getElementById("dashboard-link");
  if (dl) dl.style.display = "none";
}

// ── State ─────────────────────────────────────────────────────────────────────
let allItems = [];
let stats = null;
let searchQuery = "";
let currentItemId = null;
let pendingFile = null;

// ── Tabs ──────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "list") renderItems();
    if (btn.dataset.tab === "stats") renderStats();
  });
});

// ── Sync ──────────────────────────────────────────────────────────────────────
async function syncItems() {
  const res = await apiFetch("/api/items?limit=200&offset=0");
  if (!res) return;
  allItems = await res.json();
  renderItems();
}
async function syncStats() {
  const res = await apiFetch("/api/stats");
  if (!res) return;
  stats = await res.json();
  renderStats();
}
function syncAll() { syncItems(); syncStats(); }

// ── Camera ────────────────────────────────────────────────────────────────────
const cameraInput = document.getElementById("camera-input");
const previewWrap = document.getElementById("preview-wrap");
const previewImg  = document.getElementById("preview-img");
const analyseBtn  = document.getElementById("analyse-btn");
const resultCard  = document.getElementById("result-card");
const errorMsg    = document.getElementById("error-msg");

cameraInput.addEventListener("change", () => {
  const file = cameraInput.files[0];
  if (!file) return;
  pendingFile = file;
  previewImg.src = URL.createObjectURL(file);
  previewWrap.style.display = "block";
  analyseBtn.style.display = "flex";
  resultCard.style.display = "none";
  errorMsg.style.display = "none";
});

document.getElementById("preview-clear").addEventListener("click", () => {
  pendingFile = null;
  cameraInput.value = "";
  previewWrap.style.display = "none";
  analyseBtn.style.display = "none";
  resultCard.style.display = "none";
  errorMsg.style.display = "none";
});

// ── GPS ───────────────────────────────────────────────────────────────────────
function getGPSLocation() {
  return new Promise(resolve => {
    if (!navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      pos => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      ()  => resolve(null),
      { timeout: 6000, maximumAge: 60000 }
    );
  });
}
async function getGemeenteFromCoords(lat, lon) {
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&addressdetails=1`,
      { headers: { "Accept-Language": "nl" } }
    );
    const data = await res.json();
    const addr = data.address || {};
    return addr.municipality || addr.city || addr.town || addr.village || null;
  } catch { return null; }
}

// ── Analyseren ────────────────────────────────────────────────────────────────
analyseBtn.addEventListener("click", async () => {
  if (!pendingFile) return;
  analyseBtn.disabled = true;
  document.getElementById("analyse-text").textContent = "Locatie bepalen...";
  resultCard.style.display = "none";
  errorMsg.style.display = "none";

  const gps = await getGPSLocation();
  let detectedGemeente = null;
  if (gps) {
    document.getElementById("analyse-text").textContent = "Gemeente bepalen...";
    detectedGemeente = await getGemeenteFromCoords(gps.lat, gps.lon);
  }
  document.getElementById("analyse-text").textContent = "AI analyseert...";

  const indicator = document.getElementById("locatie-indicator");
  if (detectedGemeente) {
    indicator.textContent = `Locatie: ${detectedGemeente}`;
    indicator.style.display = "block";
  } else {
    indicator.style.display = "none";
  }

  try {
    const fd = new FormData();
    fd.append("file", pendingFile);
    if (detectedGemeente) fd.append("gemeente_override", detectedGemeente);
    const res = await apiFetch("/api/upload", { method: "POST", body: fd });
    if (!res || !res.ok) {
      const err = res ? await res.json().catch(() => ({ detail: res.statusText })) : { detail: "Geen verbinding" };
      throw new Error(err.detail || res?.statusText);
    }
    const item = await res.json();
    document.getElementById("result-label").textContent = item.ai_label || "Niet herkend";
    document.getElementById("result-weight").textContent =
      item.gewicht_kg != null ? `Geschat gewicht: ${item.gewicht_kg} kg` : "";
    document.getElementById("result-detail").textContent = item.ai_detail || "";
    resultCard.style.display = "block";
    allItems.unshift(item);
    if (stats) { stats.total++; stats.today++; }
    pendingFile = null;
    cameraInput.value = "";
    previewWrap.style.display = "none";
    analyseBtn.style.display = "none";
  } catch (err) {
    errorMsg.textContent = "Fout: " + err.message;
    errorMsg.style.display = "block";
  } finally {
    analyseBtn.disabled = false;
    document.getElementById("analyse-text").textContent = "Analyseren & opslaan";
  }
});

// ── Lijst ─────────────────────────────────────────────────────────────────────
function renderItems() {
  const list = document.getElementById("items-list");
  const q = searchQuery.toLowerCase();
  const filtered = q
    ? allItems.filter(i =>
        (i.ai_label || "").toLowerCase().includes(q) ||
        (i.manual_note || "").toLowerCase().includes(q) ||
        (i.category || "").toLowerCase().includes(q))
    : allItems;
  if (!filtered.length) {
    list.innerHTML = `<p style="padding:24px 16px;color:var(--muted);">Geen items gevonden.</p>`;
    return;
  }
  list.innerHTML = filtered.map(item => `
    <div class="item-row" onclick="openModal(${item.id})">
      <img class="item-thumb" src="${item.photo_url}" alt="" onerror="this.style.opacity=0" loading="lazy">
      <div class="item-info">
        <div class="item-name">${item.ai_label || "Niet herkend"}</div>
        <div class="item-sub">
          ${item.gewicht_kg != null ? `<span class="item-kg">${item.gewicht_kg} kg</span>` : ""}
          ${item.category ? `<span class="item-badge">${item.category}</span>` : ""}
          <span>${formatTime(item.timestamp)}</span>
        </div>
      </div>
      <span class="item-chevron">›</span>
    </div>`).join("");
}

document.getElementById("search-input").addEventListener("input", e => {
  searchQuery = e.target.value;
  renderItems();
});

// ── Stats ─────────────────────────────────────────────────────────────────────
function renderStats() {
  if (!stats) return;
  document.getElementById("s-total").textContent = stats.total;
  document.getElementById("s-today").textContent = stats.today;
  document.getElementById("s-kg").textContent = (stats.totaal_kg ?? "–") + " kg";
  const catList = document.getElementById("categories-list");
  catList.innerHTML = stats.categories.length
    ? stats.categories.map(c => `
        <div class="cat-row">
          <span class="cat-name">${c.category}</span>
          <span class="cat-count">${c.count}x</span>
        </div>`).join("")
    : `<p style="color:var(--muted);font-size:0.9rem;padding:16px 20px;">Nog geen categorieën.</p>`;
}

// ── Modal ─────────────────────────────────────────────────────────────────────
function openModal(id) {
  const item = allItems.find(i => i.id === id);
  if (!item) return;
  currentItemId = id;
  document.getElementById("modal-img").src = item.photo_url || "";
  document.getElementById("modal-label").textContent = item.ai_label || "Niet herkend";
  document.getElementById("modal-weight").textContent =
    item.gewicht_kg != null ? `Geschat: ${item.gewicht_kg} kg` : "";
  document.getElementById("modal-detail").textContent = item.ai_detail || "";
  document.getElementById("modal-ts").textContent = new Date(item.timestamp).toLocaleString("nl-NL");
  document.getElementById("modal-note").value = item.manual_note || "";
  document.getElementById("modal-cat").value = item.category || "";
  document.getElementById("modal").style.display = "flex";
}

document.getElementById("modal-close").addEventListener("click", () => {
  document.getElementById("modal").style.display = "none";
});
document.getElementById("modal").addEventListener("click", e => {
  if (e.target === document.getElementById("modal"))
    document.getElementById("modal").style.display = "none";
});

document.getElementById("modal-save").addEventListener("click", async () => {
  if (!currentItemId) return;
  const note = document.getElementById("modal-note").value;
  const cat  = document.getElementById("modal-cat").value;
  const idx = allItems.findIndex(i => i.id === currentItemId);
  if (idx !== -1) { allItems[idx].manual_note = note; allItems[idx].category = cat; }
  document.getElementById("modal").style.display = "none";
  renderItems();
  const fd = new FormData();
  fd.append("manual_note", note); fd.append("category", cat);
  apiFetch(`/api/items/${currentItemId}`, { method: "PATCH", body: fd });
});

document.getElementById("modal-del").addEventListener("click", async () => {
  if (!currentItemId || !confirm("Verwijderen?")) return;
  const id = currentItemId;
  allItems = allItems.filter(i => i.id !== id);
  if (stats) stats.total = Math.max(0, stats.total - 1);
  document.getElementById("modal").style.display = "none";
  renderItems(); renderStats();
  apiFetch(`/api/items/${id}`, { method: "DELETE" });
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  const min = Math.floor((new Date() - d) / 60000);
  if (min < 1) return "Zojuist";
  if (min < 60) return `${min} min`;
  if (min < 1440) return `${Math.floor(min / 60)} uur`;
  return d.toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
}

// ── Init ──────────────────────────────────────────────────────────────────────
syncAll();
setInterval(syncAll, 60000);
