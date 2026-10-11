#!/bin/bash
# 在飞牛上构建并启动项目库统计程序容器
set -e

DIR="${1:-/vol1/docker/projlib}"
cd "$DIR"

echo "==> 目录: $DIR"
echo "==> 构建镜像..."
docker build -t projlib:latest .

echo "==> 停止旧容器（如有）..."
docker stop projlib 2>/dev/null || true
docker rm projlib 2>/dev/null || true

echo "==> 启动容器..."
docker run -d --name projlib --restart unless-stopped \
  -p 5666:8775 \
  -v "$DIR/data:/data" \
  -e TZ=Asia/Shanghai \
  -e PROJLIB_PORT=8775 \
  -e PROJLIB_DATA_DIR=/data \
  projlib:latest

sleep 4
echo "==> 容器状态:"
docker ps --filter name=projlib --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo "==> 健康检查:"
docker exec projlib python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8775/api/health',timeout=5).read().decode())" || echo "健康检查失败"
