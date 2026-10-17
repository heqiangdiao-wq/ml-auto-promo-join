# -*- coding: utf-8 -*-
"""页面探测：拿整页快照、读分页末页号、按标题定位组。

## dump_page 的重试逻辑为什么长这样

原始实现是「读到 0 个组就当没加载好，重试 10 次 × 4s」。看起来稳妥，实际埋了个坑：

分页逻辑探到**越界页**时（平台只有 2 页却请求了 page=3），平台会渲染空态页
「目前无法创建促销活动」。这个页面确实「没有组」，于是 dump_page 判定
「没加载好」，白等 40~60 秒才放弃。现象是浏览器停在空态页像卡死。

现在的处理刻意收窄了改动面：**正常路径一行没动**。

    第一次 exec_js 就拿到 groups → 立刻 return，page_meta() 根本不会被调用（零额外开销）
    只有「读到 0 个组」这条异常分支才多查一次 PAGE_META
      → 命中空态文案就立即返回（_empty=True），不再重试

`retries` 保持 10 不变，慢页面的容错能力不降级。
"""

import time

from . import js_snippets as JS
from . import lt_common as L
from .utils import norm_title


def page_meta(store):
    """页面元信息：{empty, last_page, items, n_groups}。读失败返回 {}。"""
    m = L.exec_js(store, JS.PAGE_META, timeout=60)
    return m if isinstance(m, dict) else {}


def dump_page(store, retries=10, interval=4):
    """Dump 当前页所有组。

    返回 {"groups": [...], "last_page": int|None, "_empty": bool(可选)}
    """
    d = None
    for i in range(retries):
        d = L.exec_js(store, JS.DUMP_PAGE, timeout=120)
        if isinstance(d, dict) and d.get("groups"):
            return d

        # 只在「读到 0 个组」这条异常分支多查一次，正常路径零开销。
        # 前 3 次就查：空态页通常第 1 次就命中，不必等到第 10 次。
        if i < 3:
            m = page_meta(store)
            if m.get("empty"):
                return {"groups": [], "_empty": True, "last_page": m.get("last_page")}

        time.sleep(interval)

    return d if isinstance(d, dict) else {"groups": []}


def group_at(store, gi):
    """取当前页面第 gi 个组的快照。"""
    d = dump_page(store)
    gs = d.get("groups", [])
    return gs[gi] if gi < len(gs) else None


def group_index_by_prefix(store, prefix, dumps=None):
    """按组标题前缀找当前页面里的组索引。

    为什么需要它：reload 之后组顺序会漂移（平台插入广告位、活动状态变化都会改变排序），
    展开前记下的 gi 在新页面上可能指向别的商品。所以每次 reload 后都要重新定位。

    匹配策略用「精确前缀优先，包含兜底」：
      · 精确 startswith → 直接返回
      · 否则收集包含匹配做候选，取第一个
    用包含是因为列表会重排/加广告标记，前缀可能漂移。
    """
    d = dumps if dumps is not None else dump_page(store)
    cands = []
    for g in d.get("groups", []):
        t = norm_title(g.get("title"))
        if t.startswith(prefix):
            return g.get("gi")
        if prefix and prefix in t:
            cands.append(g.get("gi"))
    return cands[0] if cands else None


def pagination_state(store, page, groups):
    """根据当前页判断分页状态。

    返回 (should_stop, reason, last_page)

    三个判断条件，顺序不能换：

    1. **空组** → 停。区分两种空：
       - `_empty=True`（平台空态文案）→ 说明是越界页，正常收手
       - 否则 → 页面没渲染出来，也收手（继续翻只会更糟）
    2. **已到末页** → 停。`page >= last_page` 时不请求下一页。
    3. **满页但末页号等于当前页** → 说明末页号读错了，后面还有内容。
       把 last_page 推后一页，避免提前收口。

    条件 3 是实践补丁：分页控件有时只渲染「上一页/下一页」附近的页码，
    读到的 max 可能小于真实末页。满页（≥20 组）是「后面还有」的硬证据。
    """
    d_groups = groups
    last_page = None

    if not d_groups:
        return True, "无组", last_page

    m = page_meta(store)
    if m.get("last_page"):
        last_page = int(m["last_page"])

    if last_page is not None and page >= last_page:
        return True, "已到末页 %d" % last_page, last_page

    if last_page is not None and last_page <= page and len(d_groups) >= 20:
        last_page = page + 1

    return False, "", last_page
