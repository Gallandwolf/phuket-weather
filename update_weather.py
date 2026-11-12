
import os
import json
import re
from datetime import datetime, timezone
import requests

API_KEY = os.environ.get("GEMINI_API_KEY")

LOCATIONS = {
    "phuket": {"latitude": 7.82, "longitude": 98.30},
    "phi_phi": {"latitude": 7.74, "longitude": 98.77},
}

HAZARDS = ["thunder", "rain", "flood", "landslide", "sea"]
LEVELS = ["normal", "low", "moderate", "high", "critical"]


def get_weather(coords):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        **coords,
        "current": (
            "temperature_2m,precipitation,wind_speed_10m,"
            "weather_code"
        ),
        "hourly": (
            "precipitation,precipitation_probability,"
            "wind_gusts_10m,weather_code"
        ),
        "forecast_days": 2,
        "timezone": "Asia/Bangkok",
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def analyze_with_gemini(weather):
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY puuttuu")

    prompt = """
Olet Thaimaan sää- ja turvallisuusriskien analysoija.
Arvioi Phuketin ja Phi Phin säätietojen perusteella:
thunder, rain, flood, landslide ja sea.

Käytä vain annettuja tietoja. Älä keksi virallisia
varoituksia. Sääennuste ei yksin todista tulvaa,
maanvyörymää tai vaarallisia meriolosuhteita.

Riskitasot: normal, low, moderate, high, critical.
Käytä null, jos tietoja ei ole riittävästi.

Palauta ainoastaan JSON:
{
  "severity": null,
  "summary": "Suomenkielinen yhteenveto",
  "hazards": {
    "thunder": null,
    "rain": null,
    "flood": null,
    "landslide": null,
    "sea": null
  }
}

severity on kokonaisriskitaso tai null.
Älä väitä virallisten varoitusten puuttuvan,
koska niitä ei ole tarkistettu erikseen.

Säätiedot:
""" + json.dumps(weather, ensure_ascii=False)

  
    url = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/gemini-2.5-flash-lite:generateContent"
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
        timeout=90,
    )
    response.raise_for_status()
    result = response.json()
    answer = result["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(answer)


def main():
    weather = {
        name: get_weather(coords)
        for name, coords in LOCATIONS.items()
    }

    analysis = analyze_with_gemini(weather)

    hazards = analysis.get("hazards") or {}
    hazards = {
        key: (
            hazards.get(key)
            if hazards.get(key) in LEVELS
            else None
        )
        for key in HAZARDS
    }

    severity = analysis.get("severity")
    if severity not in LEVELS:
        severity = None

    phuket = weather["phuket"]["current"]
    phi_phi = weather["phi_phi"]["current"]

    output = {
        "location": "Phuket ja Phi Phi",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hazard_type": None,
        "severity": severity,
        "expected_timing": None,
        "precautions": None,
        "source_links": [],
        "fingerprint": "phuket-weather-hourly",
        "phuket": {
            "temperature": phuket["temperature_2m"],
            "rain": phuket["precipitation"],
            "wind": phuket["wind_speed_10m"],
            "severity": severity,
        },
        "phi_phi": {
            "temperature": phi_phi["temperature_2m"],
            "rain": phi_phi["precipitation"],
            "wind": phi_phi["wind_speed_10m"],
        },
        "summary": analysis.get("summary", ""),
        "hazards": hazards,
    }

    with open("weather.json", "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print("Säätiedot päivitetty onnistuneesti.")


if __name__ == "__main__":
    main()
