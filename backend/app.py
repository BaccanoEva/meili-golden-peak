"""
梅里雪山日照金山预测 API（双模型版 + 静态文件服务）
ECMWF + ICON 交叉验证
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests
from datetime import datetime
from typing import Dict, Any

app = FastAPI(title="梅里雪山日照金山预测", version="2.0")

# CORS：允许所有来源访问 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

LAT = 28.45
LON = 98.82
ELEVATION = 3400


def calculate_probability(h: Dict[str, Any]) -> int:
    score = 100
    c_high = h.get("cloudcover_high") or 50
    c_mid = h.get("cloudcover_mid") or 50
    c_low = h.get("cloudcover_low") or 50
    precip = h.get("precipitation") or 0
    humidity = h.get("relative_humidity_2m") or 60
    wcode = h.get("weather_code") or 0

    if c_high > 85: score -= 55
    elif c_high > 60: score -= 35
    elif c_high > 30: score -= 15
    elif c_high > 10: score -= 5

    if c_mid > 80: score -= 25
    elif c_mid > 50: score -= 12

    if c_low > 80: score -= 25
    elif c_low > 50: score -= 12

    if precip > 2: score -= 40
    elif precip > 0.5: score -= 25
    elif precip > 0: score -= 10

    if humidity > 95: score -= 15
    elif humidity > 85: score -= 8

    if wcode in (95, 96, 99): score -= 30
    elif wcode in (71, 73, 75, 77, 85, 86): score -= 25
    elif wcode in (45, 48): score -= 20

    return max(0, min(100, round(score)))


def get_weather_desc(code: int) -> str:
    mapping = {
        0: "晴朗", 1: "主要晴朗", 2: "部分多云", 3: "阴天",
        45: "雾", 48: "雾凇",
        51: "毛毛雨", 53: "中度毛毛雨", 55: "强毛毛雨",
        56: "冻毛毛雨", 57: "强冻毛毛雨",
        61: "小雨", 63: "中雨", 65: "大雨",
        66: "冻雨", 67: "强冻雨",
        71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
        80: "小阵雨", 81: "中阵雨", 82: "强阵雨",
        85: "小阵雪", 86: "强阵雪",
        95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
    }
    return mapping.get(code, "未知")


def format_weekday(date_str: str, is_today: bool = False) -> str:
    if is_today:
        return "今天"
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()]


def fetch_model(model: str) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "elevation": ELEVATION,
        "models": model,
        "daily": ["sunrise", "sunset"],
        "hourly": [
            "temperature_2m",
            "relative_humidity_2m",
            "cloudcover_low",
            "cloudcover_mid",
            "cloudcover_high",
            "precipitation",
            "weather_code",
        ],
        "timezone": "Asia/Shanghai",
        "forecast_days": 7,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_hourly(data: dict, day_index: int) -> dict:
    daily = data["daily"]
    hourly = data["hourly"]
    sunrise_str = daily["sunrise"][day_index]
    sunset_str = daily["sunset"][day_index]
    sunrise_dt = datetime.fromisoformat(sunrise_str)
    sunrise_hour = sunrise_dt.hour
    sunrise_day = sunrise_dt.day

    h_idx = -1
    for j, t_str in enumerate(hourly["time"]):
        t = datetime.fromisoformat(t_str)
        if t.hour == sunrise_hour and t.day == sunrise_day:
            h_idx = j
            break

    if h_idx == -1:
        for j, t_str in enumerate(hourly["time"]):
            t = datetime.fromisoformat(t_str)
            if t.day == sunrise_day and t.hour == 6:
                h_idx = j
                break

    if h_idx == -1:
        h_idx = 0

    return {
        "date": daily["time"][day_index],
        "sunrise": sunrise_str[11:16],
        "sunset": sunset_str[11:16],
        "details": {
            "cloudcover_low": hourly["cloudcover_low"][h_idx] if hourly.get("cloudcover_low") else None,
            "cloudcover_mid": hourly["cloudcover_mid"][h_idx] if hourly.get("cloudcover_mid") else None,
            "cloudcover_high": hourly["cloudcover_high"][h_idx] if hourly.get("cloudcover_high") else None,
            "precipitation": hourly["precipitation"][h_idx] if hourly.get("precipitation") else None,
            "relative_humidity_2m": hourly["relative_humidity_2m"][h_idx] if hourly.get("relative_humidity_2m") else None,
            "temperature_2m": hourly["temperature_2m"][h_idx] if hourly.get("temperature_2m") else None,
            "weather_code": hourly["weather_code"][h_idx] if hourly.get("weather_code") else None,
        }
    }


@app.get("/api/forecast")
def forecast() -> Dict[str, Any]:
    ec_data = fetch_model("ecmwf_ifs025")
    icon_data = fetch_model("icon_seamless")

    days = len(ec_data["daily"]["time"])
    results = []

    for i in range(days):
        ec_day = extract_hourly(ec_data, i)
        icon_day = extract_hourly(icon_data, i)

        ec_prob = calculate_probability(ec_day["details"])
        icon_prob = calculate_probability(icon_day["details"])

        diff = abs(ec_prob - icon_prob)
        combined = round((ec_prob + icon_prob) / 2)
        if diff > 40:
            combined = round(combined * 0.85)
        elif diff > 25:
            combined = round(combined * 0.93)

        confidence = max(0, 100 - diff)

        tags = []
        if combined >= 80:
            advice = "🌟 极佳！建议提前30分钟就位"
            tags.append({"text": "高概率", "highlight": True})
        elif combined >= 60:
            advice = "✅ 概率不错，值得守候"
            tags.append({"text": "较佳", "highlight": True})
        elif combined >= 40:
            advice = "🤔 有一定机会，需看运气"
            tags.append({"text": "中等", "highlight": False})
        elif combined >= 20:
            advice = "⚠️ 概率较低，不建议专程前往"
            tags.append({"text": "较低", "highlight": False})
        else:
            advice = "❌ 几乎无望，改日再来"
            tags.append({"text": "极低", "highlight": False})

        if confidence >= 80 and combined >= 70:
            tags.append({"text": "🔒 双模型确认", "highlight": True, "consensus": True})
        elif confidence >= 80 and combined < 35:
            tags.append({"text": "双模型一致看差", "highlight": False, "consensus": True})
        elif confidence < 60:
            tags.append({"text": "〰️ 模型分歧", "highlight": False, "divergence": True})

        avg_high = ((ec_day["details"]["cloudcover_high"] or 50) + (icon_day["details"]["cloudcover_high"] or 50)) / 2
        avg_low = ((ec_day["details"]["cloudcover_low"] or 50) + (icon_day["details"]["cloudcover_low"] or 50)) / 2
        avg_precip = ((ec_day["details"]["precipitation"] or 0) + (icon_day["details"]["precipitation"] or 0)) / 2

        if avg_high < 20 and combined > 40:
            tags.append({"text": "天高云淡", "highlight": True})
        if avg_low > 60:
            tags.append({"text": "低云蔽山", "highlight": False})
        if avg_precip > 0:
            tags.append({"text": "有降水", "highlight": False})
        if combined >= 80 and avg_high < 15 and avg_low < 20:
            tags.append({"text": "摄影佳期", "highlight": True})

        results.append({
            "date": ec_day["date"],
            "weekday": format_weekday(ec_day["date"], i == 0),
            "sunrise": ec_day["sunrise"],
            "sunset": ec_day["sunset"],
            "probability": combined,
            "ecProbability": ec_prob,
            "iconProbability": icon_prob,
            "confidence": confidence,
            "diff": diff,
            "advice": advice,
            "tags": tags,
            "ec": {
                "date": ec_day["date"],
                "sunrise": ec_day["sunrise"],
                "details": {
                    **ec_day["details"],
                    "weather_desc": get_weather_desc(ec_day["details"]["weather_code"] or 0),
                }
            },
            "icon": {
                "date": icon_day["date"],
                "sunrise": icon_day["sunrise"],
                "details": {
                    **icon_day["details"],
                    "weather_desc": get_weather_desc(icon_day["details"]["weather_code"] or 0),
                }
            }
        })

    return {"location": "梅里雪山·飞来寺", "forecast": results}


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


# 挂载静态文件（放在最后，作为 fallback）
app.mount("/", StaticFiles(directory="static", html=True), name="static")
