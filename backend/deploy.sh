#!/bin/bash
set -e

echo "=================================="
echo "  梅里雪山预测服务 - 一键部署脚本"
echo "=================================="

# 检查是否在 root 下运行
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 自动获取项目路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "项目路径: $PROJECT_DIR"

# 1. 更新系统
echo "[1/7] 更新系统包..."
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nginx curl

# 2. 安装 Python 依赖
echo "[2/7] 安装 Python 依赖..."
cd "$SCRIPT_DIR"
pip3 install -r requirements.txt

# 3. 生成 systemd 服务配置（自动适配实际路径）
echo "[3/7] 配置 systemd 服务..."
cat > /etc/systemd/system/meili.service <<EOF
[Unit]
Description=梅里雪山日照金山预测服务
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable meili

# 4. 启动服务
echo "[4/7] 启动服务..."
systemctl restart meili

# 5. 配置 Nginx
echo "[5/7] 配置 Nginx..."
cp nginx.conf /etc/nginx/sites-available/meili

# 删除默认站点（如果有）
rm -f /etc/nginx/sites-enabled/default

# 启用站点
ln -sf /etc/nginx/sites-available/meili /etc/nginx/sites-enabled/meili

# 测试配置
nginx -t

# 重启 Nginx
systemctl restart nginx
systemctl enable nginx

# 6. 配置防火墙（如有必要）
echo "[6/7] 检查防火墙配置..."
if command -v ufw &> /dev/null; then
    ufw allow 22/tcp || true
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
    ufw allow 8000/tcp || true
    echo "  UFW 防火墙规则已更新"
fi

# 7. 检查状态
echo "[7/7] 检查服务状态..."
sleep 2

if systemctl is-active --quiet meili; then
    echo "✅ meili 服务运行正常"
else
    echo "❌ meili 服务启动失败，请检查日志: journalctl -u meili -n 50"
    exit 1
fi

if systemctl is-active --quiet nginx; then
    echo "✅ Nginx 运行正常"
else
    echo "❌ Nginx 启动失败"
    exit 1
fi

# 获取公网 IP
PUBLIC_IP=$(curl -s http://checkip.amazonaws.com || curl -s http://icanhazip.com || echo "你的服务器IP")

echo ""
echo "=================================="
echo "  🎉 部署完成！"
echo "=================================="
echo ""
echo "访问地址:"
echo "  - HTTP (Nginx): http://${PUBLIC_IP}"
echo "  - 直接访问后端: http://${PUBLIC_IP}:8000"
echo ""
echo "常用命令:"
echo "  查看服务状态: systemctl status meili"
echo "  查看日志:     journalctl -u meili -f"
echo "  重启服务:     systemctl restart meili"
echo "  停止服务:     systemctl stop meili"
echo "  重载 Nginx:   nginx -t && systemctl reload nginx"
echo ""
echo "如需 HTTPS，请先配置域名，然后运行:"
echo "  apt-get install -y certbot python3-certbot-nginx"
echo "  certbot --nginx -d your-domain.com"
echo ""
