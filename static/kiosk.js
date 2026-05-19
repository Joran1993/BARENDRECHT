const GEMEENTE = "Almere";
const AUTO_TERUG = 5000; // ms voor automatisch terug naar camera

let autoTerugTimer = null;

// ── Camera starten ──
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } }
    });
    document.getElementById("video").srcObject = stream;
  } catch (e) {
    alert("Camera niet beschikbaar: " + e.message);
  }
}

function toonScherm(id) {
  document.querySelectorAll(".scherm").forEach(s => s.classList.remove("actief"));
  document.getElementById(id).classList.add("actief");
}

// ── Foto maken ──
function maakFoto() {
  const video = document.getElementById("video");
  const canvas = document.getElementById("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);

  canvas.toBlob(async (blob) => {
    toonScherm("scherm-laden");
    await scanFoto(blob);
  }, "image/jpeg", 0.85);
}

// ── Scan naar API ──
async function scanFoto(blob) {
  const form = new FormData();
  form.append("file", blob, "foto.jpg");
  form.append("gemeente", GEMEENTE);

  try {
    const res = await fetch("/api/kiosk/scan", { method: "POST", body: form });
    const data = await res.json();
    toonResultaat(data);
  } catch (e) {
    toonResultaat({ geaccepteerd: false, label: "Fout", detail: "Scan mislukt, probeer opnieuw." });
  }
}

// ── Resultaat tonen ──
function toonResultaat(data) {
  const scherm = document.getElementById("scherm-resultaat");
  scherm.classList.remove("groen", "rood");

  if (data.geaccepteerd) {
    scherm.classList.add("groen");
    document.getElementById("resultaat-icoon").textContent = "✓";
    document.getElementById("resultaat-label").textContent = data.label || "Geaccepteerd";
    document.getElementById("resultaat-tekst").textContent = "Dit product wordt ingenomen door de milieustraat.";
  } else {
    scherm.classList.add("rood");
    document.getElementById("resultaat-icoon").textContent = "✕";
    document.getElementById("resultaat-label").textContent = data.label || "Niet ingenomen";
    document.getElementById("resultaat-tekst").textContent = data.detail || "Dit product staat niet op de inzamellijst.";
  }

  // Herstart balk-animatie
  const balk = document.getElementById("resultaat-balk");
  balk.replaceWith(balk.cloneNode(true));

  toonScherm("scherm-resultaat");

  // Automatisch terug na 5 seconden
  clearTimeout(autoTerugTimer);
  autoTerugTimer = setTimeout(opnieuw, AUTO_TERUG);
}

function opnieuw() {
  clearTimeout(autoTerugTimer);
  toonScherm("scherm-camera");
}

startCamera();
