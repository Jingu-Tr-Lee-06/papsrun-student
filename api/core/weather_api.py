from datetime import datetime, timedelta
import math
import os
from typing import Any, Dict
from dotenv import load_dotenv
import requests

load_dotenv(".env.local")
load_dotenv()


def get_realtime_weather(station_id: str = "239") -> Dict[str, Any]:
    default_temp = float(os.getenv("DEFAULT_TEMP_C", "24.5"))
    default_solar = float(os.getenv("DEFAULT_SOLAR_W_M2", "750.0"))

    hour = datetime.now().hour
    if 6 <= hour <= 18:
        est_solar = round(max(120.0, math.sin((hour - 6) / 12.0 * math.pi) * 850.0), 1)
    else:
        est_solar = 0.0

    fallback_payload: Dict[str, Any] = {
        "temp_c": default_temp,
        "solar_wm2": est_solar if est_solar > 0 else default_solar,
        "humidity_pct": 55.0,
        "status": "FALLBACK_STANDARD",
    }

    kma_key = (os.getenv("KMA_API_KEY") or "").strip()
    stn = (os.getenv("KMA_STATION_ID") or station_id).strip()

    if not kma_key:
        return fallback_payload

    try:
        target_dt = datetime.now() - timedelta(minutes=15)
        tm_str = target_dt.strftime("%Y%m%d%H00")

        url = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"
        params = {
            "tm": tm_str,
            "stn": stn,
            "help": "0",
            "authKey": kma_key,
        }

        response = requests.get(url, params=params, timeout=4.0)
        if response.status_code == 200:
            temp_c = default_temp
            humidity_pct = 55.0
            solar_wm2 = fallback_payload["solar_wm2"]

            for raw_line in response.text.strip().split("\n"):
                line = raw_line.strip()
                if not line or line.startswith("#") or "7777END" in line:
                    continue

                parts = line.split()
                if len(parts) >= 12 and parts[11] not in ("-99.0", "-99", "-99.9"):
                    temp_c = float(parts[11])

                if len(parts) >= 14 and parts[13] not in ("-99.0", "-99", "-99.9"):
                    humidity_pct = float(parts[13])

                if len(parts) >= 16 and parts[15] not in ("-99.0", "-99", "-99.9"):
                    raw_si_mj = float(parts[15])
                    if raw_si_mj > 0:
                        solar_wm2 = round(raw_si_mj * 277.78, 1)

                return {
                    "temp_c": round(temp_c, 1),
                    "solar_wm2": round(solar_wm2, 1),
                    "humidity_pct": round(humidity_pct, 1),
                    "status": "LIVE_KMA_ASOS",
                    "station_id": stn,
                    "obs_time": tm_str,
                }
    except Exception:
        pass

    return fallback_payload