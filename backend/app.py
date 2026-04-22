"""
梅里雪山日照金山预测 API（双模型版 + 静态文件服务）
ECMWF + ICON 交叉验证
"""
import json
import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import requests
from datetime import datetime, timedelta
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

# ==================== 数据持久化 ====================

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
ACTUAL_FILE = os.path.join(DATA_DIR, "actual_results.json")
HISTORY_FILE = os.path.join(DATA_DIR, "forecast_history.json")
SOURCE_FILE = os.path.join(os.path.dirname(__file__), "..", "实际出现日照金山结果.txt")
WEIGHTS_FILE = os.path.join(DATA_DIR, "model_weights.json")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def parse_source_file():
    """解析实际出现日照金山结果.txt，初始化真实结果"""
    results = {}
    if not os.path.exists(SOURCE_FILE):
        return results
    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "：" in line or "天" in line or "次" in line:
                continue
            m = re.match(r"^(\d+)\.(\d+)$", line)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                date_str = f"2026-{month:02d}-{day:02d}"
                results[date_str] = True
    return results


def init_actual_results():
    ensure_data_dir()
    txt_data = parse_source_file()
    existing = {}
    if os.path.exists(ACTUAL_FILE):
        with open(ACTUAL_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    changed = False
    # 1. 文件中明确的日期设为 true
    for date, val in txt_data.items():
        if date not in existing:
            existing[date] = val
            changed = True
    # 2. 文件日期范围内未列出的日期设为 false（确认未出现）
    if txt_data:
        all_dates = sorted(txt_data.keys())
        start = datetime.strptime(all_dates[0], "%Y-%m-%d")
        end = datetime.strptime(all_dates[-1], "%Y-%m-%d")
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            if date_str not in existing:
                existing[date_str] = False
                changed = True
            current += timedelta(days=1)
    if changed or not os.path.exists(ACTUAL_FILE):
        with open(ACTUAL_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    return existing


def load_actual_results():
    if os.path.exists(ACTUAL_FILE):
        with open(ACTUAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_weights():
    """加载可学习模型权重，不存在则初始化默认值"""
    default = {
        "version": 1,
        "month_base": {"1": 50, "2": 30, "3": 35, "4": 42, "5": 40, "6": 35,
                       "7": 30, "8": 35, "9": 40, "10": 45, "11": 50, "12": 55},
        "low_cloud": {"t0": 10, "b0": 12, "t1": 30, "b1": 5, "t2": 50,
                      "p2": 0, "t3": 70, "p3": 8, "t4": 90, "p4": 18, "p5": 28},
        "precip": {"bonus": 3, "t0": 0, "p0": 5, "t1": 0.5, "p1": 12, "t2": 2, "p2": 25},
        "high_cloud": {"t0": 10, "b0": 5, "t1": 30, "b1": 2, "t2": 80, "p2": 15},
        "mid_cloud": {"t0": 20, "b0": 3, "t1": 70, "p1": 10},
        "humid": {"bonus_t": 40, "bonus_v": 3, "penalty_t": 90, "penalty_v": 8},
        "wcode": {"clear_bonus": 3, "storm_penalty": 20, "snow_penalty": 15, "fog_penalty": 12}
    }
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                w = json.load(f)
                # 补全新增字段
                for k, v in default.items():
                    if k not in w:
                        w[k] = v
                return w
        except Exception:
            pass
    ensure_data_dir()
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(default, f, ensure_ascii=False, indent=2)
    return default


def save_weights(weights: dict):
    ensure_data_dir()
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)


def save_forecast_history(forecast: list):
    ensure_data_dir()
    history = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    for day in forecast:
        date = day["date"]
        history[date] = {
            "date": date,
            "probability": day["probability"],
            "ecProbability": day["ecProbability"],
            "iconProbability": day["iconProbability"],
            "confidence": day["confidence"],
            "diff": day["diff"],
            "advice": day["advice"],
            "tags": day["tags"],
            "ec": day["ec"],
            "icon": day["icon"],
            "saved_at": datetime.now().isoformat(),
        }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_forecast_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def fetch_historical(start_date: str, end_date: str) -> dict:
    """调用 Open-Meteo Archive API（ERA5 再分析数据）获取历史实况"""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "elevation": ELEVATION,
        "start_date": start_date,
        "end_date": end_date,
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
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def backfill_historical_forecast():
    """回填 ERA5 历史参考概率：
    1. 对所有已有真实结果的日期回填；
    2. 对过去30天内所有日期回填，确保历史页面每天都有概率可展示。
    """
    actual = load_actual_results()
    history = load_forecast_history()

    today = datetime.now().date()
    missing_dates = set()

    # 1. 有真实结果但无预测记录的日期
    for d in actual:
        if d not in history:
            missing_dates.add(d)

    # 2. 过去30天内所有日期（确保历史页面每天都有概率）
    for i in range(30):
        date = (today - timedelta(days=i)).isoformat()
        if date not in history:
            missing_dates.add(date)

    if not missing_dates:
        return

    missing_dates = sorted(missing_dates)
    start_date = missing_dates[0]
    end_date = missing_dates[-1]

    try:
        data = fetch_historical(start_date, end_date)
    except Exception:
        return

    days = len(data["daily"]["time"])
    for i in range(days):
        day_data = extract_hourly(data, i)
        date = day_data["date"]
        if date not in missing_dates:
            continue
        prob = calculate_probability(day_data["details"], int(date[5:7]))
        history[date] = {
            "date": date,
            "probability": prob,
            "ecProbability": prob,
            "iconProbability": None,
            "confidence": None,
            "diff": None,
            "advice": "基于 ERA5 历史实况反推的参考概率",
            "tags": [{"text": "ERA5参考", "highlight": False}],
            "ec": {
                "date": date,
                "sunrise": day_data["sunrise"],
                "details": {
                    **day_data["details"],
                    "weather_desc": get_weather_desc(day_data["details"]["weather_code"] or 0),
                }
            },
            "icon": None,
            "source": "historical_era5",
            "saved_at": datetime.now().isoformat(),
        }

    ensure_data_dir()
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def calculate_probability(h: Dict[str, Any], month: int = None, weights: dict = None) -> int:
    """基于可学习权重的概率计算模型（月份基础概率 + 气象条件双向调整）
    weights 为外部传入的权重字典，不传则自动加载持久化权重
    """
    w = weights if weights is not None else load_weights()
    score = w["month_base"].get(str(month), 40)

    c_high = h.get("cloudcover_high") or 0
    c_mid = h.get("cloudcover_mid") or 0
    c_low = h.get("cloudcover_low") or 0
    precip = h.get("precipitation") or 0
    humidity = h.get("relative_humidity_2m") or 0
    wcode = h.get("weather_code") or 0

    # 低云（双向调整）
    lc = w["low_cloud"]
    if c_low <= lc["t0"]: score += lc["b0"]
    elif c_low <= lc["t1"]: score += lc["b1"]
    elif c_low <= lc["t2"]: score += lc["p2"]
    elif c_low <= lc["t3"]: score -= lc["p3"]
    elif c_low <= lc["t4"]: score -= lc["p4"]
    else: score -= lc["p5"]

    # 高云
    hc = w["high_cloud"]
    if c_high <= hc["t0"]: score += hc["b0"]
    elif c_high <= hc["t1"]: score += hc["b1"]
    elif c_high > hc["t2"]: score -= hc["p2"]

    # 中云
    mc = w["mid_cloud"]
    if c_mid <= mc["t0"]: score += mc["b0"]
    elif c_mid > mc["t1"]: score -= mc["p1"]

    # 降水
    pc = w["precip"]
    if precip == pc["t0"]: score += pc["bonus"]
    elif precip > pc["t2"]: score -= pc["p2"]
    elif precip > pc["t1"]: score -= pc["p1"]
    elif precip > pc["t0"]: score -= pc["p0"]

    # 湿度
    hu = w["humid"]
    if humidity < hu["bonus_t"]: score += hu["bonus_v"]
    elif humidity > hu["penalty_t"]: score -= hu["penalty_v"]

    # 天气代码
    wc = w["wcode"]
    if wcode in (0, 1): score += wc["clear_bonus"]
    elif wcode in (95, 96, 99): score -= wc["storm_penalty"]
    elif wcode in (71, 73, 75, 77, 85, 86): score -= wc["snow_penalty"]
    elif wcode in (45, 48): score -= wc["fog_penalty"]

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


# 启动时初始化真实结果并回填历史参考概率
init_actual_results()
backfill_historical_forecast()


@app.get("/api/forecast")
def forecast() -> Dict[str, Any]:
    ec_data = fetch_model("ecmwf_ifs")
    icon_data = fetch_model("icon_seamless")

    days = len(ec_data["daily"]["time"])
    results = []

    for i in range(days):
        ec_day = extract_hourly(ec_data, i)
        icon_day = extract_hourly(icon_data, i)

        month = int(ec_day["date"][5:7])
        ec_prob = calculate_probability(ec_day["details"], month)
        icon_prob = calculate_probability(icon_day["details"], month)

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

    # 持久化预测历史（失败不影响主流程）
    try:
        save_forecast_history(results)
    except Exception:
        pass

    return {"location": "梅里雪山·飞来寺", "forecast": results}


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/actual-results")
def get_actual_results():
    return load_actual_results()


@app.post("/api/actual-results")
def post_actual_result(payload: dict):
    date = payload.get("date")
    actual = payload.get("actual")
    if not date or not isinstance(actual, bool):
        raise HTTPException(status_code=400, detail="需要 date 和 actual (bool) 字段")
    data = load_actual_results()
    data[date] = actual
    ensure_data_dir()
    with open(ACTUAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 触发自动参数优化（不阻塞响应）
    optimize_info = None
    try:
        optimize_info = optimize_weights()
    except Exception:
        pass

    return {"success": True, "date": date, "actual": actual, "optimized": optimize_info}


@app.get("/api/forecast-history")
def get_forecast_history():
    return load_forecast_history()


# ============ 自动参数优化 ============

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _evaluate_weights(rows, weights):
    """用给定权重评估在训练集上的准确率"""
    correct = 0
    for r in rows:
        prob = calculate_probability(r, r["month"], weights)
        pred = prob >= 50
        if pred == r["actual"]:
            correct += 1
    return correct


def optimize_weights():
    """根据真实反馈自动优化模型权重（随机搜索）
    返回 {"accuracy": 训练集准确率, "samples": 样本数}
    """
    import random

    actual = load_actual_results()
    history = load_forecast_history()

    # 构建训练数据
    rows = []
    for date, a in actual.items():
        h = history.get(date)
        if not h or not h.get("ec"):
            continue
        d = h["ec"]["details"]
        rows.append({
            "actual": a,
            "month": int(date[5:7]),
            "cloudcover_high": d.get("cloudcover_high") or 0,
            "cloudcover_mid": d.get("cloudcover_mid") or 0,
            "cloudcover_low": d.get("cloudcover_low") or 0,
            "precipitation": d.get("precipitation") or 0,
            "relative_humidity_2m": d.get("relative_humidity_2m") or 0,
            "weather_code": d.get("weather_code") or 0,
        })

    if len(rows) < 10:
        return None  # 数据不足，不优化

    current = load_weights()
    best_acc = _evaluate_weights(rows, current)
    best_weights = current

    random.seed(42)

    # 两阶段随机搜索：
    # 阶段1（800轮）全局搜索：每轮随机选3组参数扰动，聚焦核心参数
    # 阶段2（400轮）局部搜索：以当前最优为起点继续微调
    search_base = json.loads(json.dumps(current))

    for phase in [800, 400]:
        for _ in range(phase):
            candidate = json.loads(json.dumps(search_base))

            # 随机决定本轮扰动哪些参数组（减少维度爆炸）
            groups = random.sample(
                ["month", "low_cloud", "precip", "high_cloud", "mid_cloud", "humid", "wcode"],
                k=random.randint(2, 4),
            )

            if "month" in groups:
                for m in candidate["month_base"]:
                    candidate["month_base"][m] = _clamp(
                        candidate["month_base"][m] + random.randint(-8, 8), 15, 80
                    )

            if "low_cloud" in groups:
                lc = candidate["low_cloud"]
                lc["b0"] = _clamp(lc["b0"] + random.randint(-5, 5), 0, 25)
                lc["b1"] = _clamp(lc["b1"] + random.randint(-3, 3), 0, 15)
                lc["p3"] = _clamp(lc["p3"] + random.randint(-5, 5), 0, 25)
                lc["p4"] = _clamp(lc["p4"] + random.randint(-5, 5), 0, 35)
                lc["p5"] = _clamp(lc["p5"] + random.randint(-5, 5), 0, 45)

            if "precip" in groups:
                pc = candidate["precip"]
                pc["bonus"] = _clamp(pc["bonus"] + random.randint(-2, 2), 0, 10)
                pc["p0"] = _clamp(pc["p0"] + random.randint(-3, 3), 0, 15)
                pc["p1"] = _clamp(pc["p1"] + random.randint(-5, 5), 5, 30)
                pc["p2"] = _clamp(pc["p2"] + random.randint(-5, 5), 10, 45)

            if "high_cloud" in groups:
                hc = candidate["high_cloud"]
                hc["b0"] = _clamp(hc["b0"] + random.randint(-3, 3), 0, 15)
                hc["p2"] = _clamp(hc["p2"] + random.randint(-5, 5), 0, 30)

            if "mid_cloud" in groups:
                mc = candidate["mid_cloud"]
                mc["b0"] = _clamp(mc["b0"] + random.randint(-3, 3), 0, 10)
                mc["p1"] = _clamp(mc["p1"] + random.randint(-5, 5), 0, 25)

            if "humid" in groups:
                hu = candidate["humid"]
                hu["bonus_t"] = _clamp(hu["bonus_t"] + random.randint(-5, 5), 20, 60)
                hu["bonus_v"] = _clamp(hu["bonus_v"] + random.randint(-2, 2), 0, 8)
                hu["penalty_t"] = _clamp(hu["penalty_t"] + random.randint(-5, 5), 70, 100)
                hu["penalty_v"] = _clamp(hu["penalty_v"] + random.randint(-3, 3), 0, 20)

            if "wcode" in groups:
                wc = candidate["wcode"]
                wc["clear_bonus"] = _clamp(wc["clear_bonus"] + random.randint(-2, 2), 0, 10)
                wc["storm_penalty"] = _clamp(wc["storm_penalty"] + random.randint(-5, 5), 5, 40)
                wc["snow_penalty"] = _clamp(wc["snow_penalty"] + random.randint(-5, 5), 5, 35)
                wc["fog_penalty"] = _clamp(wc["fog_penalty"] + random.randint(-5, 5), 5, 30)

            acc = _evaluate_weights(rows, candidate)
            if acc > best_acc:
                best_acc = acc
                best_weights = candidate
                # 阶段2以当前最优为起点继续搜索
                search_base = json.loads(json.dumps(candidate))

    if best_acc > _evaluate_weights(rows, current):
        save_weights(best_weights)
        recalc_history()
        return {
            "accuracy": round(best_acc / len(rows) * 100, 1),
            "samples": len(rows),
        }
    return None


def recalc_history():
    """用最新权重重新计算所有历史预测中的 probability/ecProbability"""
    history = load_forecast_history()
    for date, h in history.items():
        if not h.get("ec"):
            continue
        month = int(date[5:7])
        details = h["ec"]["details"]
        prob = calculate_probability(details, month)
        h["probability"] = prob
        h["ecProbability"] = prob
    ensure_data_dir()
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# 挂载静态文件（放在最后，作为 fallback）
app.mount("/", StaticFiles(directory="static", html=True), name="static")
