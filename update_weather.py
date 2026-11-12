
import os
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests

API_KEY = os.environ.get("GEMINI_API_KEY")
TIMEOUT = 30

LOCATIONS = {
    "phuket": {"latitude": 7.82, "longitude": 98.30},
    "phi_phi": {"latitude": 7.74, "longitude": 98.77},
}

LEVELS = ["normal", "low", "moderate", "high", "critical"]
HAZARDS = ["thunder", "rain", "flood", "landslide", "sea"]


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignore += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignore = max(0, self.ignore - 1)

    def handle_data(self, data):
        if not self.ignore:
            self.parts.append(data)


def fetch_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT,
        headers={"User-Agent": "PhuketWeatherDemo/1.0"},
    )
    response.raise_for_status()
    return response.json()


def fetch_weather(coords):
    return fetch_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            **coords,
            "current": (
                "temperature_2m,precipitation,"
                "wind_speed_10m,weather_code"
            ),
            "hourly": (
                "precipitation,precipitation_probability,"
                "wind_gusts_10m,weather_code"
            ),
            "forecast_days": 2,
            "timezone": "Asia/Bangkok",
        },
    )


def fetch_marine(coords):
    return fetch_json(
        "https://marine-api.open-meteo.com/v1/marine",
        {
            **coords,
            "hourly": (
                "wave_height,wave_period,"
                "swell_wave_height"
            ),
            "forecast_days": 2,
            "timezone": "Asia/Bangkok",
        },
    )


def fetch_tmd():
    url = "https://phuketmet.tmd.go.th/th"
    response = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()

    parser = TextExtractor()
    parser.feed(response.text)

    text = re.sub(r"\s+", " ", " ".join(parser.parts))
    return text[:5000]


def get_sources_and_data():
    data = {}
    sources = []
    now = datetime.now(timezone.utc).isoformat()

    for name, coords in LOCATIONS.items():
        for kind, fetcher, url in [
            (
                "weather",
                fetch_weather,
                "https://open-meteo.com/en/docs",
            ),
            (
                "marine",
                fetch_marine,
                "https://open-meteo.com/en/docs/marine-weather-api",
            ),
        ]:
            key = f"{name}_{kind}"
            try:
                data[key] = fetcher(coords)
                sources.append({
                    "name": f"Open-Meteo {kind} – {name}",
                    "url": url,
                    "status": "success",
                    "retrieved_at": now,
                    "used_in_analysis": True,
                })
            except Exception as exc:
                print(f"Source unavailable: {key}: {exc}")
                sources.append({
                    "name": f"Open-Meteo {kind} – {name}",
                    "url": url,
                    "status": "unavailable",
                    "retrieved_at": now,
                    "used_in_analysis": False,
                })

    try:
        data["tmd_context"] = fetch_tmd()
        sources.append({
            "name": "TMD Phuket – verkkosivun teksti",
            "url": "https://phuketmet.tmd.go.th/th",
            "status": "success",
            "retrieved_at": now,
            "used_in_analysis": True,
            "note": "Julkaisuaikaa ei varmistettu",
        })
    except Exception as exc:
        print(f"TMD unavailable: {exc}")
        sources.append({
            "name": "TMD Phuket",
            "url": "https://phuketmet.tmd.go.th/th",
            "status": "unavailable",
            "retrieved_at": now,
            "used_in_analysis": False,
        })

    if "phuket_weather" not in data:
        raise RuntimeError("Phuketin säätietoja ei saatu")

    return data, sources


def analyze(data):
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY puuttuu")

    prompt = """
Olet Phuketin ja Phi Phin henkilökohtainen
sää- ja turvallisuusriskien analysoija.

Arvioi viisi vaaratekijää:
thunder, rain, flood, landslide, sea.

Käytä vain annettuja lähdetietoja.

Tärkeät säännöt:
- Älä keksi havaintoja, varoituksia tai uutisia.
- Open-Meteon sääennuste ei ole virallinen varoitus.
- TMD:n verkkosivutekstin ajantasaisuutta ei ole
  varmistettu. Älä käytä sitä nykyisen varoituksen
  todisteena ilman selvää päivämäärää.
- Jos riskille ei ole riittävästi tietoa, käytä null.
- Arvioi ukkosriskiä myös weather_code-arvoista.
  Koodi 95, 96 tai 99 tarkoittaa ukkosennustetta.
- Arvioi sadetta myös ennustetuista sademääristä.
- Arvioi meririskiä aallonkorkeudesta ja tuulesta.
- Älä päättele tulvaa tai maanvyörymää pelkästä
  yksittäisestä sadehavainnosta.
- Kerro suomeksi selkeästi analyysin perusteet.

Riskitasot:
normal, low, moderate, high, critical tai null.

Palauta vain JSON-objekti tässä muodossa:

{
  "severity": null,
  "summary": "Yhteenveto suomeksi",
  "hazards": {
    "thunder": null,
    "rain": null,
    "flood": null,
    "landslide": null,
    "sea": null
  },
  "hazard_details": {
    "thunder": "Perustelu",
    "rain": "Perustelu",
    "flood": "Perustelu",
    "landslide": "Perustelu",
    "sea": "Perustelu"
  }
}

Säätiedot:
""" + json.dumps(data, ensure_ascii=False)

    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/gemini-3.5-flash-lite:generateContent"
    )

    response = requests.post(
        url,
        headers={"x-goog-api-key": API_KEY},
        json={
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        },
        timeout=120,
    )

    if not response.ok:
        print("Gemini API:", response.status_code, response.text[:1500])

    response.raise_for_status()

    result = response.json()
    answer = result["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(answer)


def main():
    data, sources = get_sources_and_data()
    analysis = analyze(data)

    raw_hazards = analysis.get("hazards") or {}
    hazards = {
        key: raw_hazards.get(key)
        if raw_hazards.get(key) in LEVELS
        else None
        for key in HAZARDS
    }

    severity = analysis.get("severity")
    if severity not in LEVELS:
        severity = None

    phuket = data["phuket_weather"]["current"]
    phi_data = data.get("phi_phi_weather", {})
    phi_phi = phi_data.get("current", {})

    output = {
        "location": "Phuket ja Phi Phi",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hazard_type": None,
        "severity": severity,
        "expected_timing": None,
        "precautions": None,
        "source_links": [
            source["url"]
            for source in sources
            if source["used_in_analysis"]
        ],
        "fingerprint": "phuket-weather-hourly",
        "phuket": {
            "temperature": phuket.get("temperature_2m"),
            "rain": phuket.get("precipitation"),
            "wind": phuket.get("wind_speed_10m"),
            "severity": severity,
        },
        "phi_phi": {
            "temperature": phi_phi.get("temperature_2m"),
            "rain": phi_phi.get("precipitation"),
            "wind": phi_phi.get("wind_speed_10m"),
        },
        "summary": analysis.get("summary", ""),
        "hazards": hazards,
        "hazard_details": analysis.get("hazard_details") or {},
        "sources": sources,
    }

    with open("weather.json", "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print("Weather data updated successfully.")
    print("Sources collected:", len(sources))


if __name__ == "__main__":
    main()
