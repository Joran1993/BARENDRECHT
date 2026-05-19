let alleItems = [];
let geladen = 0;
const PER_PAGINA = 500;
let totaal = 0;
let bezig = false;
let zoekterm = "";
let huidigLightboxIdx = -1;

async function laadItems() {
  if (bezig || (geladen >= totaal && totaal > 0)) return;
  bezig = true;
  document.getElementById("loading").style.display = "";

  const res = await fetch(`/api/catalogus?gemeente=Almere&limit=${PER_PAGINA}&offset=${geladen}`);
  bezig = false;

  if (!res.ok) { document.getElementById("loading").textContent = "Laden mislukt."; return; }
  const data = await res.json();

  totaal = data.total;
  const isFirst = geladen === 0;
  alleItems = alleItems.concat(data.items);
  geladen += data.items.length;

  document.getElementById("loading").style.display = "none";

  renderGrid();
  updateTeller();
  updateLaadMeerKnop();
}

function filterItems() {
  zoekterm = document.getElementById("zoek").value.toLowerCase();
  renderGrid();
  updateTeller();
}

function gefilterdItems() {
  return alleItems.filter(i =>
    !zoekterm || (i.ai_label || "").toLowerCase().includes(zoekterm) || (i.ai_detail || "").toLowerCase().includes(zoekterm)
  );
}

function renderGrid() {
  const items = gefilterdItems();
  document.getElementById("grid").innerHTML = items.map((item, idx) => `
    <div class="cat-tegel" onclick="openLightboxIdx(${idx})" data-id="${item.id}">
      <img src="${item.photo_url_thumb || item.photo_url}" alt="${item.ai_label || ""}" loading="lazy" onerror="this.style.opacity=0.2">
      <div class="cat-tegel-overlay"><span>${item.ai_label || "Onbekend"}</span></div>
    </div>`).join("");
}

function updateTeller() {
  const n = gefilterdItems().length;
  document.getElementById("teller").textContent = `${n} item${n !== 1 ? "s" : ""}`;
}

function updateLaadMeerKnop() {
  const btn = document.getElementById("laad-meer");
  if (!btn) return;
  const resterend = totaal - geladen;
  btn.style.display = resterend > 0 ? "" : "none";
  if (resterend > 0) btn.textContent = `Laad meer (${resterend} resterend)`;
}

// Lightbox
function openLightboxIdx(idx) {
  const items = gefilterdItems();
  if (idx < 0 || idx >= items.length) return;
  huidigLightboxIdx = idx;
  const item = items[idx];
  const lb = document.getElementById("lightbox");
  lb.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  vulLightbox(item, idx, items.length);
}

function openLightbox(id) {
  const items = gefilterdItems();
  const idx = items.findIndex(i => i.id === id);
  if (idx >= 0) openLightboxIdx(idx);
}

function vulLightbox(item, idx, total) {
  const img = document.getElementById("lb-img");
  img.style.opacity = "0";
  img.src = item.photo_url || "";
  img.onload = () => { img.style.opacity = "1"; };
  document.getElementById("lb-label").textContent = item.ai_label || "Onbekend";
  document.getElementById("lb-kg").textContent = item.gewicht_kg != null ? item.gewicht_kg + " kg" : "";
  document.getElementById("lb-detail").textContent = item.ai_detail || "";
  document.getElementById("lb-kg").style.display = item.gewicht_kg != null ? "" : "none";
  document.getElementById("lb-teller").textContent = `${idx + 1} / ${total}`;
  document.getElementById("lb-prev").style.visibility = idx > 0 ? "visible" : "hidden";
  document.getElementById("lb-next").style.visibility = idx < total - 1 ? "visible" : "hidden";
}

function lbNavigeer(delta) {
  const items = gefilterdItems();
  const newIdx = huidigLightboxIdx + delta;
  if (newIdx < 0 || newIdx >= items.length) return;
  huidigLightboxIdx = newIdx;
  vulLightbox(items[newIdx], newIdx, items.length);
}

function sluitLightbox(e) {
  if (e && e.target !== document.getElementById("lightbox") && !e.target.classList.contains("lb-sluit")) return;
  document.getElementById("lightbox").classList.add("hidden");
  document.body.style.overflow = "";
}

// Touch swipe support
let touchStartX = 0;
document.getElementById("lightbox").addEventListener("touchstart", e => { touchStartX = e.touches[0].clientX; });
document.getElementById("lightbox").addEventListener("touchend", e => {
  const dx = e.changedTouches[0].clientX - touchStartX;
  if (Math.abs(dx) > 50) lbNavigeer(dx < 0 ? 1 : -1);
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape") sluitLightbox();
  if (e.key === "ArrowRight") lbNavigeer(1);
  if (e.key === "ArrowLeft") lbNavigeer(-1);
});

laadItems();
