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

            // 1. 自动展开所有折叠状态的项目（例如折叠状态的 d: 盘）
            const projectBtns = Array.from(document.querySelectorAll('button[data-project-card="true"]'));
            for (const pb of projectBtns) {
                if (pb.getAttribute('aria-expanded') === 'false') {
                    try { pb.click(); } catch(e) {}
                }
            }
            await new Promise(r => setTimeout(r, 350));

            // 2. 点击侧边栏展开所有“查看全部”/“View all”
            const viewAllButtons = Array.from(document.querySelectorAll('button')).filter(b => {
                const t = b.innerText ? b.innerText.trim() : '';
                return (t.includes('查看全部') || t.includes('View all')) && b.getBoundingClientRect().left < 400;
            });
            for (const b of viewAllButtons) {
                try { b.click(); } catch(e) {}
            }
            if (viewAllButtons.length > 0) {
                await new Promise(r => setTimeout(r, 350));
            }

            // 3. 提取所有侧边栏项目标题 (例如 C:, d:, D:)
            const getSidebarHeaders = () => {
                const allElements = Array.from(document.querySelectorAll('button[data-project-card="true"], span, div'));
                return allElements.filter(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.left < 0 || rect.left > 400 || rect.width === 0) return false;
                    const t = el.innerText ? el.innerText.trim() : '';
                    return /^[a-zA-Z]:$/i.test(t);
                }).map(el => ({
                    name: el.innerText.trim().toUpperCase(),
                    top: el.getBoundingClientRect().top
                })).sort((a, b) => a.top - b.top);
            };

            // 4. 虚拟滚动分段采集：顶部、中部、底部合并，解决超长列表虚拟裁剪导致看不到D盘的问题
            const projectMap = {}; // projName -> Map(cid -> item)

            const scanCurrentViewport = () => {
                const headers = getSidebarHeaders();
                const allLinks = Array.from(document.querySelectorAll('a[href^="/c/"]')).filter(a => {
                    const rect = a.getBoundingClientRect();
                    return rect.left >= 0 && rect.left < 400 && rect.width > 0;
                });

                for (let i = 0; i < headers.length; i++) {
                    const h = headers[i];
                    const nextH = headers[i + 1] || null;
                    const topMin = h.top;
                    const topMax = nextH ? nextH.top : 999999;

                    if (!projectMap[h.name]) projectMap[h.name] = new Map();

                    const matchedLinks = allLinks.filter(a => {
                        const t = a.getBoundingClientRect().top;
                        return t >= topMin && t < topMax;
                    });

                    for (const a of matchedLinks) {
                        const cid = a.getAttribute('href').replace('/c/', '').split('?')[0];
                        let textNode = a;
                        while (textNode && (!textNode.innerText || textNode.innerText.trim() === '')) {
                            textNode = textNode.parentElement;
                        }
                        const lines = textNode ? textNode.innerText.split('\\n').map(s => s.trim()).filter(Boolean) : [];
                        const title = lines[0] || cid.substring(0, 8);
                        const time = lines[1] || '';
                        projectMap[h.name].set(cid, { id: cid, title: title, time: time });
                    }
                }
            };

            // 阶段一：扫描顶部可见区域
            const origScrollTop = sc ? sc.scrollTop : 0;
            if (sc) sc.scrollTop = 0;
            await new Promise(r => setTimeout(r, 150));
            scanCurrentViewport();

            // 阶段二：滚动到中部扫描
            if (sc && sc.scrollHeight > sc.clientHeight) {
                sc.scrollTop = Math.floor(sc.scrollHeight / 2);
                await new Promise(r => setTimeout(r, 250));
                scanCurrentViewport();

                // 阶段三：滚动到底部扫描（捕获完整的 D 盘项目列表）
                sc.scrollTop = sc.scrollHeight;
                await new Promise(r => setTimeout(r, 250));
                scanCurrentViewport();

                // 恢复原始滚动位置，避免影响电脑端用户操作
                sc.scrollTop = origScrollTop;
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
