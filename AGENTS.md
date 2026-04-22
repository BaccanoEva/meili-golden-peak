# AGENTS.md — 梅里雪山日照金山预测服务

本文档记录本项目的背景、架构和技术决策，供后续 Agent 或开发者快速上手。

---

## 项目背景

用户希望构建一个 Web 服务，预测云南梅里雪山（飞来寺观景台）未来几天的**日照金山**出现概率。

**日照金山**指日出/日落时分阳光照射在雪山主峰（卡瓦格博，海拔 6740m）上呈现金色的自然现象。预测核心在于判断日出时刻山顶是否被云层遮挡。

用户后续还希望：
- 接入**双模型交叉验证**（ECMWF + ICON）提升预报可信度
- 加入**用户反馈系统**，积累实况数据用于后续模型优化
- 支持**阿里云部署**，对外提供可访问的网址

---

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI (Python) | 异步高性能，自动生成 OpenAPI 文档 |
| 数据源 | Open-Meteo | 免费全球气象 API，无需 API Key |
| 数值模型 | ECMWF IFS 0.25° + DWD ICON Seamless | 欧洲中心 + 德国气象局双模型 |
| 前端 | 原生 HTML/CSS/JS | 单页应用，无构建工具，直接由 FastAPI StaticFiles serve |
| 进程守护 | systemd | Linux 系统级服务管理 |
| 反向代理 | Nginx | 80/443 端口转发到后端 8000 端口 |
| 部署 | 阿里云 ECS | 支持 Ubuntu 22.04 LTS |

---

## 项目结构

```
meri-golden-peak/
├── AGENTS.md                 # 本文档
├── README.md                 # 用户-facing 使用说明
├── .gitignore
└── backend/
    ├── app.py                # FastAPI 主服务：/api/forecast + /api/health + static files
    ├── requirements.txt      # Python 依赖
    ├── deploy.sh             # 阿里云一键部署脚本（安装依赖 + systemd + Nginx）
    ├── meili.service         # systemd 服务配置文件模板
    ├── nginx.conf            # Nginx 反向代理配置模板
    └── static/
        └── index.html        # 前端单页应用
```

**关键约定**：
- 后端同时承担 **API 服务** 和 **静态文件服务器** 职责（`app.mount("/", StaticFiles(...))`）
- 前端不直接请求 Open-Meteo，所有气象数据通过后端 `/api/forecast` 获取
- 用户反馈数据仅存于浏览器 `localStorage`，未接入后端数据库（如需多设备同步需后续扩展）

---

## 核心算法

### 概率计算

在日出时刻取该小时的气象数据，基于以下因子线性扣分：

| 因子 | 扣分规则 |
|------|---------|
| 高云覆盖 | >85% 扣 55，>60% 扣 35，>30% 扣 15，>10% 扣 5 |
| 中云覆盖 | >80% 扣 25，>50% 扣 12 |
| 低云覆盖 | >80% 扣 25，>50% 扣 12 |
| 降水 | >2mm 扣 40，>0.5mm 扣 25，>0 扣 10 |
| 湿度 | >95% 扣 15，>85% 扣 8 |
| 恶劣天气代码 | 雷暴扣 30，降雪扣 25，大雾扣 20 |

基础分 100，扣到 0 为止。

### 双模型综合

```
diff = abs(EC概率 - ICON概率)
combined = (EC + ICON) / 2

if diff > 40:   combined *= 0.85
elif diff > 25: combined *= 0.93

confidence = 100 - diff
```

### 一致性标签逻辑

- `confidence >= 80 && combined >= 70` → "🔒 双模型确认"
- `confidence >= 80 && combined < 35` → "双模型一致看差"
- `confidence < 60` → "〰️ 模型分歧"

---

## API 规范

### GET /api/forecast

返回未来 7 天预测数组。

关键字段：
- `probability`: 综合概率（0-100）
- `ecProbability`: ECMWF 模型概率
- `iconProbability`: ICON 模型概率
- `confidence`: 一致性评分（0-100）
- `diff`: 两模型绝对差值
- `advice`: 中文建议文本
- `tags`: 标签数组（高概率、双模型确认、模型分歧等）
- `ec.details`: ECMWF 详细气象数据（含 `weather_desc`）
- `icon.details`: ICON 详细气象数据

### GET /api/health

健康检查，返回 `{"status": "ok"}`。

---

## 前端功能

- **7 天预测卡片**：圆环进度条展示概率，左侧彩色竖条标识高低概率
- **双模型对比**：每张卡片展示 EC/ICON 各自概率和一致性状态
- **详情弹窗**：点击卡片查看两模型在高云/中云/低云/降水/湿度等维度的详细对比
- **实况反馈**：今天及过去日期可标记"拍到了/没看到"，数据存 localStorage
- **统计面板**：右上角 📊 按钮查看整体准确率、各概率区间命中率、模型对比、历史记录

---

## 部署指南

### 本地开发

```bash
cd backend
pip3 install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8000
# 浏览器访问 http://localhost:8000
```

### 阿里云生产部署

1. 购买 ECS（推荐香港/新加坡免备案，或大陆节点+域名备案）
2. 安全组开放 22、80、8000 端口
3. 上传代码：`scp -r backend root@IP:/root/meili-golden-peak/`
4. 服务器上执行：`cd /root/meili-golden-peak/backend && sudo bash deploy.sh`
5. 访问 `http://服务器IP`

deploy.sh 会自动完成：
- 安装 python3、pip3、nginx
- pip 安装项目依赖
- 配置 systemd 服务（自动重启、开机自启）
- 配置 Nginx 反向代理

### 常用运维命令

```bash
systemctl status meili      # 查看服务状态
journalctl -u meili -f      # 查看实时日志
systemctl restart meili     # 重启服务
nginx -t && systemctl reload nginx  # 重载 Nginx
```

---

## 扩展建议

### 短期（无需改架构）
- **PWA 支持**：添加 `manifest.json` 和 Service Worker，支持离线访问和添加到主屏幕
- **定时推送**：服务器 Cron 每天 05:30 检查当天概率，若 >80% 则发送邮件/企业微信提醒

### 中期（需后端扩展）
- **数据库接入**：用 SQLite/PostgreSQL 存储用户反馈，支持多设备同步和数据分析
- **权重调优**：积累 50+ 条反馈后，用逻辑回归或简单线性回归优化各气象因子的扣分权重
- **接入更多模型**：Open-Meteo 支持 GFS（美国）、MeteoSwiss 等，可扩展为三模型甚至集合预报

### 长期
- **图像识别**：接入景区摄像头或用户上传照片，用 CV 自动识别是否出现日照金山
- **微信小程序**：复用现有后端 API，前端用小程序框架重写
- **付费订阅**：高概率日期的短信/微信推送增值服务

---

## 已知限制

1. **Open-Meteo 免费 tier 限制**：ECMWF 模型在部分参数上可能返回 null（已选用 `ecmwf_ifs025` 解决）
2. **预报时效**：最多 7-10 天，超过后误差显著增大
3. **地形微气候**：数值模型分辨率（13-25km）无法精确捕捉梅里雪山复杂地形效应，预测仅供参考
4. **反馈数据本地存储**：当前版本反馈仅存于浏览器 localStorage，换设备/清缓存会丢失

---

## 修改历史

| 时间 | 变更 |
|------|------|
| 2026-04-22 | 初始版本：纯前端单页应用，直接请求 Open-Meteo |
| 2026-04-22 | 双模型升级：接入 ECMWF + ICON，新增一致性分析和分歧惩罚 |
| 2026-04-22 | 实况反馈系统：用户可标记拍到/未拍，统计面板展示准确率 |
| 2026-04-22 | 前后端分离：前端改为请求后端 `/api/forecast`，后端承担静态文件服务 |
| 2026-04-22 | 部署支持：添加 deploy.sh、systemd 配置、Nginx 配置，支持阿里云一键部署 |
| 2026-04-22 | 推送到 GitHub，仓库初始化 |

---

## 联系与贡献

本项目由 AI Agent 辅助开发。如需修改：
1. 阅读本文件了解架构约束
2. 修改后更新 `AGENTS.md` 的"修改历史"章节
3. 遵循现有代码风格（Python PEP8，前端原生 JS/CSS）
