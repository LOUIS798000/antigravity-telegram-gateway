# -*- coding: utf-8 -*-
import os
import sys
import json
import urllib.request
import urllib.error

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8-sig')
        sys.stderr.reconfigure(encoding='utf-8-sig')
    except Exception:
        pass

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def main():
    config = load_config()
    intercept_tools = set(config.get('intercept_tools', ['run_command', 'write_to_file', 'replace_file_content']))
    port = config.get('port', 28888)

    # 从 stdin 读取 Antigravity Hook 传递的完整上下文
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_call = payload.get('toolCall', {})
    tool_name = tool_call.get('name') or payload.get('toolName') or 'unknown'
    args = tool_call.get('args') or payload.get('toolArgs') or {}

    # 如果不在敏感工具列表中，直接自动放行，提升流畅度
    if tool_name not in intercept_tools:
        print(json.dumps({'decision': 'allow'}))
        return

    # 构造审批请求
    summary = args.get('toolSummary') or args.get('toolAction') or ''
    post_data = {
        'agent': 'Antigravity',
        'tool': tool_name,
        'args': args,
        'summary': summary,
        'timeout': config.get('timeout_seconds', 180)
    }

    url = f'http://127.0.0.1:{port}/approve'
    req = urllib.request.Request(
        url,
        data=json.dumps(post_data, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'AntigravityHook/1.0'}
    )

    try:
        with urllib.request.urlopen(req, timeout=post_data['timeout'] + 10) as resp:
            body = resp.read().decode('utf-8')
            res = json.loads(body)
            decision = res.get('decision', 'ask')
            reason = res.get('reason', '')
            out = {'decision': decision}
            if reason:
                out['reason'] = reason
            print(json.dumps(out, ensure_ascii=False))
            return
    except Exception as e:
        # 网关异常或未启动时，安全降级至桌面询问，避免死锁
        print(json.dumps({
            'decision': 'ask',
            'reason': f'Telegram审批网关未响应 ({e})，转交桌面处理'
        }, ensure_ascii=False))

if __name__ == '__main__':
    main()
