# -*- coding: utf-8 -*-
import os
import sys
import glob
import html
import json
import time
import uuid
import logging
import threading
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.parse
import urllib.error
import asyncio
import websockets

# 引入 CDP 注入与会话服务模块
from cdp_injector import send_prompt, find_active_tab
import session_service

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("AgentApproverGateway")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
BRAIN_DIR = os.path.expanduser(r"~/.gemini/antigravity/brain")
TOKEN_ENV_VAR = "ANTIGRAVITY_TG_TOKEN"  # Bot Token 优先从该环境变量读取，config.json 不再存明文

def get_bot_token():
    """
    智能获取 Bot Token：
    1. 优先读取当前环境中的 ANTIGRAVITY_TG_TOKEN
    2. 若未获取到，直接读取 Windows 用户注册表 HKCU\\Environment（解决未重启终端或子进程未刷新环境变量的问题）
    3. 最后从 config.json 兜底
    """
    token = os.environ.get(TOKEN_ENV_VAR)
    if token and token.strip():
        return token.strip()
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment")
        val, _ = winreg.QueryValueEx(key, TOKEN_ENV_VAR)
        if val and val.strip():
            return val.strip()
    except Exception:
        pass
    cfg = load_config()
    return cfg.get("bot_token", "").strip()

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as e:
            logger.error("读取配置文件失败: %s", e)
    return {}

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error("保存配置文件失败: %s", e)
        return False

# 切换会话
async def async_switch_session(cid):
    ws_url = find_active_tab()
    if not ws_url:
        return False, "未找到活动界面"
    async with websockets.connect(ws_url) as ws:
        # 优先点击侧边栏链接（保留单页路由与section状态，杜绝白屏重载）
        js = f"""
        (async () => {{
            let link = document.querySelector('a[href*="/c/{cid}"]');
            if (link) {{
                link.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                link.click();
                return {{ok: true, method: 'direct_click'}};
            }}
            // 1. 若当前视口未找到，自动展开所有折叠的项目卡片及置顶栏
            const cards = Array.from(document.querySelectorAll('button[data-project-card="true"], h2 button'));
            for (const c of cards) {{{{
                if (c.getAttribute('aria-expanded') === 'false') {{{{
                    try {{{{ c.click(); }}}} catch(e) {{{{}}}}
                }}}}
            }}}}
            await new Promise(r => setTimeout(r, 200));
            link = document.querySelector('a[href*="/c/{cid}"]');
            if (link) {{
                link.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                link.click();
                return {{ok: true, method: 'expanded_click'}};
            }}

            // 2. 尝试在滚动容器中滚动查找（应对虚拟滚动裁剪）
            const sc = document.querySelector('.relative.w-full.h-full.overflow-y-auto.overscroll-none.px-2');
            if (sc && sc.scrollHeight > sc.clientHeight) {{
                sc.scrollTop = sc.scrollHeight;
                sc.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
                await new Promise(r => setTimeout(r, 250));
                link = document.querySelector('a[href*="/c/{cid}"]');
                if (link) {{
                    link.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                    link.click();
                    return {{ok: true, method: 'scrolled_click'}};
                }}
            }}

            // 3. 兜底回退：修改 location.href 强制进入
            window.location.href = window.location.origin + "/c/{cid}";
            return {{ok: true, method: 'href'}};
        }})()
        """
        msg = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": js, "awaitPromise": True, "returnByValue": True}}
        await ws.send(json.dumps(msg))
        await ws.recv()
        return True, "成功"

def switch_session(cid):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, async_switch_session(cid)).result()
    return asyncio.run(async_switch_session(cid))

# 新建会话
async def async_new_session():
    ws_url = find_active_tab()
    if not ws_url:
        return False, "未找到活动界面"
    async with websockets.connect(ws_url) as ws:
        js = """
        (() => {
            const newBtn = Array.from(document.querySelectorAll('button, a')).find(el => 
                (el.innerText && (el.innerText.includes('新建对话') || el.innerText.includes('New Conversation'))) ||
                el.getAttribute('aria-label') === 'New Conversation' ||
                el.getAttribute('title') === 'New Conversation'
            );
            if (newBtn) {
                newBtn.click();
                return {ok: true};
            }
            window.location.href = window.location.origin + "/";
            return {ok: true};
        })()
        """
        msg = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}
        await ws.send(json.dumps(msg))
        await ws.recv()
        return True, "成功"

def new_session():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, async_new_session()).result()
    return asyncio.run(async_new_session())

class TelegramClient:
    def __init__(self, token, proxy=None):
        self.token = token
        self.proxy = proxy
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._setup_opener()

    def _setup_opener(self):
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, method, params=None):
        if not self.token:
            return None
        url = f"{self.base_url}/{method}"
        data = None
        headers = {"User-Agent": "AntigravityApprover/1.0"}
        if params is not None:
            data = json.dumps(params).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=35) as resp:
                body = resp.read().decode("utf-8")
                res = json.loads(body)
                if res.get("ok"):
                    return res.get("result")
                else:
                    logger.warning("Telegram API 错误: %s", res)
                    return None
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            if "message is not modified" in err_body.lower():
                return None
            logger.error("调用 Telegram API %s HTTP 错误 (%s): %s", method, e.code, err_body or e)
            return None
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return None
            logger.error("调用 Telegram API %s 异常: %s", method, e)
            return None

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        import re
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = self.request("sendMessage", payload)
        # 如果携带 parse_mode 发送失败（例如复杂的代码、不平衡的 HTML 标签），自动深度清理标签降级为纯文本重试
        if res is None and parse_mode:
            clean_text = re.sub(r'<[^>]+>', '', text)
            plain_payload = {"chat_id": chat_id, "text": clean_text}
            if reply_markup:
                plain_payload["reply_markup"] = reply_markup
            logger.info("parse_mode=%s 发送失败，自动降级为纯文本兜底发送到 %s", parse_mode, chat_id)
            res = self.request("sendMessage", plain_payload)
        return res

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode=None):
        import re
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        res = self.request("editMessageText", payload)
        if res is None and parse_mode:
            clean_text = re.sub(r'<[^>]+>', '', text)
            plain_payload = {"chat_id": chat_id, "message_id": message_id, "text": clean_text}
            if reply_markup is not None:
                plain_payload["reply_markup"] = reply_markup
            res = self.request("editMessageText", plain_payload)
        return res

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = show_alert
        return self.request("answerCallbackQuery", payload)

    def get_updates(self, offset=None, timeout=20):
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        return self.request("getUpdates", payload)

def get_latest_transcript_path():
    try:
        candidates = glob.glob(os.path.join(BRAIN_DIR, "*", ".system_generated", "logs", "transcript.jsonl"))
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0]
    except Exception as e:
        logger.error("查找 transcript 异常: %s", e)
    return None

def build_list_overview_payload(force_refresh=False):
    """
    构造按项目（C:、d:等）分类的会话列表概览消息和键盘
    """
    projects = session_service.get_projects(force_refresh=force_refresh)
    if not projects:
        return "⚠️ 暂未获取到工作区与会话列表，请确保电脑端 Antigravity 正常打开。", None

    text_lines = ["📋 <b>Antigravity 工作区与会话列表</b>\n"]
    keyboard = []

    for p in projects:
        proj_name = p["project"]
        total_cnt = p.get("count", len(p["items"]))
        is_pinned_group = "固定" in proj_name or "置顶" in proj_name or "PINNED" in proj_name.upper()

        if is_pinned_group:
            clean_name = proj_name.replace("📌", "").strip()
            text_lines.append(f"📌 <b>{html.escape(clean_name)}</b> (共 {total_cnt} 个)")
            prefix = "📌"
        else:
            text_lines.append(f"📁 <b>工作区：{html.escape(proj_name)}</b> (共 {total_cnt} 个会话)")
            prefix = proj_name.replace(":", "").upper()

        # 概览页展示前 5 个
        display_items = p["items"][:5]
        for idx, it in enumerate(display_items, 1):
            time_str = f" <i>({html.escape(it['time'])})</i>" if it.get("time") else ""
            text_lines.append(f"  {idx}. {html.escape(it['title'])}{time_str}")

        text_lines.append("")  # 空行

        # 对应的切换按钮（每行 1 个，带真实标题，防止太长截取前 20 字符）
        for idx, it in enumerate(display_items, 1):
            short_title = it['title']
            if len(short_title) > 18:
                short_title = short_title[:18] + "…"
            btn_text = f"[{prefix}{idx}] {short_title}"
            keyboard.append([{"text": btn_text, "callback_data": f"switch:{it['id']}"}])

        # 如果总数超过 5 个，提供“查看全部”按钮（分页回调，page 用 | 分隔避免与项目名中的冒号冲突）
        if total_cnt > 5:
            keyboard.append([{"text": f"📂 查看全部 {proj_name} ({total_cnt}个)", "callback_data": f"viewp:1|{proj_name}"}])

    # 底部通用工具按钮
    is_syncing = load_config().get("sync_messages", True)
    sync_btn_text = "⏸️ 暂停接收消息" if is_syncing else "▶️ 恢复接收消息"
    sync_btn_cb = "action:sync_pause" if is_syncing else "action:sync_resume"

    keyboard.append([
        {"text": "➕ 新建对话", "callback_data": "action:new"},
        {"text": "🔄 刷新列表", "callback_data": "action:refresh"}
    ])
    keyboard.append([
        {"text": sync_btn_text, "callback_data": sync_btn_cb}
    ])

    return "\n".join(text_lines), {"inline_keyboard": keyboard}

def get_bottom_keyboard(is_syncing):
    """
    构造手机输入框下方的常驻大按键菜单（文案与行为严格保持一致）
    """
    sync_btn = "⏸️ 暂停接收消息" if is_syncing else "▶️ 恢复接收消息"
    return {
        "keyboard": [
            [{"text": "📂 历史会话"}, {"text": "➕ 新建对话"}],
            [{"text": sync_btn}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

def setup_bot_commands(tg_client):
    """
    注册 Telegram 官方原生左侧 [Menu] 菜单命令（全局统一命名）
    """
    cmds = [
        {"command": "list", "description": "📂 历史会话列表"},
        {"command": "new", "description": "➕ 开启新会话"},
        {"command": "pause", "description": "⏸️ 暂停接收消息"},
        {"command": "resume", "description": "▶️ 恢复接收消息"},
        {"command": "start", "description": "ℹ️ 状态与帮助说明"}
    ]
    try:
        tg_client.request("setMyCommands", {"commands": cmds})
    except Exception as e:
        logger.error("设置 Telegram 菜单命令异常: %s", e)

def build_project_all_payload(proj_name, page=1, page_size=10):
    """
    构造某个项目下所有会话的详细列表和按钮（每页10个精致分页，完美贴合手机屏幕高度）
    """
    projects = session_service.get_projects(force_refresh=False)
    target_proj = next((p for p in projects if p["project"].lower() == proj_name.lower()), None)
    if not target_proj:
        return f"⚠️ 未找到工作区 {html.escape(proj_name)}", None

    total_cnt = len(target_proj["items"])
    max_page = max(1, (total_cnt + page_size - 1) // page_size)
    page = min(max(1, page), max_page)
    start = (page - 1) * page_size
    page_items = target_proj["items"][start:start + page_size]

    page_note = f"（第 {page}/{max_page} 页）" if max_page > 1 else ""
    text_lines = [
        f"📂 <b>工作区：{html.escape(target_proj['project'])} 的全部会话</b> (共 {total_cnt} 个){page_note}\n",
        "💡 点击下方任意按钮，电脑端立即跳转对应会话：\n"
    ]

    keyboard = []
    prefix = target_proj['project'].replace(":", "").upper()

    for idx, it in enumerate(page_items, start + 1):
        time_str = f" <i>({html.escape(it['time'])})</i>" if it.get("time") else ""
        text_lines.append(f"{idx}. {html.escape(it['title'])}{time_str}")

        short_title = it['title']
        if len(short_title) > 20:
            short_title = short_title[:20] + "…"
        btn_text = f"{idx}. {short_title}"
        keyboard.append([{"text": btn_text, "callback_data": f"switch:{it['id']}"}])

    # 翻页按钮
    nav_row = []
    if page > 1:
        nav_row.append({"text": "⬅️ 上一页", "callback_data": f"viewp:{page - 1}|{target_proj['project']}"})
    if page < max_page:
        nav_row.append({"text": "下一页 ➡️", "callback_data": f"viewp:{page + 1}|{target_proj['project']}"})
    if nav_row:
        keyboard.append(nav_row)

    # 底部返回按钮
    keyboard.append([
        {"text": "◀️ 返回项目列表", "callback_data": "action:overview"},
        {"text": "➕ 新建对话", "callback_data": "action:new"}
    ])

    return "\n".join(text_lines), {"inline_keyboard": keyboard}

class ApprovalGateway:
    def __init__(self):
        self.config = load_config()
        self.bot_token = get_bot_token()
        self.proxy = self.config.get("proxy", "http://127.0.0.1:12334")
        self.allowed_chat_ids = set(self.config.get("allowed_chat_ids", []))
        self.port = self.config.get("port", 28888)
        self.timeout_seconds = self.config.get("timeout_seconds", 180)
        self.pending_requests = {}
        self.lock = threading.Lock()
        self.tg = TelegramClient(self.bot_token, self.proxy)

    def reload_config(self):
        self.config = load_config()
        self.bot_token = get_bot_token()
        self.proxy = self.config.get("proxy", "http://127.0.0.1:12334")
        self.allowed_chat_ids = set(self.config.get("allowed_chat_ids", []))
        self.tg = TelegramClient(self.bot_token, self.proxy)

    def watch_transcript_loop(self):
        logger.info("Antigravity 聊天框实时同步线程已启动...")
        file_positions = {}  # path -> 已读取的字节偏移量

        # 启动初始化：仅将启动前已存在的历史会话游标置为末尾，避免历史旧消息刷屏
        try:
            initial_files = glob.glob(os.path.join(BRAIN_DIR, "*", ".system_generated", "logs", "transcript.jsonl"))
            for p in initial_files:
                try:
                    file_positions[p] = os.path.getsize(p)
                except OSError:
                    pass
            logger.info("已完成 %d 个历史会话游标初始化，后续新增回复将实时推送", len(file_positions))
        except Exception as init_e:
            logger.error("初始化历史会话游标异常: %s", init_e)

        def session_label(path):
            """从 transcript 路径提取会话 ID，并尝试匹配侧边栏真实标题"""
            try:
                # ...\brain\<sid>\.system_generated\logs\transcript.jsonl
                sid = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
                for p in session_service.get_projects():
                    for it in p["items"]:
                        if it["id"] == sid:
                            return it["title"]
                return sid[:8]
            except Exception:
                return ""

        while True:
            try:
                candidates = glob.glob(os.path.join(BRAIN_DIR, "*", ".system_generated", "logs", "transcript.jsonl"))
                if not candidates:
                    time.sleep(1)
                    continue

                candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)

                # 监听最近活跃的 10 个会话，避免会话挤压
                for current_path in candidates[:10]:
                    try:
                        size = os.path.getsize(current_path)
                    except OSError:
                        continue

                    # 若为运行期间新创建的会话，offset 默认为 0，保证新会话第一条回复绝不遗漏
                    offset = file_positions.get(current_path, 0)
                    if size < offset:
                        # 文件被重写/轮转（行数变少），重置游标避免永久失联
                        logger.info("转录本被重写或轮转，重置读取位置: %s", current_path)
                        offset = 0
                    if size == offset:
                        continue

                    try:
                        with open(current_path, "rb") as f:
                            f.seek(offset)
                            new_data = f.read()
                    except OSError:
                        continue

                    # 只处理完整行，最后一行若尚未写完则留到下轮
                    if not new_data.endswith(b"\n"):
                        cut = new_data.rfind(b"\n")
                        if cut == -1:
                            continue
                        new_data = new_data[:cut + 1]

                    file_positions[current_path] = offset + len(new_data)
                    text = new_data.decode("utf-8", errors="replace")

                    for raw_line in text.splitlines():
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            item = json.loads(raw_line)
                            if item.get("source") == "MODEL" and item.get("type") == "PLANNER_RESPONSE":
                                content = item.get("content")
                                if content and not item.get("tool_calls"):
                                    content = content.strip()
                                    if content:
                                        # 检查用户是否开启了消息同步（实时从文件读取最新状态）
                                        if not load_config().get("sync_messages", True):
                                            logger.info("用户已暂停消息接收，忽略向 Telegram 推送 (字数: %d)", len(content))
                                            continue
                                        label = session_label(current_path)
                                        header = f"💬 <b>{html.escape(label)}</b>\n────────────\n" if label else ""
                                        logger.info("检测到会话新回复，全量同步推送至 Telegram (字数: %d)...", len(content))
                                        max_chunk = 3800
                                        chunks = [content[i:i + max_chunk] for i in range(0, len(content), max_chunk)]
                                        for idx, chunk in enumerate(chunks):
                                            body = html.escape(chunk)
                                            if len(chunks) > 1 and idx < len(chunks) - 1:
                                                body += f"\n<i>(续 {idx + 1}/{len(chunks)})</i>"
                                            prefix = header if idx == 0 else ""
                                            for cid in self.allowed_chat_ids:
                                                self.tg.send_message(cid, prefix + body, parse_mode="HTML")
                        except Exception as parse_e:
                            logger.error("解析转录行异常: %s", parse_e)

            except Exception as e:
                logger.error("监听转录本异常: %s", e)

            time.sleep(1)

    def poll_telegram_loop(self):
        logger.info("Telegram 监听轮询线程已启动...")
        offset = None
        while True:
            try:
                if not self.bot_token:
                    time.sleep(3)
                    self.reload_config()
                    continue

                updates = self.tg.get_updates(offset=offset, timeout=20)
                if not updates:
                    continue

                for update in updates:
                    offset = update["update_id"] + 1
                    self.handle_tg_update(update)
            except Exception as e:
                logger.error("Telegram 轮询异常: %s", e)
                time.sleep(3)

    def handle_tg_update(self, update):
        if "message" in update:
            msg = update["message"]
            chat_id = msg.get("chat", {}).get("id")
            user_name = msg.get("from", {}).get("first_name", "User")
            text = (msg.get("text") or "").strip()

            if chat_id not in self.allowed_chat_ids:
                return

            # 图片/贴纸/语音等无文本消息直接忽略，避免空注入
            if not text:
                return

            # 兼容 "/start@javirs798_bot" 这类带机器人名的命令
            text = text.split("@", 1)[0] if text.startswith("/") else text

            if text in ("/start", "/status", "/sync"):
                is_syncing = self.config.get("sync_messages", True)
                sync_desc = "🟢 正在接收 (电脑回复实时推送)" if is_syncing else "🔴 已暂停 (电脑回复保持静音)"
                reply = (
                    f"✅ <b>Antigravity 随身客户端</b>\n\n"
                    f"你好 {html.escape(user_name)}！所有菜单与按键已全部理顺并实时联动：\n\n"
                    f"• <b>当前推送状态</b>: <b>{sync_desc}</b>\n\n"
                    f"📋 <b>统一操作方式</b>：\n"
                    f"• <b>底部常驻按键</b>：看屏幕正下方的大按钮，一键直达，永远保持最新状态。\n"
                    f"• <b>左下角 [Menu]</b>：\n"
                    f"  - <code>/list</code> —— 📂 历史会话列表\n"
                    f"  - <code>/new</code> —— ➕ 开启新会话\n"
                    f"  - <code>/pause</code> —— ⏸️ 暂停接收消息\n"
                    f"  - <code>/resume</code> —— ▶️ 恢复接收消息\n"
                    f"• <b>直接对话</b>：发送任意文字，电脑当前会话立刻输入并执行。\n"
                    f"• <b>高危审批</b>：敏感操作自动推送弹窗供您把关。"
                )
                self.tg.send_message(chat_id, reply, reply_markup=get_bottom_keyboard(is_syncing), parse_mode="HTML")

            elif text in ("/pause", "/stop_sync", "/sync_off", "⏸️ 暂停接收消息", "暂停接收消息", "终止接收消息"):
                self.config["sync_messages"] = False
                save_config(self.config)
                reply = (
                    "⏸️ <b>已暂停接收电脑消息（保持静音）</b>\n\n"
                    "• 电脑端的普通回答不会发给手机，还您清静；\n"
                    "• 高危操作（命令行/改写文件）需要审批时，手机仍会正常弹窗；\n"
                    "• 您随时可以在手机上发文字指令，电脑端依然会照常执行；\n"
                    "• 点击下方 <b>【▶️ 恢复接收消息】</b> 即可随时恢复。"
                )
                self.tg.send_message(chat_id, reply, reply_markup=get_bottom_keyboard(False), parse_mode="HTML")

            elif text in ("/resume", "/start_sync", "/sync_on", "▶️ 恢复接收消息", "恢复接收消息", "开始接收消息"):
                self.config["sync_messages"] = True
                save_config(self.config)
                reply = (
                    "▶️ <b>已恢复接收电脑消息！</b>\n\n"
                    "• 电脑端 Antigravity 产生的新回复将实时 1:1 推送到手机；\n"
                    "• 如需临时静音，点击下方 <b>【⏸️ 暂停接收消息】</b> 即可。"
                )
                self.tg.send_message(chat_id, reply, reply_markup=get_bottom_keyboard(True), parse_mode="HTML")

            elif text in ("/list", "📂 历史会话", "历史会话"):
                text_content, reply_markup = build_list_overview_payload(force_refresh=True)
                self.tg.send_message(chat_id, text_content, reply_markup=reply_markup, parse_mode="HTML")

            elif text in ("/new", "➕ 新建对话", "新建对话"):
                ok, reason = new_session()
                is_syncing = self.config.get("sync_messages", True)
                if ok:
                    self.tg.send_message(chat_id, "✨ <b>已在电脑上为您开启新对话！</b>\n您可以直接在此发送新任务指令。", reply_markup=get_bottom_keyboard(is_syncing), parse_mode="HTML")
                else:
                    self.tg.send_message(chat_id, f"⚠️ 新建失败: {reason}", reply_markup=get_bottom_keyboard(is_syncing))

            else:
                logger.info("收到来自 Telegram 的指令: %s", text)
                self.tg.send_message(chat_id, f"⚡️ <b>正在送入 Antigravity...</b>\n<i>{html.escape(text)}</i>", parse_mode="HTML")
                
                def do_inject(t):
                    ok, reason = send_prompt(t)
                    if ok:
                        is_sync = load_config().get("sync_messages", True)
                        mute_hint = "" if is_sync else "\n\n💡 <i>提示：当前消息接收处于【暂停】状态，电脑回复不会推送到手机。可随时点击下方【▶️ 恢复接收消息】。</i>"
                        self.tg.send_message(chat_id, f"🚀 <b>已成功提交！</b>\nAntigravity 已开始执行，请稍候...{mute_hint}", reply_markup=get_bottom_keyboard(is_sync), parse_mode="HTML")
                    else:
                        self.tg.send_message(chat_id, f"⚠️ 提交失败: {reason}")
                
                threading.Thread(target=do_inject, args=(text,), daemon=True).start()

        elif "callback_query" in update:
            cb = update["callback_query"]
            cb_id = cb.get("id")
            chat_id = cb.get("message", {}).get("chat", {}).get("id")
            msg_id = cb.get("message", {}).get("message_id")
            data = cb.get("data", "")
            user_display = cb.get("from", {}).get("first_name", "User")

            if chat_id not in self.allowed_chat_ids:
                self.tg.answer_callback_query(cb_id, text="❌ 未授权", show_alert=True)
                return

            # 会话切换回调
            if data.startswith("switch:"):
                target_cid = data.split(":", 1)[1]
                # 寻找会话真实标题
                title = target_cid[:8]
                for p in session_service.get_projects():
                    for it in p["items"]:
                        if it["id"] == target_cid:
                            title = it["title"]
                            break

                ok, reason = switch_session(target_cid)
                if ok:
                    self.tg.answer_callback_query(cb_id, text=f"已切换至: {title[:25]}")
                    self.tg.send_message(chat_id, f"🔀 <b>电脑界面已切换至会话</b>：\n【{html.escape(title)}】\n\n现在的指令将直接进入该会话！", parse_mode="HTML")
                else:
                    self.tg.answer_callback_query(cb_id, text="切换失败")
                return

            # 查看某个项目的全部会话（分页，格式 viewp:{page}|{project}）
            elif data.startswith("viewp:"):
                try:
                    page_str, proj_name = data.split(":", 1)[1].split("|", 1)
                    page = int(page_str)
                except ValueError:
                    self.tg.answer_callback_query(cb_id, text="参数异常")
                    return
                text_content, reply_markup = build_project_all_payload(proj_name, page=page)
                self.tg.edit_message_text(chat_id, msg_id, text_content, reply_markup=reply_markup, parse_mode="HTML")
                self.tg.answer_callback_query(cb_id)
                return

            # 返回工作区概览
            elif data == "action:overview":
                text_content, reply_markup = build_list_overview_payload(force_refresh=False)
                self.tg.edit_message_text(chat_id, msg_id, text_content, reply_markup=reply_markup, parse_mode="HTML")
                self.tg.answer_callback_query(cb_id)
                return

            # 刷新会话列表
            elif data == "action:refresh":
                text_content, reply_markup = build_list_overview_payload(force_refresh=True)
                self.tg.edit_message_text(chat_id, msg_id, text_content, reply_markup=reply_markup, parse_mode="HTML")
                self.tg.answer_callback_query(cb_id, text="已刷新最新会话列表")
                return

            # 新建对话
            elif data == "action:new":
                ok, reason = new_session()
                if ok:
                    self.tg.answer_callback_query(cb_id, text="已开启新对话")
                    self.tg.send_message(chat_id, "✨ <b>电脑界面已开启新对话！</b>", parse_mode="HTML")
                else:
                    self.tg.answer_callback_query(cb_id, text="新建失败")
                return

            # 暂停或恢复消息推送（卡片底部按键触发）
            elif data in ("action:sync_pause", "action:sync_resume"):
                new_state = (data == "action:sync_resume")
                self.config["sync_messages"] = new_state
                save_config(self.config)
                alert_text = "▶️ 已恢复接收电脑消息！" if new_state else "⏸️ 已暂停接收电脑消息（保持静音）"
                self.tg.answer_callback_query(cb_id, text=alert_text, show_alert=False)

                # 1. 原地刷新会话列表卡片，保持会话内容不丢失，仅切换底部开关按钮
                text_content, reply_markup = build_list_overview_payload(force_refresh=False)
                self.tg.edit_message_text(chat_id, msg_id, text_content, reply_markup=reply_markup, parse_mode="HTML")

                # 2. 同步更新手机屏幕下方的常驻大按键
                hint = (
                    "▶️ <b>已恢复接收消息</b>，后续回答将实时同步至手机。"
                    if new_state else
                    "⏸️ <b>已暂停接收消息</b>，电脑回答已静音，高危操作仍会提醒。"
                )
                self.tg.send_message(chat_id, hint, reply_markup=get_bottom_keyboard(new_state), parse_mode="HTML")
                return

            # 审批动作回调
            if ":" not in data:
                return
            action, req_id = data.split(":", 1)

            with self.lock:
                req = self.pending_requests.get(req_id)

            if not req:
                self.tg.answer_callback_query(cb_id, text="⚠️ 审批已完成或已超时")
                return

            if req["done"]:
                self.tg.answer_callback_query(cb_id, text="⚠️ 审批已处理")
                return

            decision = "allow" if action == "approve" else "deny"
            req["decision"] = decision
            req["reason"] = f"由用户 {user_display} 在 Telegram 审批" if decision == "allow" else f"由用户 {user_display} 在 Telegram 拒绝"
            req["done"] = True
            req["event"].set()

            action_text = "🟢 已批准 (Allowed)" if decision == "allow" else "🔴 已拒绝 (Denied)"
            self.tg.answer_callback_query(cb_id, text=f"您{action_text}此操作")

            orig_text = req.get("message_text", "")
            timestamp = datetime.now().strftime("%H:%M:%S")
            updated_text = (
                f"{orig_text}\n\n"
                "━━━━━━━━━━━━━━━\n"
                f"<b>审批结果</b>: {action_text}\n"
                f"<b>操作人</b>: {user_display}\n"
                f"<b>时间</b>: {timestamp}"
            )
            for m in req.get("sent_messages", []):
                self.tg.edit_message_text(m["chat_id"], m["msg_id"], updated_text, reply_markup=None, parse_mode="HTML")

    def request_approval(self, agent, tool, args, summary=None, timeout=None):
        timeout = timeout or self.timeout_seconds
        req_id = str(uuid.uuid4())[:8]

        if not self.bot_token or not self.allowed_chat_ids:
            return {"decision": "ask", "reason": "Telegram Bot 未配置"}

        cmd_info = ""
        if "CommandLine" in args:
            raw_cmd = str(args["CommandLine"])
            if len(raw_cmd) > 1000:
                raw_cmd = raw_cmd[:1000] + "...(截断)"
            cmd_info = f"<b>执行命令</b>:\n<pre><code>{html.escape(raw_cmd)}</code></pre>\n"
        elif "TargetFile" in args:
            tf = str(args["TargetFile"])
            cmd_info = f"<b>修改文件</b>: <code>{html.escape(tf)}</code>\n"

        cwd_str = str(args.get("Cwd", "-")) if "Cwd" in args else ""
        cwd_info = f"<b>工作目录</b>: <code>{html.escape(cwd_str)}</code>\n" if cwd_str else ""
        summary_info = f"<b>说明</b>: {html.escape(summary)}\n" if summary else ""

        text = (
            "🚨 <b>AI Agent 远程审批请求</b>\n\n"
            f"🤖 <b>来源</b>: <code>{html.escape(str(agent))}</code>\n"
            f"⚙️ <b>工具</b>: <code>{html.escape(str(tool))}</code>\n"
            f"{cwd_info}"
            f"{cmd_info}"
            f"{summary_info}"
            f"⏱️ <b>有效时间</b>: {timeout} 秒"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "🟢 允许 (Allow)", "callback_data": f"approve:{req_id}"},
                    {"text": "🔴 拒绝 (Deny)", "callback_data": f"deny:{req_id}"}
                ]
            ]
        }

        req = {
            "event": threading.Event(),
            "decision": None,
            "reason": "",
            "done": False,
            "message_text": text,
            "sent_messages": []
        }

        with self.lock:
            self.pending_requests[req_id] = req

        for chat_id in list(self.allowed_chat_ids):
            res = self.tg.send_message(chat_id, text, reply_markup=reply_markup, parse_mode="HTML")
            if res and "message_id" in res:
                req["sent_messages"].append({"chat_id": chat_id, "msg_id": res["message_id"]})

        finished = req["event"].wait(timeout)

        with self.lock:
            if not finished and not req["done"]:
                req["done"] = True
                req["decision"] = self.config.get("default_on_timeout", "ask")
                req["reason"] = "远程审批超时，已转交本地处理"
                timeout_text = (
                    f"{text}\n\n"
                    "━━━━━━━━━━━━━━━\n"
                    "⏳ <b>审批已超时</b>（转交桌面处理）"
                )
                for m in req.get("sent_messages", []):
                    self.tg.edit_message_text(m["chat_id"], m["msg_id"], timeout_text, reply_markup=None, parse_mode="HTML")

            decision = req["decision"]
            reason = req["reason"]
            self.pending_requests.pop(req_id, None)

        return {"decision": decision, "reason": reason}

gateway_instance = ApprovalGateway()

class HttpRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            status_data = {
                "status": "running",
                "bot_configured": bool(gateway_instance.bot_token),
                "bound_admins": list(gateway_instance.allowed_chat_ids),
                "port": gateway_instance.port
            }
            self.wfile.write(json.dumps(status_data, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/approve":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = {}

            agent = data.get("agent", "Antigravity")
            tool = data.get("tool", "unknown")
            args = data.get("args", {})
            summary = data.get("summary", "")
            timeout = data.get("timeout")

            result = gateway_instance.request_approval(agent, tool, args, summary=summary, timeout=timeout)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def main():
    logger.info("===========================================")
    logger.info("  Antigravity Telegram 1:1 对等镜像网关运行中")
    logger.info("===========================================")
    
    # 异步注册 Telegram 官方菜单命令（避免因代理网络延迟阻塞 HTTP 端口监听）
    threading.Thread(target=setup_bot_commands, args=(gateway_instance.tg,), daemon=True).start()

    t_tg = threading.Thread(target=gateway_instance.poll_telegram_loop, daemon=True)
    t_tg.start()

    t_sync = threading.Thread(target=gateway_instance.watch_transcript_loop, daemon=True)
    t_sync.start()

    server_address = ("127.0.0.1", gateway_instance.port)
    # 多线程模式：多个审批请求可并行挂起等待，/status 不再被阻塞
    httpd = ThreadingHTTPServer(server_address, HttpRequestHandler)
    httpd.daemon_threads = True
    logger.info("本地审批 HTTP 接口就绪: http://127.0.0.1:%d/approve", gateway_instance.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("网关正在停止...")

if __name__ == "__main__":
    main()
