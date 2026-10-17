# -*- coding: utf-8 -*-
"""执行器：点按钮、复核弹窗、填数量、点确认。

## 一个动作的完整生命周期

    click_action()          清旧标记 → 打新标记 → CDP 真实点击
      ↓ 等 7s
    read_modal()            弹窗打开了吗
      ↓
    复核（opt 比当前 / join 比基准）→ 不达标就 cancel_modal 走人
      ↓
    限时抢购 → 填数量
      ↓
    TAG_CONFIRM → 点确认
      ↓ 等 6s
    receipt.classify()      读 snackbar 定成败

## click_action 的三层定位兜底

DOM 会在操作过程中重排。同一链接连续参加多个提案时，
第一次成功后那条卡片从「立即参与」变成「已预约/修改」并可能换位，
后面几条的 bi（box 索引）全部失效。

    第一层  TAG_ACTION        精确索引
    第二层  TAG_ACTION_SCAN   同一行内按卡片文本签名扫描
    第三层  relocate_action   由调用方 reload + 重展开后重新算索引（在 analyze.py）

第三层不在这个模块里，因为它需要重新分析页面——那是 analyze 的职责。
"""

import json
import time

from . import constants as C
from . import js_snippets as JS
from . import lt_common as L
from .modal_reader import (
    cancel_modal,
    read_modal,
    resolve_join_nets,
    resolve_opt_nets,
)
from .receipt import classify


def click_action(store, act, texts, attr):
    """清理旧标记 → 标记目标卡片里指定文案的按钮 → CDP 真实点击。

    返回 None 表示点击动作已发出；返回 dict 表示失败（带 detail）。

    ⚠️ 必须先 CLEAR_ATTR。不清的话 CDP 会命中文档里第一个残留标记元素，
       表现为「点了一个完全无关的地方」，且无异常、无报错。
    """
    L.exec_js(store, JS.CLEAR_ATTR % (attr, attr), timeout=60)

    t = L.exec_js(
        store,
        JS.TAG_ACTION % (json.dumps(list(texts), ensure_ascii=False),
                         act["gi"], act["ri"], act["bi"], attr),
        timeout=90,
    )

    if not (isinstance(t, dict) and t.get("ok")):
        # 第二层：索引失效 → 同行按卡片文本签名扫描
        sig = (act.get("camp") or "")[:C.CAMP_TRUNC_SIG]
        t2 = L.exec_js(
            store,
            JS.TAG_ACTION_SCAN % (json.dumps(list(texts), ensure_ascii=False),
                                  json.dumps(sig, ensure_ascii=False),
                                  act["gi"], act["ri"], attr),
            timeout=90,
        )
        if isinstance(t2, dict) and t2.get("ok"):
            act["bi_orig"] = act.get("bi")
            act["bi"] = t2.get("bi", act.get("bi"))
            act["btn_locate"] = t2.get("via")
            act["btn"] = t2.get("t")
            if t2.get("via") == "scan_first":
                print("      [info] 索引失效，按扫描定位到第%s个 box（非签名命中）"
                      % t2.get("bi"), flush=True)
            L.click(store, '[%s="1"]' % attr, timeout=120)
            return None

        err = t.get("err") if isinstance(t, dict) else t
        return {"result": "失败", "detail": "未找到按钮(%s)" % err}

    act["btn"] = t.get("t")
    L.click(store, '[%s="1"]' % attr, timeout=120)
    return None


def join_one(store, act):
    """执行一条动作（参加 / 优化），返回结果 dict。

    返回形如：
        {"result": "成功",    "snackbar": "完成！您的商品已加入促销活动", "qty": 10, "kind": "join"}
        {"result": "失败",    "detail": "弹窗未打开"}
        {"result": "跳过",    "detail": "新提案净收93.94不高于当前110.64，已取消"}
        {"result": "待核验",  "snackbar": None, "qty": 8, "kind": "join"}
    """
    kind = act.get("kind", "join")
    is_opt = kind == "opt"

    err = click_action(
        store, act,
        C.OPT_BTNS if is_opt else C.JOIN_BTNS,
        "data-ajopt" if is_opt else "data-ajjoin",
    )
    if err:
        return err

    time.sleep(C.DEFAULT_RUNTIME["modal_wait_seconds"])
    modal = read_modal(store)
    if not modal.get("open"):
        return {"result": "失败", "detail": "弹窗未打开"}

    act["qty"] = None

    if is_opt:
        verdict = _verify_opt(store, act, modal)
    else:
        verdict = _verify_join(store, act, modal)

    if verdict is not None:
        return verdict          # 已被取消，直接返回

    # ---- 限时抢购：数量处理 ----
    if modal.get("is_deal") and modal.get("has_input"):
        _fill_deal_units(store, act, modal)

    # ---- 点确认 ----
    cf = L.exec_js(store, JS.TAG_CONFIRM, timeout=90)
    if not cf.get("ok") or cf.get("disabled"):
        cancel_modal(store)
        return {
            "result": "失败",
            "detail": "确认不可用(数量/折扣被拦)" if cf.get("ok") else "无确认按钮",
        }

    L.click(store, '[data-ajconfirm="1"]', timeout=120)
    time.sleep(C.DEFAULT_RUNTIME["snackbar_wait_seconds"])

    sb = L.exec_js(store, JS.READ_SNACKBAR, timeout=60)
    sb_txt = (sb or {}).get("snackbar") or ""

    # 附带回读卡片状态（仅记录，不作为成败判据——索引可能移位）
    v = L.exec_js(store, JS.VERIFY_BOX % (act["gi"], act["ri"], act["bi"]), timeout=60)
    if isinstance(v, dict):
        act["box_btns"] = v.get("btns")
        act["scheduled"] = v.get("scheduled")

    result, detail = classify(sb_txt)
    out = {"result": result, "snackbar": sb.get("snackbar"),
           "qty": act.get("qty"), "kind": kind}
    if detail:
        out["detail"] = detail
    return out


def _verify_opt(store, act, modal):
    """优化折扣的弹窗复核。达标返回 None；不达标则取消并返回结果 dict。"""
    cur_net, new_net, src = resolve_opt_nets(store, act, modal)
    act["net_src"] = src
    act["cur_net"], act["new_net"] = cur_net, new_net

    if cur_net is None or new_net is None:
        cancel_modal(store)
        return {"result": "跳过",
                "detail": "弹窗净收读取失败(cells=%s / 当前%s / 新%s)，已取消"
                          % (act.get("net_cells"), cur_net, new_net)}

    if new_net <= cur_net:
        cancel_modal(store)
        return {"result": "跳过",
                "detail": "新提案净收%s不高于当前%s，已取消" % (new_net, cur_net)}

    # 通过 → 卡片读数替换成弹窗读数（更可信）
    act["net"], act["base"] = new_net, cur_net
    return None


def _verify_join(store, act, modal):
    """立即参与的弹窗复核。达标返回 None；不达标则取消并返回结果 dict。"""
    cur_net, new_net, src = resolve_join_nets(store, act, modal)
    base = act.get("base")

    if new_net is None:
        cancel_modal(store)
        return {"result": "跳过",
                "detail": "弹窗复核：净收款额读取失败(cells=%s)，已取消"
                          % (act.get("net_cells"),)}

    if cur_net is not None:
        # 弹窗给了两列 → 比「新 > 当前」
        act["cur_net"], act["new_net"] = cur_net, new_net
        if new_net <= cur_net:
            cancel_modal(store)
            return {"result": "跳过",
                    "detail": "弹窗复核：新提案净收%s不高于当前%s，已取消" % (new_net, cur_net)}
        mn = new_net
    else:
        # 只有一列（限时抢购的常态）→ 拿它跟基准比
        mn = new_net
        act["modal_net"] = mn

    # 无论走哪条路，最终都必须打得过基准（激活 ∪ 已预约 里的最低净收）
    if base is not None and mn <= base:
        cancel_modal(store)
        return {"result": "跳过",
                "detail": "弹窗复核：净收%s不高于基准%s，已取消" % (mn, base)}

    act["net"], act["net_src"] = mn, src
    return None


def _fill_deal_units(store, act, modal):
    """限时抢购的数量填写。

    读到仓库数量 → 填 N（有多少上多少）
    读不到     → **不动输入框**，保持平台默认值

    ⚠️ 早期实现读不到时兜底填 1。这是错的：
       · 1 个库存的促销对曝光毫无意义；
       · 平台默认值通常是平台算过的合理值。
       在信息不足时保持默认，是更安全的选择。
    """
    wh = modal.get("warehouse")
    if wh:
        fill = L.exec_js(store, JS.FILL_UNITS % wh, timeout=90)
        if not (isinstance(fill, dict) and fill.get("ok")):
            act["qty_fill_error"] = "数量填写失败"
            return
        time.sleep(2)
        m2 = read_modal(store)
        act["qty"] = m2.get("input_value")
    else:
        act["qty"] = modal.get("input_value")
        act["qty_default"] = True
        print("      [info] 仓库数量未读到 → 保持平台默认 %s" % act.get("qty"), flush=True)


def click_more_all(store, gi, n_rows):
    """把组内每一行的「查看更多促销」逐个点开（备选，默认不用）。

    ⚠️ 实测对子链接行点开前后 box 数不变（纯视觉折叠，DOM 本来就全）。
       只有聚合父行有懒渲染，而父行我们不看 → 默认关闭，仅在 --click-more 时启用。

    每次点击前必须清旧标记，否则 CDP 会命中文档里第一个残留标记元素（点错行）。
    """
    n = 0
    for ri in range(n_rows):
        L.exec_js(store, JS.CLEAR_ATTR % ("data-ajmore", "data-ajmore"), timeout=60)
        t = L.exec_js(store, JS.TAG_MORE_ROW % (gi, ri), timeout=90)
        if not (isinstance(t, dict) and t.get("ok")):
            continue
        L.click(store, '[data-ajmore="1"]', timeout=120)
        time.sleep(1.5)
        n += 1
    return n
