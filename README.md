# Antigravity Telegram Gateway

在手机 Telegram 与 PC 本地运行的 Google Antigravity 之间搭建的轻量交互与控制网关。

支持在手机端实时查看电脑端对话、发送新 Prompt、切换历史会话以及远程确认敏感工具操作。

---

## 主要功能

1. **消息双向同步**
   - 电脑端 Antigravity 回复时，实时将内容推送至绑定的 Telegram。
   - 手机端直接在 Telegram 输入并发送文字，即可自动注入到电脑端输入框并触发执行。
   - 支持一键暂停 / 恢复消息推送，避免频繁通知打扰。

2. **多工作区与历史会话浏览**
   - 自动扫描本地 Antigravity 存储的所有会话并按工作区（如 C 盘、D 盘项目）分组。
   - 提供分页浏览与一键切换按钮，点击后电脑端界面自动跟随跳转至对应会话。

3. **电脑端草稿防覆盖保护**
   - 手机端发送消息前，会自动检测电脑端输入框是否已存在尚未发送的文字内容；若有，则主动拦截并提示，避免覆盖本地正在编辑的内容。

4. **远程敏感操作审批**
   - 支持对终端命令执行、核心文件读写等高危工具调用进行拦截，并在 Telegram 弹出包含执行摘要的审批按钮，由用户远程批准或拒绝。

---

## 运行原理

- **下发指令**：通过 Chrome DevTools Protocol (CDP) 动态连接 Antigravity 界面，模拟原生输入与按键触发。
- **获取回复**：以字节偏移量增量监听本地会话转录日志（	ranscript.jsonl），解析最新大模型回复并格式化推送。
- **网络通信**：使用 Python 内置库与 Telegram Bot API 长轮询通信，支持本地代理。

---

## 环境要求

- **操作系统**：Windows 10 / 11
- **Python**：Python 3.10 及以上
- **Google Antigravity**：启动时需启用远程调试端口（网关会自动读取 %APPDATA%\Antigravity\DevToolsActivePort 或探活常规端口 12119 / 2989 / 9222）
- **网络**：若处于特定网络环境，需准备本地 HTTP 代理（如 127.0.0.1:12334）

---

## 快速配置

### 1. 安装依赖

`ash
pip install -r requirements.txt
`

### 2. 配置 Bot Token

推荐将 Telegram Bot Token 设置为系统环境变量（避免将私钥写入代码）：

- **Windows 环境变量名**：ANTIGRAVITY_TG_TOKEN
- 也可直接写入系统注册表 HKCU\Environment，网关会自动读取。

### 3. 配置参数

复制配置模板：

`ash
copy config.example.json config.json
`

编辑 config.json：

`json
{
  allowed_chat_ids: [
    123456789
  ],
  proxy: http://127.0.0.1:12334,
  port: 28888,
  timeout_seconds: 180,
  intercept_tools: [
    run_command,
    write_to_file,
    replace_file_content
  ],
  default_on_timeout: ask,
  auto_bind_on_start: false,
  sync_messages: true
}
`

- llowed_chat_ids: 允许与 Bot 交互的 Telegram 用户 Chat ID 白名单（防止他人误触）。
- proxy: 本地代理地址（如不需要可设为 
ull 或留空）。
- sync_messages: 是否默认开启电脑端消息向手机端同步推送。

---

## 运行与停止

- **前台测试运行**：
  `ash
  python gateway.py
  `
  或者双击运行 start_gateway.bat。

- **后台静默常驻**：
  双击 start_background.vbs（无黑框后台运行）。

- **停止服务**：
  双击 stop_gateway.bat。

---

## Telegram 常用操作

| 命令 / 按钮 | 功能说明 |
| :--- | :--- |
| /start | 检查网关运行状态，并唤起常驻底部快捷菜单 |
| /list | 查看所有工作区会话列表（支持分页与点击切换） |
| /new | 在电脑端 Antigravity 界面新建对话 |
| ⏸️ 暂停 / ▶️ 恢复接收消息 | 快速启闭电脑端消息推送 |
| 直接输入文字 | 向电脑当前会话发送提示词并开始生成 |

---

## 开源协议

MIT License
