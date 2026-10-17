# -*- coding: utf-8 -*-
"""决策引擎：给定一个商品组的快照，算出「该做哪些动作」和「为什么跳过」。

这是整个项目**业务规则最密集**、也最不该随便动的地方。
改动前请先读 docs/06_Decision_Rules.md。

## 一条链接的判定流程

    1. 逐行（子链接）处理
       ├─ 取 SKU（页面有就用页面的；没有就用 MLB→SKU 映射；再没有就用标题）
       ├─ 无「激活」在跑 → 整行跳过          【硬门槛】
       └─ 有激活 → 算基准，逐卡片判断
            ├─ 无绿字（手续费减免）        → 跳过
            ├─ 立即参与 且 净收 ≤ 基准     → 跳过
            └─ 否则 → 进 actions 队列

## 两个最容易被改错的点

### ① 基准池 = 「激活」∪「已预约」的最小值

不是只看激活。已预约也是**已经占住的档位**，只要打得过其中最差的那条，
换上去就是净赚。只取激活会白丢机会。

但「必须至少有一条激活」这个门槛**不受影响**——基准池扩大不等于门槛放松。

### ② 「优化折扣」在卡片层不做净收预筛

`kind == "opt"` 的提案，即使卡片上的净收看起来不划算，也要进队列。
因为优化的真实对比发生在**弹窗里**：「当前档 vs 新提案」。
卡片上显示的净收未必等于弹窗里的「当前」列。

所以 opt 的净收比较全部下沉到 joiner 层的弹窗复核。
"""

from . import constants as C
from .sku_map import load_mlb_sku_map
from .utils import has_green, norm_title, parse_net, title_of


def analyze(group, mlb_sku=None):
    """分析一个商品组，返回 (actions, skips)。

    actions: 待执行的动作列表，每条带 gi/ri/bi 索引
    skips:   被跳过的提案及原因，用于报告与复盘

    两边的记录都带 ident（SKU 或 MLB#id 标题），供去重与汇报使用。
    """
    actions, skips = [], []
    has_chev = group.get("has_chevron")
    rows = group.get("rows") or []

    if has_chev:
        # 展开过的组：只处理有 SKU 的真实子链接行；无 SKU 的聚合父行一律忽略
        # （父行是跨变体区间值，点它的按钮平台也不响应 —— 实测）
        link_rows = [r for r in rows if r.get("sku")]
        if not link_rows:
            print("      [warn] gi%s 展开后无 SKU 子链接行（聚合父行已忽略）"
                  % group.get("gi"), flush=True)
            skips.append({
                "sku": None, "mlb": None,
                "ident": "组「%s」" % norm_title(group.get("title"))[:22],
                "reason": "有箭头但无 SKU 子链接行（父行忽略）",
                "net": None, "camp": None,
            })
            return actions, skips
    else:
        link_rows = rows

    mapping = mlb_sku if mlb_sku is not None else load_mlb_sku_map()

    for rr in link_rows:
        sku = rr.get("sku")
        mlb = rr.get("mlb")

        # 大链接没有 SKU 时，用 MLB→SKU 映射补；再补不到就用标题兜底
        if not sku and mlb:
            sku = mapping.get(str(mlb), "")

        ident = sku or (
            "MLB#%s %s" % (mlb, title_of(rr.get("head"))) if mlb else title_of(rr.get("head"))
        )

        camps = rr.get("campaigns") or []

        # ---- 规则 1：硬门槛 ----
        # 判据**只看卡片文本含「激活」**，不看按钮文案。
        # 平台会把按钮从「停止参与」换成别的措辞，用按钮文案判断会在改版当天大量误判。
        activated = [c for c in camps if c.get("activated")]
        if not activated:
            skips.append({"sku": sku, "mlb": mlb, "ident": ident,
                          "reason": "无激活在跑活动", "net": None, "camp": None})
            continue

        # ---- 规则 2：基准 = 激活 ∪ 已预约 的最小净收 ----
        pool = [c for c in camps if c.get("activated") or c.get("scheduled")]
        base_pool = [("激活" if c.get("activated") else "已预约", parse_net(c.get("txt")))
                     for c in pool]
        base_net = min([n for _, n in base_pool if n is not None], default=None)

        for c in camps:
            btns = c.get("btns") or []
            is_new = any(b in btns for b in C.JOIN_BTNS)
            is_opt = any(b in btns for b in C.OPT_BTNS)
            # 只认「立即参与」会整条漏掉「优化折扣」入口
            if not (is_new or is_opt):
                continue

            kind = "join" if is_new else "opt"
            net = parse_net(c.get("txt"))

            if base_net is None:
                skips.append({"sku": sku, "mlb": mlb, "ident": ident,
                              "reason": "基准净收未解析(基线缺失)", "net": net,
                              "base_pool": base_pool, "camp": (c.get("txt") or "")[:C.CAMP_TRUNC]})
                continue

            # ---- 规则 3：绿字对 join 和 opt 都生效 ----
            if not has_green(c.get("txt")):
                skips.append({"sku": sku, "mlb": mlb, "ident": ident,
                              "reason": "无绿字", "net": net,
                              "base_pool": base_pool, "camp": (c.get("txt") or "")[:C.CAMP_TRUNC]})
                continue

            # ---- join：卡片层先做一次净收预筛 ----
            if is_new and (net is None or net <= base_net):
                skips.append({"sku": sku, "mlb": mlb, "ident": ident,
                              "reason": "净收不高于基准(基线%s)" % base_net,
                              "net": net, "base_pool": base_pool,
                              "camp": (c.get("txt") or "")[:C.CAMP_TRUNC]})
                continue

            # ---- opt：不做卡片层预筛，真实对比下沉到弹窗 ----
            actions.append({
                "sku": sku, "mlb": mlb, "ident": ident, "kind": kind,
                "net": net, "base": base_net, "base_pool": base_pool,
                "camp": (c.get("txt") or "")[:C.CAMP_TRUNC],
                "gi": group.get("gi"), "ri": rr.get("ri"), "bi": c.get("bi"),
            })

    return actions, skips


def relocate_action(group, act):
    """重载 + 重展开后，用「ident + camp 前 60 字」在新快照里找回同一条动作。

    为什么需要：join_one 失败重试时会 reload 重展开，此时 gi/ri/bi 全部失效。
    必须在新快照里重新算出这条动作的索引。

    匹配分两档：
      1. ident + camp 签名（严格）—— 最可靠
      2. ident + kind（宽松）—— camp 文案随平台刷新会变（比如日期区间更新），
         签名对不上时退到这一档

    返回新动作 dict，找不到返回 None。
    """
    acts, _ = analyze(group)
    sig = (act.get("camp") or "")[:C.CAMP_TRUNC_SIG]

    for a2 in acts:
        if a2.get("ident") == act.get("ident") and (a2.get("camp") or "")[:C.CAMP_TRUNC_SIG] == sig:
            return a2

    for a2 in acts:
        if a2.get("ident") == act.get("ident") and a2.get("kind") == act.get("kind"):
            return a2

    return None
