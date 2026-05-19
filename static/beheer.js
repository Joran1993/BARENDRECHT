/* De Bouwkringloop — Beheerpagina */

const CATEGORIES = ["Hout", "Metaal", "Beton / steen", "Glas", "Kunststof", "Gevaarlijk afval", "Overig"];

const token = localStorage.getItem("token");
if (!token) location.href = "/login?redirect=/beheer";

const role     = localStorage.getItem("role") || "user";
const gemeente = localStorage.getItem("gemeente") || "";
const username = localStorage.getItem("username") || "";

// Alleen superadmin mag hier
if (role !== "superadmin") { location.replace("/"); throw new Error("Geen toegang"); }

document.documentElement.style.visibility = '';

// Superadmin ziet ook het gemeenten-tabblad
document.getElementById("tab-gemeenten-btn").style.display = "flex";

// Admin ziet geen gemeente-keuze (al gekoppeld aan hun gemeente)
if (role === "admin") {
  document.getElementById("nu-gemeente-field").classList.add("hidden");
}

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) }
  });
  if (res.status === 401) { localStorage.clear(); location.href = "/login"; return null; }
  return res;
}

function logout() { localStorage.clear(); location.href = "/login"; }

async function loginAls(userId) {
  const res = await apiFetch(`/api/auth/impersonate/${userId}`, { method: "POST" });
  if (!res || !res.ok) { alert("Kon niet inloggen als deze gebruiker."); return; }
  const data = await res.json();
  const params = new URLSearchParams({
    _imp_token:       data.token,
    _imp_user_id:     data.user_id,
    _imp_username:    data.username,
    _imp_role:        data.role,
    _imp_gemeente:    data.gemeente || "",
    _imp_organisatie: data.organisatie || "",
    _imp_bedrijf_id:  data.bedrijf_id || "",
  });
  window.open(`/?${params}`, "_blank");
}

// ── Tabs ───────────────────────────────────────────────────────────────────────
document.querySelectorAll(".tabbar-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabbar-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "gebruikers")    loadUsers();
    if (btn.dataset.tab === "bedrijven")     loadBedrijven();
    if (btn.dataset.tab === "gemeenten")     loadGemeenten();
    if (btn.dataset.tab === "inzamellijst")  loadInzamellijst();
  });
});

// ── Gemeenten laden voor dropdowns ─────────────────────────────────────────────
async function loadGemeenteOptions() {
  if (role === "admin") {
    // Admin: gemeente-veld bij gebruiker verbergen, bij bedrijf voorinvullen
    document.getElementById("nu-gemeente-field").classList.add("hidden");
    const nbGem = document.getElementById("nb-gemeente");
    nbGem.value = gemeente;
    nbGem.readOnly = true;
    nbGem.style.opacity = "0.6";
    return;
  }
  // Superadmin: gemeenten + milieustraten ophalen
  const [gemRes, milRes] = await Promise.all([
    apiFetch("/api/gemeenten"),
    apiFetch("/api/milieustraten"),
  ]);
  if (!gemRes) return;
  const lijst = await gemRes.json();
  const milieustraten = (milRes && milRes.ok) ? await milRes.json() : {};

  const SEL_STYLE = "width:100%;height:42px;border:1px solid var(--border);border-radius:10px;padding:0 12px;font-family:'Space Grotesk',sans-serif;font-size:0.9rem;background:var(--surface);color:var(--ink);";

  function vulMilSel(milSel, gemeente) {
    const ms = milieustraten[gemeente] || [];
    if (!ms.length) { milSel.style.display = "none"; return; }
    milSel.innerHTML = '<option value="">— Selecteer milieustraat —</option>' +
      ms.map(m => `<option value="${m}">${m}</option>`).join("");
    milSel.style.display = "";
    if (ms.length === 1) milSel.value = ms[0];
  }

  // ── Nieuwe gebruiker: gemeente + milieustraat cascade ────────────────────
  const nuSel = document.getElementById("nu-gemeente");
  nuSel.innerHTML = '<option value="">— Selecteer gemeente —</option>' +
    lijst.map(g => `<option value="${g}">${g}</option>`).join("");

  let nuMilSel = document.getElementById("nu-milieustraat");
  if (!nuMilSel) {
    nuMilSel = document.createElement("select");
    nuMilSel.id = "nu-milieustraat";
    nuMilSel.style.cssText = SEL_STYLE + "margin-top:6px;display:none;";
    nuMilSel.innerHTML = '<option value="">— Selecteer milieustraat —</option>';
    nuSel.parentNode.insertBefore(nuMilSel, nuSel.nextSibling);
  }
  nuSel.addEventListener("change", function () { vulMilSel(nuMilSel, this.value); });

  // ── Inzamellijst gemeente select ─────────────────────────────────────────
  const ilSel = document.getElementById("il-gemeente");
  if (ilSel) {
    lijst.forEach(g => {
      const opt = document.createElement("option");
      opt.value = g; opt.textContent = g;
      ilSel.appendChild(opt);
    });
    ilSel.addEventListener("change", loadInzamellijst);
    document.getElementById("il-gemeente-field").style.display = "block";
  }

  // ── Nieuw bedrijf: gemeente + milieustraat cascade ───────────────────────
  const nbGemInput = document.getElementById("nb-gemeente");
  if (nbGemInput && nbGemInput.tagName === "INPUT") {
    const nbSel = document.createElement("select");
    nbSel.id = "nb-gemeente";
    nbSel.style.cssText = SEL_STYLE;
    nbSel.innerHTML = '<option value="">— Selecteer gemeente —</option>' +
      lijst.map(g => `<option value="${g}">${g}</option>`).join("");
    nbGemInput.parentNode.replaceChild(nbSel, nbGemInput);

    const nbMilSel = document.createElement("select");
    nbMilSel.id = "nb-milieustraat";
    nbMilSel.style.cssText = SEL_STYLE + "margin-top:6px;display:none;";
    nbMilSel.innerHTML = '<option value="">— Selecteer milieustraat —</option>';
    nbSel.parentNode.insertBefore(nbMilSel, nbSel.nextSibling);
    nbSel.addEventListener("change", function () { vulMilSel(nbMilSel, this.value); });
  }
}

// ── Categorie checkboxes opbouwen ──────────────────────────────────────────────
function buildCatCheckboxes(containerId, selected = []) {
  const wrap = document.getElementById(containerId);
  wrap.innerHTML = "";
  CATEGORIES.forEach(cat => {
    const label = document.createElement("label");
    label.className = "cat-check" + (selected.includes(cat) ? " checked" : "");
    label.innerHTML = `<input type="checkbox" value="${cat}" ${selected.includes(cat) ? "checked" : ""}>${cat}`;
    label.querySelector("input").addEventListener("change", e => {
      label.classList.toggle("checked", e.target.checked);
    });
    wrap.appendChild(label);
  });
}

function getCheckedCats(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} input:checked`)).map(i => i.value);
}

// ── Gebruikers laden ───────────────────────────────────────────────────────────
let _alleBedrijven = [];
let _openUserEdit = null;

async function loadUsers() {
  const [resUsers, resBedrijven] = await Promise.all([
    apiFetch("/api/users"),
    apiFetch("/api/bedrijven"),
  ]);
  if (!resUsers) return;
  const users    = await resUsers.json();
  _alleBedrijven = resBedrijven ? await resBedrijven.json() : [];

  const rlabel = { superadmin: "Platform", admin: "Beheerder", user: "Scanner", bedrijf: "Afnemer" };
  const rcls   = { superadmin: "badge-orange", admin: "badge-blue", user: "badge-neutral", bedrijf: "badge-green" };
  const list   = document.getElementById("users-list");

  if (!users.length) {
    list.innerHTML = `<p style="padding:20px 16px;color:var(--muted);">Nog geen gebruikers.</p>`;
    return;
  }

  list.innerHTML = users.map(u => `
    <div class="user-row" id="user-row-${u.id}" style="flex-wrap:wrap;">
      <div class="user-info" style="cursor:pointer;flex:1;" onclick="toggleUserEdit(${u.id})">
        <div class="user-name">${u.username}</div>
        <div class="user-meta">
          <span class="badge ${rcls[u.role] || ''}">${rlabel[u.role] || u.role}</span>
          ${u.gemeente ? `<span class="badge badge-neutral">${u.gemeente}</span>` : ""}
          ${u.role === "bedrijf" && u.bedrijf_id ? `<span class="badge badge-neutral">gekoppeld</span>` : ""}
        </div>
      </div>
      <button class="btn-view-as" onclick="event.stopPropagation();loginAls(${u.id})" title="Bekijk app als deze gebruiker">👁</button>
      ${u.username !== username ? `<button class="btn-del" onclick="event.stopPropagation();deleteUser(${u.id}, '${u.username}')">Verwijder</button>` : ""}
      <div class="user-edit-panel" id="user-edit-${u.id}" style="display:none;width:100%;padding:12px 0 4px;">
        <div class="field" style="margin-bottom:10px;">
          <label>Rol wijzigen</label>
          <select id="user-rol-${u.id}">
            <option value="user"    ${u.role==="user"    ? "selected":""}>Scanner</option>
            <option value="admin"   ${u.role==="admin"   ? "selected":""}>Beheerder</option>
            <option value="bedrijf" ${u.role==="bedrijf" ? "selected":""}>Afnemer (bedrijf)</option>
          </select>
        </div>
        <div class="field" id="user-bedrijf-field-${u.id}" style="margin-bottom:12px;${u.role!=='bedrijf'?'display:none;':''}">
          <label>Koppel aan bedrijf</label>
          <select id="user-bedrijf-${u.id}">
            <option value="">— Selecteer bedrijf —</option>
            ${_alleBedrijven.map(b => `<option value="${b.id}" ${u.bedrijf_id===b.id?"selected":""}>${b.naam}${b.gemeente?" ("+b.gemeente+")":""}</option>`).join("")}
          </select>
        </div>
        <button class="btn btn-primary" style="padding:10px 20px;font-size:0.78rem;" onclick="slaRolOp(${u.id})">Opslaan</button>
      </div>
    </div>`).join("");

  // Toon/verberg bedrijf-select als rol verandert
  users.forEach(u => {
    const rolSel = document.getElementById(`user-rol-${u.id}`);
    if (rolSel) rolSel.addEventListener("change", () => {
      const bf = document.getElementById(`user-bedrijf-field-${u.id}`);
      if (bf) bf.style.display = rolSel.value === "bedrijf" ? "block" : "none";
    });
  });
}

function toggleUserEdit(id) {
  const panel = document.getElementById(`user-edit-${id}`);
  if (!panel) return;
  const open = panel.style.display !== "none";
  // Sluit eerder geopend paneel
  if (_openUserEdit && _openUserEdit !== id) {
    const prev = document.getElementById(`user-edit-${_openUserEdit}`);
    if (prev) prev.style.display = "none";
  }
  panel.style.display = open ? "none" : "block";
  _openUserEdit = open ? null : id;
}

async function slaRolOp(userId) {
  const rolSel    = document.getElementById(`user-rol-${userId}`);
  const bedrijfSel = document.getElementById(`user-bedrijf-${userId}`);
  const rol       = rolSel.value;
  const bedrijfId = bedrijfSel && rol === "bedrijf" ? (bedrijfSel.value || null) : null;

  const fd = new FormData();
  fd.append("role", rol);
  if (bedrijfId) fd.append("bedrijf_id", bedrijfId);

  const res = await apiFetch(`/api/users/${userId}/role`, { method: "PATCH", body: fd });
  if (!res || !res.ok) { alert("Opslaan mislukt"); return; }
  loadUsers();
}

async function gebruikerToevoegen() {
  const uname = document.getElementById("nu-username").value.trim();
  const pwd   = document.getElementById("nu-password").value;
  const gem   = role === "admin" ? gemeente : document.getElementById("nu-gemeente").value;
  const r     = document.getElementById("nu-rol").value;
  const fout  = document.getElementById("nu-fout");
  fout.style.display = "none";

  if (!uname || !pwd) {
    fout.textContent = "Vul gebruikersnaam en wachtwoord in.";
    fout.style.display = "block"; return;
  }
  if (role === "superadmin" && !gem) {
    fout.textContent = "Selecteer een gemeente.";
    fout.style.display = "block"; return;
  }

  const fd = new FormData();
  fd.append("username", uname); fd.append("password", pwd);
  fd.append("gemeente", gem);   fd.append("role", r);
  const res = await apiFetch("/api/users", { method: "POST", body: fd });
  if (!res || !res.ok) {
    fout.textContent = "Aanmaken mislukt — gebruikersnaam al in gebruik?";
    fout.style.display = "block"; return;
  }
  document.getElementById("nu-username").value = "";
  document.getElementById("nu-password").value = "";
  loadUsers();
}

async function deleteUser(id, naam) {
  if (!confirm(`Gebruiker "${naam}" verwijderen?`)) return;
  const res = await apiFetch(`/api/users/${id}`, { method: "DELETE" });
  if (!res || !res.ok) { alert("Verwijderen mislukt"); return; }
  loadUsers();
}

// ── Bedrijven ──────────────────────────────────────────────────────────────────
async function loadBedrijven() {
  const res = await apiFetch("/api/bedrijven");
  if (!res) return;
  const data = await res.json();
  const list = document.getElementById("bedrijven-list");
  if (!data.length) {
    list.innerHTML = `<p style="padding:20px 16px;color:var(--muted);">Nog geen bedrijven.</p>`;
    return;
  }
  list.innerHTML = data.map(b => {
    const link = `${location.origin}/bedrijf/${b.meld_token}`;
    return `
    <div class="bedrijf-card">
      <div class="bedrijf-naam">${b.naam}</div>
      <div class="bedrijf-meta">
        ${b.gemeente ? `${b.gemeente}` : ""}
        ${b.contactpersoon ? ` · ${b.contactpersoon}` : ""}
        ${b.email ? ` · <a href="mailto:${b.email}" style="color:var(--orange)">${b.email}</a>` : ""}
        ${b.telefoon ? ` · ${b.telefoon}` : ""}
      </div>
      ${b.categorieen.length ? `<div class="bedrijf-cats">${b.categorieen.map(c => `<span class="bedrijf-cat">${c}</span>`).join("")}</div>` : ""}
      <div style="margin:10px 0 8px;font-size:0.72rem;color:var(--muted);">
        Link voor bedrijf:
        <a href="${link}" target="_blank" style="color:var(--orange);word-break:break-all;">${link}</a>
        <button class="btn-sm" style="margin-left:6px;" onclick="navigator.clipboard.writeText('${link}').then(()=>alert('Gekopieerd!'))">Kopieer</button>
      </div>
      <button class="btn-del" onclick="deleteBedrijf(${b.id}, '${b.naam}')">Verwijder</button>
    </div>`;
  }).join("");
}

async function bedrijfToevoegen() {
  const naam    = document.getElementById("nb-naam").value.trim();
  const gem     = document.getElementById("nb-gemeente").value.trim();
  const contact = document.getElementById("nb-contact").value.trim();
  const email   = document.getElementById("nb-email").value.trim();
  const tel     = document.getElementById("nb-tel").value.trim();
  const cats    = getCheckedCats("nb-cats");
  const fout    = document.getElementById("nb-fout");
  fout.style.display = "none";

  if (!naam) { fout.textContent = "Vul een bedrijfsnaam in."; fout.style.display = "block"; return; }
  if (!gem)  { fout.textContent = "Selecteer een gemeente."; fout.style.display = "block"; return; }

  const loginUser = document.getElementById("nb-login-username").value.trim();
  const loginPwd  = document.getElementById("nb-login-password").value;

  const fd = new FormData();
  fd.append("naam", naam); fd.append("gemeente", gem);
  fd.append("contactpersoon", contact); fd.append("email", email);
  fd.append("telefoon", tel); fd.append("categorieen", cats.join(","));
  fd.append("login_username", loginUser);
  fd.append("login_password", loginPwd);

  const res = await apiFetch("/api/bedrijven", { method: "POST", body: fd });
  if (!res || !res.ok) { fout.textContent = "Aanmaken mislukt — gebruikersnaam al in gebruik?"; fout.style.display = "block"; return; }

  document.getElementById("nb-naam").value           = "";
  document.getElementById("nb-contact").value        = "";
  document.getElementById("nb-email").value          = "";
  document.getElementById("nb-tel").value            = "";
  document.getElementById("nb-login-username").value = "";
  document.getElementById("nb-login-password").value = "";
  buildCatCheckboxes("nb-cats");
  loadBedrijven();
}

async function deleteBedrijf(id, naam) {
  if (!confirm(`Bedrijf "${naam}" verwijderen?`)) return;
  const res = await apiFetch(`/api/bedrijven/${id}`, { method: "DELETE" });
  if (!res || !res.ok) { alert("Verwijderen mislukt"); return; }
  loadBedrijven();
}

// ── Gemeenten laden ────────────────────────────────────────────────────────────
async function loadGemeenten() {
  const res = await apiFetch("/api/gemeenten/stats");
  if (!res) return;
  const data = await res.json();
  const list = document.getElementById("gemeenten-list");
  if (!data.length) {
    list.innerHTML = `<p style="padding:20px 16px;color:var(--muted);">Nog geen gemeenten.</p>`;
    return;
  }
  list.innerHTML = data.map(g => `
    <div class="gemeente-row" onclick="openGemeenteDashboard('${g.gemeente}')" style="cursor:pointer;">
      <div>
        <div class="gemeente-naam">${g.gemeente}</div>
        <div class="gemeente-meta">${g.user_count} scanner${g.user_count !== 1 ? 's' : ''} · ${g.item_count} items · ${(g.totaal_kg ?? 0).toFixed(1)} kg · <span style="color:#2e7d32;font-weight:700;">${((g.totaal_kg ?? 0) * 3.5).toFixed(1)} kg CO₂ bespaard</span></div>
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
    </div>`).join("");
}

async function openGemeenteDashboard(gem) {
  const panel = document.getElementById("gemeente-detail");
  document.getElementById("gd-naam").textContent = gem;
  document.getElementById("gd-body").innerHTML = `<p style="padding:20px 16px;color:var(--muted);">Laden…</p>`;
  panel.classList.add("open");

  const [dashRes, itemsRes] = await Promise.all([
    apiFetch(`/api/dashboard?gemeente=${encodeURIComponent(gem)}&days=30`),
    apiFetch(`/api/items?gemeente=${encodeURIComponent(gem)}&limit=50`),
  ]);
  if (!dashRes) return;
  const dash  = await dashRes.json();
  const items = itemsRes ? await itemsRes.json() : [];
  const s = dash.stats || {};

  const statCards = [
    { label: "Items gescand", value: s.total_items ?? 0 },
    { label: "Kg ingezameld", value: (s.totaal_kg ?? 0).toFixed(1) },
    { label: "CO₂ bespaard", value: ((s.totaal_kg ?? 0) * 3.5).toFixed(1) + " kg" },
    { label: "Aanbiedingen", value: s.total_aanbiedingen ?? 0 },
  ].map(c => `
    <div class="gd-stat">
      <div class="gd-stat-val">${c.value}</div>
      <div class="gd-stat-lbl">${c.label}</div>
    </div>`).join("");

  const itemRows = items.slice(0, 20).map(it => `
    <div class="gd-item-row">
      ${it.photo_url ? `<img src="${it.photo_url}" class="gd-thumb" alt="">` : `<div class="gd-thumb gd-thumb-empty"></div>`}
      <div class="gd-item-info">
        <div class="gd-item-label">${it.ai_label || "Onbekend"}</div>
        <div class="gd-item-meta">${it.gewicht_kg ? it.gewicht_kg + " kg · " : ""}${it.category || ""}</div>
      </div>
      ${it.bedrijf_naam ? `<span class="badge badge-green" style="flex-shrink:0;">${it.bedrijf_naam}</span>` : (it.status === "aangeboden" ? `<span class="badge badge-orange" style="flex-shrink:0;">Aangeboden</span>` : "")}
    </div>`).join("");

  document.getElementById("gd-body").innerHTML = `
    <div class="gd-stats">${statCards}</div>
    <div class="gd-section-title">Recente items (${items.length})</div>
    ${itemRows || `<p style="padding:12px 16px;color:var(--muted);font-size:0.82rem;">Nog geen items.</p>`}
  `;
}

function closeGemeenteDashboard() {
  document.getElementById("gemeente-detail").classList.remove("open");
}

async function gemeenteToevoegen() {
  const naam  = document.getElementById("ng-naam").value.trim();
  const admin = document.getElementById("ng-admin").value.trim();
  const pwd   = document.getElementById("ng-pwd").value;
  const fout  = document.getElementById("ng-fout");
  fout.style.display = "none";

  if (!naam || !admin || !pwd) {
    fout.textContent = "Vul alle velden in.";
    fout.style.display = "block"; return;
  }
  const fd = new FormData();
  fd.append("username", admin); fd.append("password", pwd);
  fd.append("gemeente", naam);  fd.append("role", "admin");
  const res = await apiFetch("/api/users", { method: "POST", body: fd });
  if (!res || !res.ok) {
    fout.textContent = "Aanmaken mislukt — gebruikersnaam al in gebruik?";
    fout.style.display = "block"; return;
  }
  document.getElementById("ng-naam").value  = "";
  document.getElementById("ng-admin").value = "";
  document.getElementById("ng-pwd").value   = "";
  loadGemeenten();
  loadGemeenteOptions();
}

// ── Inzamellijst — Bouwproductlijst ──────────────────────────────────────────

const BOUWPRODUCTLIJST = [
  { nr:1,  naam:"Steen", subgroepen:[
    { naam:"Bouwen", producten:["Metselsteen","Bouwblokken gasbeton en gips","Cement en stucwerk","Beton","Natuursteen","Dakpannen"] },
    { naam:"Vloer",  producten:["Bestratingsmateriaal","Bodem (zand, grind, mest)","Tegels buiten","Vensterbanken"] },
  ]},
  { nr:2,  naam:"Balken, palen, latten en strips", subgroepen:[
    { naam:"", producten:["Hout (balken/palen)","Metaal (balken/profielen)","Beton (balken)","Kunststof (balken)","Strips en profielen","Buizen, staven en stangen"] },
  ]},
  { nr:3,  naam:"Platen en rollen", subgroepen:[
    { naam:"", producten:["Hout (platen)","Gipsplaat","Kunststof en rubber (platen)","Metaal (platen)","Multiplex en underlayment","Spaanplaat, hardboard en OSB","Sandwichplaat","Isolatiemateriaal","Glas","Aanrechtbladen","Dakbedekking en toebehoren","Bouwfolies"] },
  ]},
  { nr:4,  naam:"Ramen, kozijnen, puien en deuren", subgroepen:[
    { naam:"", producten:["Kunststof (ramen/kozijnen)","Hout (ramen/kozijnen)","Metaal (ramen/kozijnen)","Dakramen","Voordeur","Binnendeur board","Glasdeur","Branddeur"] },
  ]},
  { nr:5,  naam:"IJzerwaren", subgroepen:[
    { naam:"Hang- en sluitwerk",  producten:["Scharnieren","Sloten","Handgrepen","Ventilatieroosters","Deurdrangers en -veren"] },
    { naam:"Montagemateriaal",    producten:["Schroeven, spijkers en pluggen","IJzerwaren overig"] },
  ]},
  { nr:6,  naam:"Verlichting en Elektra", subgroepen:[
    { naam:"Montagemateriaal", producten:["Elektriciteitsleidingen","Elektriciteitsdozen","Schakelmateriaal"] },
    { naam:"Producten",        producten:["Ventilatoren","Lampen en fittingen","Alarm en beveiliging","Zonnepanelen en -boilers"] },
  ]},
  { nr:7,  naam:"Water, verwarming en afvoer", subgroepen:[
    { naam:"Water, riool en CV", producten:["Kunststof buizen en hulpstukken","Leidingmateriaal","CV radiatoren","Brandslangen","Dakgoten en buizen"] },
    { naam:"Gas",                producten:["Geiser, boiler en ketel","Branders en ketels"] },
  ]},
  { nr:8,  naam:"Sanitair en keuken", subgroepen:[
    { naam:"Tegels",   producten:["Tegels vloer binnen","Tegels wand binnen","Toiletpotten"] },
    { naam:"Sanitair", producten:["Wasbakken","Douchebakken","Doucheschermen en gordijnen","Kranen","Spiegels","Badkameraccessoires"] },
    { naam:"Keuken",   producten:["Keuken- en badkamerkastjes","Kookplaten","Witgoed en apparatuur"] },
  ]},
  { nr:9,  naam:"Verf en decoratie", subgroepen:[
    { naam:"Schilderwerk", producten:["Beits","Latex","Verf waterbasis","Verf terpentinebasis","Kwasten en toebehoren","Verdunning en olie","Coating","Kitspuiten","Potten lijm en kit"] },
    { naam:"Decoratie",    producten:["Behang","Zonwering en gordijnen"] },
  ]},
  { nr:10, naam:"Vloer en plafond", subgroepen:[
    { naam:"", producten:["Laminaat, parket en vloerplanken","Tapijt en ondertapijt","Vloertegels","Schroten en plafondplaten"] },
  ]},
  { nr:11, naam:"Gereedschap", subgroepen:[
    { naam:"", producten:["Elektrisch gereedschap","Handgereedschap","Tuingereedschap","Werkplaatsinrichting"] },
  ]},
  { nr:12, naam:"Tuin en terras", subgroepen:[
    { naam:"", producten:["Zaden en tuinmiddelen","Tuinslangen","Tuinschuttingen","Netten, hekken en gaas","Groen","Tuinmeubilair en barbecue","Bloempotten en -bakken","Regentonnen"] },
  ]},
  { nr:13, naam:"Fiets en auto", subgroepen:[
    { naam:"", producten:["Auto-accessoires","Fietsonderdelen"] },
  ]},
  { nr:14, naam:"Bijzondere bouwelementen", subgroepen:[
    { naam:"", producten:["Trap, brug, jacuzzi, vijver e.d."] },
  ]},
];

let _ilGemeente = gemeente;
let _autoSaveTimer = null;

function _esc(s) {
  return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
}

function renderProductCheckboxes(acceptedSet) {
  const container = document.getElementById("inzamellijst-list");
  container.innerHTML = BOUWPRODUCTLIJST.map(hg => {
    const alleProducten = hg.subgroepen.flatMap(s => s.producten);
    const aantalChecked = alleProducten.filter(p => acceptedSet.has(p)).length;
    const alChecked     = aantalChecked === alleProducten.length;
    const deelsChecked  = aantalChecked > 0 && !alChecked;

    const subHTML = hg.subgroepen.map(sg => {
      const sgChecked = sg.producten.filter(p => acceptedSet.has(p)).length;
      const sgAl      = sgChecked === sg.producten.length;
      const sgDeels   = sgChecked > 0 && !sgAl;

      const items = sg.producten.map(p => `
        <label class="product-item">
          <input type="checkbox" class="product-cb" data-product="${_esc(p)}"
            ${acceptedSet.has(p) ? "checked" : ""}
            onchange="onProductChange(this)">
          <span>${_esc(p)}</span>
        </label>`).join("");

      if (!sg.naam) return `<div class="product-producten">${items}</div>`;

      return `
        <div class="product-subgroep" data-sectie="${hg.nr}">
          <label class="product-subgroep-hdr" onclick="event.stopPropagation()">
            <input type="checkbox" class="subgroep-cb"
              ${sgAl ? "checked" : ""}
              data-indeterminate="${sgDeels}"
              onchange="toggleSubgroep(this)">
            <span>${_esc(sg.naam)}</span>
          </label>
          <div class="product-producten">${items}</div>
        </div>`;
    }).join("");

    return `
      <div class="product-sectie" id="sectie-${hg.nr}">
        <div class="product-sectie-hdr" onclick="toggleSectie(${hg.nr})">
          <label onclick="event.stopPropagation()">
            <input type="checkbox" class="sectie-cb"
              ${alChecked ? "checked" : ""}
              data-indeterminate="${deelsChecked}"
              onchange="toggleHoofdgroep(this, ${hg.nr})">
          </label>
          <span class="sectie-nr">${hg.nr}</span>
          <span class="sectie-naam">${_esc(hg.naam)}</span>
          <span class="sectie-chevron">›</span>
        </div>
        <div class="product-sectie-body" id="sectie-body-${hg.nr}">${subHTML}</div>
      </div>`;
  }).join("");

  // Indeterminate state werkt alleen via JS
  container.querySelectorAll("[data-indeterminate='true']").forEach(cb => {
    cb.indeterminate = true;
  });
}

function toggleSectie(nr) {
  const body = document.getElementById(`sectie-body-${nr}`);
  const hdr  = document.querySelector(`#sectie-${nr} .product-sectie-hdr`);
  const open = body.classList.toggle("open");
  hdr.classList.toggle("open", open);
}

function toggleSubgroep(cb) {
  const subgroep = cb.closest(".product-subgroep");
  subgroep.querySelectorAll(".product-cb").forEach(c => { c.checked = cb.checked; });
  _updateSectieCheckbox(subgroep.closest(".product-sectie"));
  scheduleAutoSave();
}

function toggleHoofdgroep(cb, nr) {
  const sectie = document.getElementById(`sectie-${nr}`);
  sectie.querySelectorAll(".product-cb,.subgroep-cb").forEach(c => {
    c.checked = cb.checked;
    c.indeterminate = false;
  });
  scheduleAutoSave();
}

function onProductChange(cb) {
  const sg = cb.closest(".product-subgroep");
  if (sg) _updateSubgroepCheckbox(sg);
  _updateSectieCheckbox(cb.closest(".product-sectie"));
  scheduleAutoSave();
}

function _updateSubgroepCheckbox(sgEl) {
  const cb = sgEl.querySelector(".subgroep-cb");
  if (!cb) return;
  const cbs = sgEl.querySelectorAll(".product-cb");
  const n   = Array.from(cbs).filter(c => c.checked).length;
  cb.checked       = n === cbs.length;
  cb.indeterminate = n > 0 && n < cbs.length;
}

function _updateSectieCheckbox(sectieEl) {
  const cb  = sectieEl.querySelector(".sectie-cb");
  const cbs = sectieEl.querySelectorAll(".product-cb");
  const n   = Array.from(cbs).filter(c => c.checked).length;
  cb.checked       = n === cbs.length;
  cb.indeterminate = n > 0 && n < cbs.length;
}

function scheduleAutoSave() {
  const status = document.getElementById("il-save-status");
  if (status) { status.textContent = "Opslaan…"; status.className = "il-save-status saving"; }
  clearTimeout(_autoSaveTimer);
  _autoSaveTimer = setTimeout(saveInzamellijst, 800);
}

async function saveInzamellijst() {
  const g = role === "superadmin" ? document.getElementById("il-gemeente").value : gemeente;
  const status = document.getElementById("il-save-status");
  if (!g) return;

  const checked = Array.from(
    document.querySelectorAll("#inzamellijst-list .product-cb:checked")
  ).map(cb => cb.dataset.product);

  const res = await apiFetch("/api/inzamellijst", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ gemeente: g, producten: checked }),
  });

  if (status) {
    if (res && res.ok) {
      status.textContent = `${checked.length} producten opgeslagen`;
      status.className = "il-save-status saved";
    } else {
      status.textContent = "Opslaan mislukt";
      status.className = "il-save-status error";
    }
  }
}

async function loadInzamellijst() {
  const g = role === "superadmin" ? document.getElementById("il-gemeente").value : gemeente;
  const container = document.getElementById("inzamellijst-list");
  if (!g) {
    container.innerHTML = `<p style="padding:20px 16px;color:var(--muted);">Selecteer een gemeente om de inzamellijst te laden.</p>`;
    return;
  }
  _ilGemeente = g;
  const res = await apiFetch(`/api/inzamellijst?gemeente=${encodeURIComponent(g)}`);
  if (!res) return;
  const items = await res.json();
  const acceptedSet = new Set(items.map(i => i.product));
  renderProductCheckboxes(acceptedSet);
  const status = document.getElementById("il-save-status");
  if (status) {
    status.textContent = acceptedSet.size > 0 ? `${acceptedSet.size} producten actief` : "";
    status.className = "il-save-status";
  }
}

async function heranalyseerGewichten() {
  const gem = document.getElementById("heranalyse-gemeente").value;
  const btn = document.getElementById("heranalyse-btn");
  const status = document.getElementById("heranalyse-status");
  btn.disabled = true;
  btn.textContent = "Bezig…";
  status.style.display = "block";
  status.style.background = "#e8f5e9";
  status.style.color = "#2e7d32";
  status.textContent = `Gewichten heranalyseren voor ${gem}… dit duurt ~5 minuten.`;
  const fd = new FormData();
  fd.append("gemeente", gem);
  const res = await apiFetch("/api/admin/heranalyseer-gewichten", { method: "POST", body: fd });
  btn.disabled = false;
  btn.textContent = "Start heranalyse";
  if (res && res.ok) {
    const j = await res.json();
    status.textContent = `Klaar! ${j.updated} items bijgewerkt, ${j.skipped} overgeslagen.`;
  } else {
    status.style.background = "#ffebee";
    status.style.color = "var(--danger)";
    status.textContent = "Fout bij heranalyse.";
  }
}

// ── Init ───────────────────────────────────────────────────────────────────────
loadGemeenteOptions();
loadUsers();
buildCatCheckboxes("nb-cats");
