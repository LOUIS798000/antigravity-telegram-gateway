# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import urllib.request
import websockets
import time

logger = logging.getLogger("SessionService")

_cached_projects = []
_last_fetch_time = 0
CACHE_TTL = 3  # 缓存 3 秒

from cdp_injector import find_active_tab

async def _async_fetch_projects():
    ws_url = find_active_tab()
    if not ws_url:
        logger.error("未找到 Antigravity 调试连接")
        return []

    async with websockets.connect(ws_url) as ws:
        js = """
        (async () => {
            // 点击侧边栏展开所有“查看全部”
            const viewAllButtons = Array.from(document.querySelectorAll('button')).filter(b => {
                const t = b.innerText ? b.innerText.trim() : '';
                return (t.includes('查看全部') || t.includes('View all')) && b.getBoundingClientRect().left < 400;
            });
            for (const b of viewAllButtons) {
                try { b.click(); } catch(e) {}
            }
            if (viewAllButtons.length > 0) {
                // 等待侧边栏渲染稳定（链接数量连续两轮不变或超时），避免读到不完整列表
                let last = -1, stable = 0;
                for (let i = 0; i < 12 && stable < 2; i++) {
                    await new Promise(r => setTimeout(r, 100));
                    const n = document.querySelectorAll('a[href^="/c/"]').length;
                    if (n === last) { stable++; } else { stable = 0; last = n; }
                }
            }

            // 提取所有的侧边栏盘符标题 (例如 C:, d:, D:)
            const sidebarHeaders = Array.from(document.querySelectorAll('*')).filter(el => {
                const rect = el.getBoundingClientRect();
                if (rect.left < 0 || rect.left > 400 || rect.width === 0) return false;
                const t = el.innerText ? el.innerText.trim() : '';
                return /^[a-zA-Z]:$/.test(t) && el.children.length === 0;
            });
            sidebarHeaders.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);

            const allLinks = Array.from(document.querySelectorAll('a[href^="/c/"]')).filter(a => {
                const rect = a.getBoundingClientRect();
                return rect.left >= 0 && rect.left < 400 && rect.width > 0;
            });

            const results = [];
            for (let i = 0; i < sidebarHeaders.length; i++) {
                const header = sidebarHeaders[i];
                const nextHeader = sidebarHeaders[i + 1] || null;
                const topMin = header.getBoundingClientRect().top;
                const topMax = nextHeader ? nextHeader.getBoundingClientRect().top : 999999;

                const projLinks = allLinks.filter(a => {
                    const top = a.getBoundingClientRect().top;
                    return top >= topMin && top < topMax;
                });

                const items = [];
                for (const a of projLinks) {
                    let textNode = a;
                    while (textNode && (!textNode.innerText || textNode.innerText.trim() === '')) {
                        textNode = textNode.parentElement;
                    }
                    const lines = textNode ? textNode.innerText.split('\\n').map(s => s.trim()).filter(Boolean) : [];
                    const cid = a.getAttribute('href').replace('/c/', '').split('?')[0];
                    const title = lines[0] || cid.substring(0, 8);
                    const time = lines[1] || '';
                    if (!items.some(it => it.id === cid)) {
                        items.push({
                            id: cid,
                            title: title,
                            time: time
                        });
                    }
                }

                if (items.length > 0) {
                    results.push({
                        project: header.innerText.trim(),
                        count: items.length,
                        items: items
                    });
                }
            }
            return results;
        })()
        """
        msg = {"id": 102, "method": "Runtime.evaluate", "params": {"expression": js, "awaitPromise": True, "returnByValue": True}}
        await ws.send(json.dumps(msg))
        rep = await ws.recv()
        data = json.loads(rep)
        return data.get("result", {}).get("result", {}).get("value", [])

def get_projects(force_refresh=False):
    global _cached_projects, _last_fetch_time
    now = time.time()
    if not force_refresh and _cached_projects and (now - _last_fetch_time < CACHE_TTL):
        return _cached_projects
    try:
        projs = asyncio.run(_async_fetch_projects())
        # 去重合并同名项目（如果有重复盘符标题）
        merged = {}
        for p in projs:
            name = p["project"]
            if name not in merged:
                merged[name] = {"project": name, "items": []}
            for it in p["items"]:
                if not any(x["id"] == it["id"] for x in merged[name]["items"]):
                    merged[name]["items"].append(it)
            merged[name]["count"] = len(merged[name]["items"])
        _cached_projects = list(merged.values())
        _last_fetch_time = now
        return _cached_projects
    except Exception as e:
        logger.error("刷新项目会话列表失败: %s", e)
        return _cached_projects or []
