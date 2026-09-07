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
            const sc = document.querySelector('.relative.w-full.h-full.overflow-y-auto.overscroll-none.px-2') ||
                       Array.from(document.querySelectorAll('*')).find(el => {
                           const r = el.getBoundingClientRect();
                           return r.left >= 0 && r.left < 400 && el.scrollHeight > el.clientHeight && el.clientHeight > 200;
                       });

            // 点击展开所有“查看全部”/“View all”
            const viewAllButtons = Array.from(document.querySelectorAll('button')).filter(b => {
                const t = b.innerText ? b.innerText.trim() : '';
                return (t.includes('查看全部') || t.includes('View all')) && b.getBoundingClientRect().left < 400;
            });
            for (const b of viewAllButtons) {
                try { b.click(); } catch(e) {}
            }
            if (viewAllButtons.length > 0) {
                await new Promise(r => setTimeout(r, 200));
            }

            const origScrollTop = sc ? sc.scrollTop : 0;
            const projectMap = {}; // projName -> Map(cid -> item)

            const scanCurrent = () => {
                // 确保当前可见的项目卡片处于展开状态（例如折叠状态的 d: 盘）
                const currentCards = Array.from(document.querySelectorAll('button[data-project-card="true"]'));
                for (const c of currentCards) {
                    if (c.getAttribute('aria-expanded') === 'false') {
                        try { c.click(); } catch(e) {}
                    }
                }

                // 提取项目卡片名称及位置
                let headers = currentCards.map(c => ({
                    name: (c.innerText || '').split('\\n')[0].trim().toUpperCase() || 'WORKSPACE',
                    top: c.getBoundingClientRect().top
                })).sort((a, b) => a.top - b.top);

                // 兜底：若未匹配到卡片，正则匹配盘符
                if (headers.length === 0) {
                    headers = Array.from(document.querySelectorAll('span, div')).filter(el => {
                        const rect = el.getBoundingClientRect();
                        if (rect.left < 0 || rect.left > 400 || rect.width === 0) return false;
                        const t = el.innerText ? el.innerText.trim() : '';
                        return /^[a-zA-Z]:$/i.test(t);
                    }).map(el => ({
                        name: el.innerText.trim().toUpperCase(),
                        top: el.getBoundingClientRect().top
                    })).sort((a, b) => a.top - b.top);
                }

                if (headers.length === 0) {
                    headers = [{ name: 'DEFAULT', top: 0 }];
                }

                const links = Array.from(document.querySelectorAll('a[href^="/c/"]')).filter(a => {
                    const r = a.getBoundingClientRect();
                    return r.left >= 0 && r.left < 400 && r.width > 0;
                });

                for (let i = 0; i < headers.length; i++) {
                    const h = headers[i];
                    const nextH = headers[i + 1] || null;
                    const topMin = h.top;
                    const topMax = nextH ? nextH.top : 999999;
                    if (!projectMap[h.name]) projectMap[h.name] = new Map();

                    const matched = links.filter(a => {
                        const t = a.getBoundingClientRect().top;
                        return t >= topMin && t < topMax;
                    });
                    for (const a of matched) {
                        const cid = a.getAttribute('href').replace('/c/', '').split('?')[0];
                        let tn = a;
                        while (tn && (!tn.innerText || tn.innerText.trim() === '')) tn = tn.parentElement;
                        const lines = tn ? tn.innerText.split('\\n').map(s => s.trim()).filter(Boolean) : [];
                        const title = lines[0] || cid.substring(0, 8);
                        const time = lines[1] || '';
                        projectMap[h.name].set(cid, { id: cid, title, time });
                    }
                }
            };

            // 阶段一：滚动到顶部扫描（捕获 C: 盘及上方项目）
            if (sc) {
                sc.scrollTop = 0;
                sc.dispatchEvent(new Event('scroll', { bubbles: true }));
            }
            await new Promise(r => setTimeout(r, 350));
            scanCurrent();

            // 阶段二：若存在长页面，滚动到中部扫描（捕获中间项目）
            if (sc && sc.scrollHeight > sc.clientHeight) {
                sc.scrollTop = Math.floor(sc.scrollHeight / 2);
                sc.dispatchEvent(new Event('scroll', { bubbles: true }));
                await new Promise(r => setTimeout(r, 350));
                scanCurrent();

                // 阶段三：滚动到底部扫描（捕获 D: 盘及尾部项目）
                sc.scrollTop = sc.scrollHeight;
                sc.dispatchEvent(new Event('scroll', { bubbles: true }));
                await new Promise(r => setTimeout(r, 350));
                scanCurrent();
            }

            // 恢复原始滚动位置，避免影响电脑端用户
            if (sc) {
                sc.scrollTop = origScrollTop;
                sc.dispatchEvent(new Event('scroll', { bubbles: true }));
            }

            // 组装最终结果
            const results = [];
            for (const [proj, map] of Object.entries(projectMap)) {
                if (map.size > 0) {
                    results.push({
                        project: proj,
                        count: map.size,
                        items: Array.from(map.values())
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

def _merge_projects(projs):
    global _cached_projects, _last_fetch_time
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
    _last_fetch_time = time.time()
    return _cached_projects

async def async_get_projects(force_refresh=False):
    global _cached_projects, _last_fetch_time
    now = time.time()
    if not force_refresh and _cached_projects and (now - _last_fetch_time < CACHE_TTL):
        return _cached_projects
    try:
        projs = await _async_fetch_projects()
        return _merge_projects(projs)
    except Exception as e:
        logger.error("异步刷新项目会话列表失败: %s", e)
        return _cached_projects or []

def get_projects(force_refresh=False):
    global _cached_projects, _last_fetch_time
    now = time.time()
    if not force_refresh and _cached_projects and (now - _last_fetch_time < CACHE_TTL):
        return _cached_projects
    try:
        # 判断当前线程是否已有正在运行的事件循环
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                projs = pool.submit(asyncio.run, _async_fetch_projects()).result()
        else:
            projs = asyncio.run(_async_fetch_projects())

        return _merge_projects(projs)
    except Exception as e:
        logger.error("刷新项目会话列表失败: %s", e)
        return _cached_projects or []
