@echo off
chcp 65001 >nul
title Agent Telegram 审批网关
echo 正在启动 Telegram 远程审批网关...
python "%~dp0gateway.py"
pause