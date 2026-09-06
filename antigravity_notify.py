# -*- coding: utf-8 -*-
import os
import sys
import json
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def main():
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    transcript_path = payload.get("transcriptPath")
    if not transcript_path or not os.path.exists(transcript_path):
        return

    # 从 transcript.jsonl 中读取最后一次 MODEL 的最终回复
    last_response = None
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                    if step.get("source") == "MODEL" and step.get("content"):
                        content = step["content"].strip()
                        # 过滤纯日志或空内容
                        if content and not content.startswith("Created At:"):
                            last_response = content
                            break
                except Exception:
                    continue
    except Exception as e:
        print("读取 transcript 失败:", e)
        return

    if not last_response:
        return

    config = load_config()
    bot_token = os.environ.get("ANTIGRAVITY_TG_TOKEN")
    if not bot_token:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
            bot_token, _ = winreg.QueryValueEx(key, "ANTIGRAVITY_TG_TOKEN")
        except Exception:
            pass
    if not bot_token:
        bot_token = config.get("bot_token")
    allowed_chat_ids = config.get("allowed_chat_ids", [])
    proxy = config.get("proxy", "http://127.0.0.1:12334")

    if not bot_token or not allowed_chat_ids:
        return

    # 格式化并发送到 Telegram（如果超长分段发送）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    
    # 分段 3500 字符
    max_len = 3500
    parts = [last_response[i:i+max_len] for i in range(0, len(last_response), max_len)]

    for part in parts:
        for chat_id in allowed_chat_ids:
            try:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                data = json.dumps({
                    "chat_id": chat_id,
                    "text": part
                }).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                opener.open(req, timeout=15)
            except Exception as e:
                print(f"发送消息到 {chat_id} 失败:", e)

if __name__ == "__main__":
    main()