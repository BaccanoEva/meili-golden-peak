# 梅里雪山 · 日照金山预测（前后端分离版）

基于 **ECMWF + ICON** 双数值预报模型交叉验证，预测未来 7 天梅里雪山（飞来寺观景台）出现「日照金山」的概率。

**架构**：FastAPI 后端 + 纯前端页面，前后端分离部署。

---

## 项目结构

```
meri-golden-peak/
├── backend/
│   ├── app.py              # FastAPI 后端主服务
│   ├── requirements.txt    # Python 依赖
│   └── static/
│       └── index.html      # 前端页面（由后端 serve）
└── README.md
```

- **后端**：请求 Open-Meteo 双模型数据 → 计算概率 → 提供 `/api/forecast` 接口
- **前端**：请求后端 API → 渲染卡片 → 用户反馈 → 统计面板

---

## 快速启动

### 1. 安装依赖

```bash
cd backend
pip3 install -r requirements.txt
```

依赖：`fastapi`, `uvicorn`, `requests`

### 2. 启动后端服务

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### 3. 浏览器访问

打开 http://localhost:8000

> 后端会自动 serve `static/index.html`，无需额外配置前端服务器。

---

## API 接口

| 端点 | 说明 |
|------|------|
| `GET /api/forecast` | 未来 7 天双模型预测数据 |
| `GET /api/health` | 健康检查 |

### `/api/forecast` 响应示例

```json
{
  "location": "梅里雪山·飞来寺",
  "forecast": [
    {
      "date": "2026-04-22",
      "weekday": "今天",
      "sunrise": "06:52",
      "sunset": "19:53",
      "probability": 15,
      "ecProbability": 20,
      "iconProbability": 25,
      "confidence": 95,
      "diff": 5,
      "advice": "❌ 几乎无望，改日再来",
      "tags": [
        {"text": "极低", "highlight": false},
        {"text": "双模型一致看差", "highlight": false, "consensus": true},
        {"text": "有降水", "highlight": false}
      ],
      "ec": {
        "date": "2026-04-22",
        "sunrise": "06:52",
        "details": {
          "cloudcover_high": 100,
          "cloudcover_mid": 100,
          "cloudcover_low": 70,
          "precipitation": 1.7,
          "weather_code": 61,
          "weather_desc": "小雨",
          ...
        }
      },
      "icon": {
        "date": "2026-04-22",
        "sunrise": "06:52",
        "details": {
          "cloudcover_high": 100,
          "cloudcover_mid": 100,
          "cloudcover_low": 65,
          "precipitation": 0.3,
          "weather_code": 80,
          "weather_desc": "小阵雨",
          ...
        }
      }
    }
  ]
}
```

---

## 双模型分析原理

| 模型 | 来源 | 特点 |
|------|------|------|
| **ECMWF** | 欧洲中期天气预报中心 | 全球公认精度最高的中期预报之一 |
| **ICON** | 德国气象局 (DWD) | 二十面体非静力框架，山地对流处理有优势 |

综合概率算法：

```
diff = |EC概率 - ICON概率|
综合概率 = (EC概率 + ICON概率) / 2

if diff > 40:   综合概率 *= 0.85    # 分歧大，大幅降权
elif diff > 25: 综合概率 *= 0.93    # 分歧中等，小幅降权

一致性评分 = 100 - diff
```

---

## 实况反馈功能

每天（今天及过去日期）卡片底部可以标记：
- **👍 拍到了** — 当天确实出现日照金山
- **👎 没看到** — 当天未出现

点击右上角 **📊** 打开统计面板，查看：

| 统计项 | 说明 |
|--------|------|
| 整体准确率 | 所有反馈记录的命中比例 |
| 高概率命中 | 预测 >80% 且实际出现的比例 |
| 中概率命中 | 预测 40-80% 且实际出现的比例 |
| 低概率回避 | 预测 <40% 且确实未出现的比例 |
| ECMWF 准确率 | 单独看欧洲模型的命中情况 |
| ICON 准确率 | 单独看德国模型的命中情况 |
| 一致时准确率 | 两模型分歧 <20% 时的命中情况 |

反馈数据保存在浏览器 `localStorage` 中。

---

## 部署建议

### 生产环境启动

```bash
cd backend
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
```

### 反向代理（Nginx）

```nginx
server {
    listen 80;
    server_name meili.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 扩展为微信小程序

1. 部署后端到云服务器
2. 小程序前端请求 `https://your-domain.com/api/forecast`
3. 反馈数据建议接入后端数据库（当前版本只用 localStorage，多设备不互通）

---

## 数据来源

- [Open-Meteo](https://open-meteo.com/) — 免费开源全球气象 API
- 模型：ECMWF IFS 0.25° + DWD ICON Seamless
- 坐标：28.45°N, 98.82°E（飞来寺观景台，海拔 3400m）

> ⚠️ **免责声明**：气象模型存在不确定性，山地微气候变化快，预测结果仅供摄影行程参考。

---

## License

MIT
