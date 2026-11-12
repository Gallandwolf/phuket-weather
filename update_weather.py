import os
import json
import re
import time
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
DMR_URL = "https://landslidemonitoring.dmr.go.th/Home/Index"


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
            "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
            "hourly": "precipitation,precipitation_probability,wind_gusts_10m,weather_code",
            "forecast_days": 2,
            "timezone": "Asia/Bangkok",
        },
    )


def fetch_marine(coords):
    return fetch_json(
        "https://marine-api.open-meteo.com/v1/marine",
        {
            **coords,
            "hourly": "wave_height,wave_period,swell_wave_height",
            "forecast_days": 2,
            "timezone": "Asia/Bangkok",
        },
    )


def fetch_page_text(url):
    response = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    parser = TextExtractor()
    parser.feed(response.text)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:5000]


def get_sources_and_data():
    data = {}
    sources = []

    def add_source(name, url, success, note=""):
        source = {
            "name": name,
            "url": url,
            "status": "success" if success else "unavailable",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "used_in_analysis": success,
        }
        if note:
            source["note"] = note
        sources.append(source)

    for name, coords in LOCATIONS.items():
        for kind, fetcher, url in [
            ("weather", fetch_weather, "https://open-meteo.com/en/docs"),
            ("marine", fetch_marine, "https://open-meteo.com/en/docs/marine-weather-api"),
        ]:
            key = f"{name}_{kind}"
            try:
                data[key] = fetcher(coords)
                add_source(f"Open-Meteo {kind} – {name}", url, True)
            except Exception as exc:
                print(f"Source unavailable: {key}: {exc}")
                add_source(f"Open-Meteo {kind} – {name}", url, False)

    tmd_url = "https://phuketmet.tmd.go.th/th"
    try:
        text = fetch_page_text(tmd_url)
        if not text:
            raise ValueError("Sivulta ei saatu tekstiä")
        data["tmd_context"] = text
        add_source("TMD Phuket – verkkosivun teksti", tmd_url, True,
                   "Julkaisuaikaa ja varoitusten ajantasaisuutta ei varmistettu")
    except Exception as exc:
        print(f"TMD unavailable: {exc}")
        add_source("TMD Phuket", tmd_url, False)

    try:
        text = fetch_page_text(DMR_URL)
        if len(text) < 100:
            raise ValueError("DMR-sivulta ei saatu riittävästi tekstiä")
        data["dmr_landslide_context"] = text
        add_source("DMR – maanvyörymäseurannan verkkosivun teksti", DMR_URL, True,
                   "Sivun teksti haettu; ajantasaisia mittauksia tai varoituksia ei ole vahvistettu")
    except Exception as exc:
        print(f"DMR unavailable: {exc}")
        add_source("DMR – maanvyörymäseuranta", DMR_URL, False)

    if "phuket_weather" not in data:
        raise RuntimeError("Phuketin säätietoja ei saatu")

    return data, sources


def analyze(data):
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY puuttuu")

    prompt = """
Olet Phuketin ja Phi Phin henkilökohtainen sää- ja turvallisuusriskien analysoija.
Arvioi viisi vaaratekijää: thunder, rain, flood, landslide, sea.
Käytä vain annettuja lähdetietoja. Älä keksi havaintoja, varoituksia tai uutisia.

Tärkeät säännöt:
- Open-Meteon sääennuste ei ole virallinen varoitus.
- TMD:n ja DMR:n verkkosivutekstin julkaisuajankohtaa tai ajantasaisuutta
  ei ole vahvistettu. Älä pidä sivun yleistä tekstiä voimassa olevana varoituksena.
- DMR:n sivu voi ladata varsinaiset mittaukset ja varoitukset JavaScriptillä.
  Pelkkä haettu HTML-teksti ei todista, että ajantasaiset varoitukset on luettu.
- DMR:n sivun onnistunut lataus ei todista maanvyörymäriskin olevan normaali.
- Jos maanvyörymän tai tulvan riskitasolle ei ole riittävästi ajantasaista,
  paikallista näyttöä, käytä kyseisen riskin arvona null, ei normal.
- Jos DMR-tekstissä näkyy varoitus, arvioi sen merkitys vain, jos
  ajankohta ja alue (Phuket / Phi Phi) käyvät yksiselitteisesti ilmi.
- Älä päättele maanvyörymäriskiä normaaliksi vain siksi, ettei
  haetussa tekstissä näy varoitusta.
- Arvioi ukkosriskiä myös weather_code-arvoista: 95, 96, 99 tarkoittavat ukkosta.
- Arvioi sadetta ennustetuista sademääristä.
- Arvioi meririskiä aallonkorkeudesta ja tuulesta.
- Älä päättele tulvaa tai maanvyörymää pelkästä yksittäisestä sadehavainnosta.
- Kerro suomeksi selkeästi analyysin perusteet ja puuttuvan tiedon rajoitukset.

Riskitasot: normal, low, moderate, high, critical tai null.
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
    "landslide": "Perustelu ja DMR-tietojen rajoitukset",
    "sea": "Perustelu"
  }
}

Lähteistä kerätyt tiedot:
""" + json.dumps(data, ensure_ascii=False)

    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/gemini-3.5-flash-lite:generateContent"
    )

    for attempt in range(4):
        try:
            response = requests.post(
                url,
                headers={"x-goog-api-key": API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.1,
                    },
                },
                timeout=120,
            )
        except requests.exceptions.Timeout:
            if attempt == 3:
                raise
            time.sleep(15 * (2 ** attempt))
            continue

        if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            wait = 15 * (2 ** attempt)
            print(f"Gemini {response.status_code}, odotetaan {wait}s")
            time.sleep(wait)
            continue
        break

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
        key: raw_hazards.get(key) if raw_hazards.get(key) in LEVELS else None
        for key in HAZARDS
    }
    # HTML-teksti ei yksin riitä osoittamaan maanvyörymä- tai tulvariskiä normaaliksi.
    for key in ("landslide", "flood"):
        if hazards[key] == "normal":
            hazards[key] = None
            details = analysis.setdefault("hazard_details", {})
            details[key] = (
                "Ajantasaista paikallista viranomaistietoa ei ole vahvistettu. "
                "Riskitaso on tuntematon; tämä ei tarkoita turvallista tilannetta."
            )

    severity = analysis.get("severity")
    if severity not in LEVELS:
        severity = None

    phuket = data["phuket_weather"]["current"]
    phi_phi = data.get("phi_phi_weather", {}).get("current", {})

    output = {
        "location": "Phuket ja Phi Phi",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hazard_type": None,
        "severity": severity,
        "expected_timing": None,
        "precautions": None,
        "source_links": [s["url"] for s in sources if s["used_in_analysis"]],
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
