import base64
import json
import os
from typing import Optional

CATEGORIES = ["Hout", "Metaal", "Beton / steen", "Glas", "Kunststof", "Gevaarlijk afval", "Overig"]

GEWICHT_INSTRUCTIE = """
- gewicht_kg: schat het gewicht zo nauwkeurig mogelijk op basis van wat je ziet.
  Gebruik visuele aanwijzingen: afmetingen t.o.v. de omgeving, materiaalsoort en dichtheid.
  Voorbeeldgewichten ter referentie (gebruik dit als kalibratie, niet als default):
  steen/baksteen los ~2 kg, stapel bakstenen ~15–40 kg, deur ~20–35 kg, raam ~8–20 kg,
  zak cement ~25 kg, dakraam ~8–15 kg, kozijn ~10–25 kg, plank hout ~2–8 kg,
  radiator ~8–20 kg, toilet ~25–35 kg, wastafel ~10–20 kg, buizen kort ~1–5 kg.
  Gebruik decimalen voor precisie (bijv. 3.2, 12.5). Nooit een round number tenzij het echt klopt.
  Nooit null, altijd een getal."""

BASE_SYSTEM_PROMPT = f"""Je bent een expert in het herkennen en wegen van bouwmaterialen.
Analyseer de foto en geef:
- label: korte naam van het product/materiaal (max 4 woorden)
- detail: beknopte beschrijving van het materiaal en staat
- gewicht_kg: {GEWICHT_INSTRUCTIE}
- category: kies exact één van: {", ".join(CATEGORIES)}

Reageer uitsluitend in dit JSON-formaat (geen extra tekst):
{{"label": "...", "detail": "...", "gewicht_kg": 0.0, "category": "..."}}"""

SYSTEM_PROMPT_MET_LIJST = f"""Je bent een expert in het herkennen en wegen van bouwmaterialen.
Analyseer de foto en geef:
- label: korte naam van het product/materiaal (max 4 woorden)
- detail: beknopte beschrijving van het materiaal en staat
- gewicht_kg: {GEWICHT_INSTRUCTIE}
- category: kies exact één van: {", ".join(CATEGORIES)}
- geaccepteerd: true als het herkende product overeenkomt met een product op de inzamellijst, anders false

Reageer uitsluitend in dit JSON-formaat (geen extra tekst):
{{"label": "...", "detail": "...", "gewicht_kg": 0.0, "category": "...", "geaccepteerd": true}}"""


def analyse_photo(image_b64: str, inzamellijst: Optional[list] = None) -> tuple[Optional[str], Optional[str], Optional[float], Optional[str], Optional[bool]]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "ANTHROPIC_API_KEY niet ingesteld.", None, None, None

    heeft_lijst = bool(inzamellijst)
    if heeft_lijst:
        producten_tekst = "\n".join(f"- {p}" for p in inzamellijst)
        system = SYSTEM_PROMPT_MET_LIJST + f"\n\nInzamellijst van geaccepteerde producten:\n{producten_tekst}"
        user_text = "Analyseer dit bouwproduct. Geef label, beschrijving, gewichtsschatting, categorie en of het op de inzamellijst staat."
    else:
        system = BASE_SYSTEM_PROMPT
        user_text = "Analyseer dit bouwproduct. Geef label, beschrijving, gewichtsschatting en categorie."

    import anthropic
    import time

    client = anthropic.Anthropic(api_key=api_key)

    for poging in range(3):
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                system=system,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": user_text,
                        },
                    ],
                }],
            )

            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            gewicht = result.get("gewicht_kg")
            try:
                gewicht = float(gewicht) if gewicht is not None else None
            except (ValueError, TypeError):
                gewicht = None
            category = result.get("category")
            if category not in CATEGORIES:
                category = "Overig"
            geaccepteerd = result.get("geaccepteerd") if heeft_lijst else None
            if geaccepteerd is not None:
                geaccepteerd = bool(geaccepteerd)
            return result.get("label"), result.get("detail"), gewicht, category, geaccepteerd

        except anthropic.RateLimitError:
            wacht = 2 ** poging
            print(f"[ai] Rate limit — wacht {wacht}s (poging {poging+1}/3)")
            time.sleep(wacht)
        except anthropic.APIStatusError as e:
            print(f"[ai] API-fout {e.status_code} (poging {poging+1}/3): {e}")
            if e.status_code < 500:
                break  # Client-fout, niet opnieuw proberen
            time.sleep(2 ** poging)
        except Exception as e:
            print(f"[ai] Onverwachte fout (poging {poging+1}/3): {e}")
            if poging < 2:
                time.sleep(2)

    return None, "AI-analyse tijdelijk niet beschikbaar. Probeer het opnieuw.", None, None, None


def heranalyseer_gewicht(photo_url: str, label: str) -> Optional[float]:
    """Heranalyseer alleen het gewicht van een item op basis van foto-URL."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    import anthropic, time, urllib.request
    client = anthropic.Anthropic(api_key=api_key)
    try:
        with urllib.request.urlopen(photo_url, timeout=10) as r:
            img_data = base64.b64encode(r.read()).decode()
        ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            ct = "image/jpeg"
    except Exception as e:
        print(f"[ai] Foto ophalen mislukt voor {label}: {e}")
        return None

    for poging in range(2):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=64,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": ct, "data": img_data}},
                    {"type": "text", "text": f"Dit is een foto van: {label}.\n"
                     "Geef alleen het geschatte gewicht in kg als decimaal getal (bijv. 4.2). "
                     "Gebruik visuele aanwijzingen: grootte, materiaal, context. "
                     "Geef ALLEEN het getal, niks anders."},
                ]}],
            )
            val = msg.content[0].text.strip().replace(",", ".")
            return round(float(val), 1)
        except Exception as e:
            print(f"[ai] Gewicht heranalyse poging {poging+1} mislukt: {e}")
            if poging < 1:
                time.sleep(2)
    return None
