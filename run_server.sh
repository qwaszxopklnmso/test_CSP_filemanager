#!/usr/bin/env bash
# CSP File Manager - TEST / PRACTICE ONLY
# Linux / WSL 启动脚本：前台运行，日志实时输出，Ctrl+C 停止。
set -euo pipefail
cd "$(dirname "$0")"

export PYTHONUNBUFFERED=1
export CSP_PORT="${CSP_PORT:-8010}"

echo "============================================================"
echo " CSP File Manager - TEST / PRACTICE ONLY"
echo " URL: http://127.0.0.1:${CSP_PORT}"
echo " Logs are shown below in real time."
echo " Press Ctrl+C to stop the server."
echo "============================================================"
echo

# 部署在 nginx 等可信反向代理后面时才打开（默认关闭，防 X-Forwarded-For 伪造）：
# export CSP_TRUST_PROXY=1

exec python3 app.py
