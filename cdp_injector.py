# -*- coding: utf-8 -*-
import os
import asyncio
import json
import time
import urllib.request
import websockets

def get_devtools_port():
    """
    动态获取 Antigravity 当前实例的 CDP 调试端口
    通过读取 %APPDATA%/Antigravity/DevToolsActivePort 确保每次重启都能秒级自动识别
    """
    devtools_file = os.path.expandvars(r"%APPDATA%\Antigravity\DevToolsActivePort")
    if os.path.exists(devtools_file):
        try:
            with open(devtools_file, "r", encoding="utf-8") as f:
                line = f.readline().strip()
                if line.isdigit():
                    return int(line)
        except Exception:
            pass
    for p in (12119, 2989, 9222):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/json/version", timeout=0.3) as r:
                if r.status == 200:
                    return p
        except Exception:
            pass
    return 12119

def find_active_tab():
    port = get_devtools_port()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/list")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tabs = json.loads(resp.read().decode("utf-8"))
        pages = [t for t in tabs if t.get("type") == "page"]
        if pages:
            for tab in pages:
                url = tab.get("url", "")
                if "/c/" in url:
                    return tab.get("webSocketDebuggerUrl")
            return pages[0].get("webSocketDebuggerUrl")
        if tabs:
            return tabs[0].get("webSocketDebuggerUrl")
    except Exception as e:
        print(f"查找 Tab 异常 (端口 {port}):", e)
    return None

async def send_to_antigravity(prompt_text):
    ws_url = find_active_tab()
    if not ws_url:
        return False, "未找到 Antigravity 界面调试端口"

    async with websockets.connect(ws_url) as ws:
        # 1. 如果当前正在运行（有取消按钮），等待其空闲（最多等 15 秒）
        for _ in range(15):
            js_check_idle = """
            (() => {
                const cancelBtn = document.querySelector('button[aria-label*="取消"], button[aria-label*="Cancel"], button[aria-label*="Stop"]');
                return cancelBtn === null;
            })()
            """
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js_check_idle, "returnByValue": True}}))
            res = json.loads(await ws.recv())
            is_idle = res.get("result", {}).get("result", {}).get("value", True)
            if is_idle:
                break
            await asyncio.sleep(1)

        # 2. 聚焦输入框；若电脑端已有未发送内容则拒绝注入，避免覆盖用户正在输入的草稿
        js_focus = """
        (() => {
            const el = document.querySelector('div[contenteditable="true"]');
            if (!el) return {found: false};
            const hasContent = (el.innerText || '').trim().length > 0;
            if (hasContent) return {found: true, hasContent: true};
            el.focus();
            return {found: true, hasContent: false};
        })()
        """
        await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {"expression": js_focus, "returnByValue": True}}))
        res = json.loads(await ws.recv())
        info = res.get("result", {}).get("result", {}).get("value", {})
        if not info.get("found"):
            return False, "未找到输入框"
        if info.get("hasContent"):
            return False, "电脑端输入框已有未发送内容，为防覆盖您的草稿，本次未注入。请先在电脑端发送或清空该内容后重试。"

        await asyncio.sleep(0.1)

        # 3. 使用 CDP 原生 Input.insertText 模拟真实输入
        insert_msg = {
            "id": 3,
            "method": "Input.insertText",
            "params": {"text": prompt_text}
        }
        await ws.send(json.dumps(insert_msg))
        await ws.recv()

        await asyncio.sleep(0.2)

        # 4. 点击 Send message 按钮，双保险 fallback 回车按键
        js_click_send = """
        (() => {
            const sendBtn = document.querySelector('button[aria-label="Send message"], button[aria-label="发送消息"], button[aria-label*="Send"]');
            if (sendBtn && !sendBtn.disabled) {
                sendBtn.click();
                return { clicked: true, method: "button_click" };
            }
            return { clicked: false };
        })()
        """
        await ws.send(json.dumps({"id": 4, "method": "Runtime.evaluate", "params": {"expression": js_click_send, "returnByValue": True}}))
        res = json.loads(await ws.recv())
        click_res = res.get("result", {}).get("result", {}).get("value", {})

        if not click_res.get("clicked"):
            enter_down = {
                "id": 5,
                "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": "rawKeyDown",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                    "text": "\r"
                }
            }
            await ws.send(json.dumps(enter_down))
            await ws.recv()

            enter_up = {
                "id": 6,
                "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": "keyUp",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13
                }
            }
            await ws.send(json.dumps(enter_up))
            await ws.recv()

        return True, "消息已成功输入并触发发送"

def send_prompt(text):
    return asyncio.run(send_to_antigravity(text))

if __name__ == "__main__":
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "测试输入"
    print(send_prompt(msg))