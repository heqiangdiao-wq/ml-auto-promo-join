# -*- coding: utf-8 -*-
"""MLB → SKU 映射。

## 为什么要这个东西

活动卡片上有些行不带 `SKU XXX #<id>` 文本，只有一个 MLB 编号。汇报时
「MLB#5094114567」对运营毫无意义——他要知道是哪个 SKU。

映射来源是商品抓取产出的 `sku_capture_v4/*_all.json`（每店一个文件），
里面是 `[{mlb_id, seller_sku, ...}]` 的列表。

## 三个设计选择

1. **缺失不报错、返回空串**：映射缺失是常态（新上架商品还没抓过），
   此时退回汇报标识 `MLB#<id> <标题>`，不影响主流程。
2. **同名 MLB 后写覆盖**：多个抓取文件可能包含同一个 MLB，取后读到的那个。
3. **模块级缓存**：整个进程只扫一次盘。一轮跑 20~35 分钟，
   每个活动都去 glob 一遍文件会把 IO 打满。

## SKU 判定的坑

**不要用窄正则判定「这一行有没有 SKU」**。历史实现用过 `/^[A-Z]{2}-[0-9]/`，
结果 `GDSZAL300A`、`PET-BAG-56327-BL` 这类都漏掉了。
正确做法是看页面结构给出的字段（`rr["sku"]`），或按已知名单精确匹配。
"""

import glob
import json
import os

from .constants import BASE_DIR

_CACHE = None


def _capture_files():
    """商品抓取文件。目录不存在时返回空列表（不报错）。"""
    pat = os.path.join(BASE_DIR, "sku_capture_v4", "*_all.json")
    return sorted(glob.glob(pat))


def load_mlb_sku_map(force=False):
    """建 {mlb_id: seller_sku} 映射。空 sku 保留为空串。

    返回的 dict 是**新建的**，调用方随便改不影响缓存。
    """
    global _CACHE
    if _CACHE is not None and not force:
        return dict(_CACHE)

    m = {}
    for f in _capture_files():
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            # 抓取文件可能正在写入，读坏了跳过即可，不影响其它文件
            continue

        rows = d.get("rows", []) if isinstance(d, dict) else d
        if not isinstance(rows, list):
            continue

        for r in rows:
            if not isinstance(r, dict):
                continue
            mlb = r.get("mlb_id")
            if mlb:
                m[str(mlb)] = (r.get("seller_sku") or "").strip()

    _CACHE = m
    return dict(m)


def sku_of(mlb_id, mapping=None):
    """按 MLB 取 SKU；无映射返回空串。"""
    if not mlb_id:
        return ""
    m = mapping if mapping is not None else load_mlb_sku_map()
    return m.get(str(mlb_id), "")


def stats():
    """映射统计，用于启动时打印一行健康检查。"""
    m = load_mlb_sku_map()
    files = _capture_files()
    return {
        "files": len(files),
        "mlb_total": len(m),
        "with_sku": sum(1 for v in m.values() if v),
    }
