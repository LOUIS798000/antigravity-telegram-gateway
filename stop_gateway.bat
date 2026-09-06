@echo off
chcp 65001 >nul
echo 正在查找并停止审批网关 (端口 28888)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :28888') do (
    echo 正在终止进程 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)
echo 网关已停止。
pause