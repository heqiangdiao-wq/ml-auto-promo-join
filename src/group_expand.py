# -*- coding: utf-8 -*-
"""商品组展开。

## 这个模块只有一条铁律

> **同一次页面加载里，只有第一次展开点击有效。**

第二次开始点击一律不生效——行数恒为 1（看起来像没展开）。这不是 visibilityState
的问题（后台标签页冻结 rAF 是另一回事，这里页面是可见的）。

实测结论：不管怎么等、怎么重试，同一次加载的第二下就是无效。

### 所以唯一的解法是：每个需要展开的组**独占一次页面加载**

```python
for gi in groups_with_chevron:
    g2 = expand_fresh(sid, page_url, prefix)   # 内部会 reload
```

代价是 N 个组就要 reload N 次。一页 25 组里通常有 10~20 个带箭头，
每次 reload 等 13s → 单页展开开销 2~4 分钟。这是这个项目耗时的主要来源，
**但它是不可省的**。

## 为什么等待要 8 × 4s

展开后要轮询「子行是否出现」。早期用 3 次 × 3s，美客多一卡就误判展开失败，
日志里出现 `第1次展开 gi22 未出子行` 这种假告警，然后整组被跳过（白丢机会）。

8 × 4s = 32s 上限。绝大多数组在 4~8s 内出子行，32s 只是给极卡的时候留余量。
"""

import time

from . import js_snippets as JS
from . import lt_common as L
from .page_probe import dump_page, group_at, group_index_by_prefix


def expand_fresh(store, page_url, prefix, tries=3, poll_times=8, poll_interval=4):
    """重新加载页面 → 展开目标组 → 返回展开后的组快照。

    参数：
        store        数字 storeId
        page_url     当前页地址（含 ?page=N）
        prefix       组标题前缀（已 norm_title 归一，建议截断到 36 字）
        tries        reload 重试次数
        poll_times   展开后轮询次数
        poll_interval 每次轮询间隔秒数

    返回：展开后的组 dict（rows 长度 > 1），失败返回 None。

    为什么每次都要重新定位 gi：
        reload 后组顺序会漂移（平台插入广告位、活动状态变化都会影响排序），
        展开前记下的 gi 在新页面上可能指向别的商品。
        所以必须先 group_index_by_prefix 重新找。
    """
    for t in range(tries):
        L.visit(store, page_url, wait=13)

        gi = group_index_by_prefix(store, prefix)
        if gi is None:
            print("      [warn] reload 后找不到组「%s」，重试" % prefix[:24], flush=True)
            time.sleep(2)
            continue

        # 打标记 → CDP 真实点击（两步，不可合并）
        L.exec_js(store, JS.EXPAND_ONE % gi, timeout=90)
        L.click(store, '[data-ajchev="1"]', timeout=120)

        for _ in range(poll_times):
            time.sleep(poll_interval)
            g = group_at(store, gi)
            if g and len(g.get("rows") or []) > 1:
                # 子行出现了，但内容可能还在渲染。多等 1s 再取一次快照，
                # 避免读到「行有了、卡片还没挂上」的中间态。
                time.sleep(1)
                g2 = group_at(store, gi)
                return g2 or g

        print("      第%d次展开 gi%d 未出子行，重试" % (t + 1, gi), flush=True)

    return None


def needs_expand(group):
    """这个组是否必须展开。

    有箭头 → 必须展开（箭头意味着这个商品有多个变体子链接，卡片挂在子行上）。
    无箭头 → 是大链接，卡片直接挂在唯一行上，当前页数据已经够用。
    """
    return bool(group.get("has_chevron"))


def rows_to_process(group):
    """从组快照里挑出真正要处理的行。

    展开过的组（有箭头）→ **只保留有 SKU 的行**。
        无 SKU 的是聚合父行，它的净收是跨变体的区间值，不是一条可点的链接；
        对父行点按钮，平台根本不响应。
        不忽略它的话，报告里会塞满噪音，而且会占用去重键（父行 ident 为空，
        不同商品的父行可能算出同一个键）。

    未展开的组（无箭头）→ 全部行都处理（通常只有一行）。
    """
    rows = group.get("rows") or []
    if group.get("has_chevron"):
        return [r for r in rows if r.get("sku")]
    return rows
