# -*- coding: utf-8 -*-
"""编排：分店串行 → 分页 → 分组 → 判定 → 执行 → 落库。

## 主循环结构

    for store in stores:
        visit(promos_url)
        page = 0
        while page < MAX_PAGE:
            page += 1
            读分页控件拿末页号 → 到末页就 break
            groups = dump_page()
            1) 无箭头的组：直接处理（当前页数据够用）
            2) 有箭头的组：每个组独占一次页面加载后处理
        写结果 JSON（每店写完就 flush 一次，防止中途崩掉丢全部数据）

## 为什么分店串行而不是并行

两个店铺各占一个浏览器标签页，并行跑会互相抢标签页焦点
（CDP 操作是按 storeId 定位的，但 `page click` 依赖前台页面状态）。

实测并行会互相挤崩，串行稳定。代价是耗时叠加，这是可接受的。

## 掉登录的处理

「本店 0 条」有两种完全不同的原因：

    ① 今天确实没活动        → 正常
    ② 店铺掉登录了          → 需要人工在紫鸟重新登录

两者在日志上的表现一模一样（都是「第1页无组」），早期无法区分，
导致 2026-10-14 和 10-17 两次事故都被误判成「没活动」。

现在在「读到 0 个组」时强制做一次登录态检查，并明确打印出来。
"""

import argparse
import json
import os
import time

from . import constants as C
from . import lt_common as L
from . import page_probe as P
from .analyze import analyze, relocate_action
from .group_expand import expand_fresh, needs_expand
from .joiner import click_more_all, join_one
from .receipt import should_retry
from .sku_map import load_mlb_sku_map, stats as sku_stats
from .utils import dedup_key, norm_title

MAX_PAGE = 30          # 防御性上限，防止分页逻辑出错导致无限翻页


def log(msg=""):
    print(msg, flush=True)


def process_group(store, group, page_url, args, seen, results):
    """处理一个组：判定 + （非 dry 时）执行。"""
    actions, skips = analyze(group)

    for s in skips:
        key = dedup_key(store, s)
        if key in seen:
            continue
        seen.add(key)
        results.append({"store": store, "type": "跳过", **s})

    for a in actions:
        key = dedup_key(store, a)
        if key in seen:
            continue
        seen.add(key)

        if args.dry:
            results.append({"store": store, "type": "可参加", **a})
            continue

        log("    %s %s | 净收%s>%s" % (
            "优化" if a.get("kind") == "opt" else "参加",
            a["ident"], a["net"], a["base"]))

        r = join_one(store, a)

        # 动作没发出去（弹窗未打开 / 未找到按钮）→ reload + 重展开 + 重定位索引后重试。
        # 「待核验」不重试：动作已经发出去了，重试可能重复参加。
        for _ in range(2):
            if not should_retry(r.get("result"), r.get("detail")):
                break
            gg = expand_fresh(store, page_url, norm_title(group.get("title"))[:36], tries=1)
            if not gg:
                break
            a2 = relocate_action(gg, a)
            if a2:
                a = a2
            r = join_one(store, a)

        results.append({"store": store, "type": "参加", **a, **r})
        log("      -> %s %s" % (r["result"], r.get("detail") or r.get("snackbar") or ""))


def run_store(store, sid, cfg, args, all_results, seen):
    """跑一个店铺的全部分页。"""
    rt = cfg["runtime"]
    promos_url = cfg["promos_url"]

    log("==== 店铺 %s ====" % store)
    L.visit(sid, promos_url, wait=rt["page_wait_seconds"])

    page = 0
    last_page = None
    store_results = []

    while page < MAX_PAGE:
        if last_page is not None and page >= last_page:
            log("  已到末页 %d，结束分页（不再探越界页）" % last_page)
            break

        page += 1
        t0 = time.time()

        if page > 1:
            L.visit(sid, promos_url + "?page=%d" % page, wait=rt["page_wait_seconds"])

        data = P.dump_page(sid)
        groups = data.get("groups") or []

        if not groups:
            if data.get("_empty"):
                log("  第%d页为空态「目前无法创建促销活动」，结束分页（耗时%.1fs）"
                    % (page, time.time() - t0))
            else:
                # 🔴 这里必须区分「没活动」和「掉登录」。
                #    两者日志表现一模一样，但处理方式完全不同。
                is_login, h, t = L.check_login(sid)
                if is_login:
                    log("  🔴 第%d页无组 = 店铺 %s 掉登录！" % (page, store))
                    log("     当前地址：%s" % h)
                    log("     页面标题：%s" % t)
                    log("     → 需要在紫鸟客户端里人工重新登录该店铺，本次本店全部跳过")
                    store_results.append({
                        "store": store, "type": "异常", "ident": None,
                        "reason": "店铺掉登录（需人工重登）",
                        "detail": {"href": h, "title": t},
                    })
                else:
                    log("  第%d页无组（非登录态），结束分页" % page)
            break

        if last_page is None:
            m = P.page_meta(sid)
            if m.get("last_page"):
                last_page = int(m["last_page"])

        # 满页（约等于每页容量）说明后面还有 → 末页号可能读错，不许提前收口
        if last_page is not None and last_page <= page and len(groups) >= 20:
            last_page = page + 1

        log("  第%d页 %d 组（%.1fs，共%s页）" % (
            page, len(groups), time.time() - t0,
            last_page if last_page is not None else "?"))

        page_url = promos_url if page == 1 else promos_url + "?page=%d" % page

        # 1) 无箭头的组：当前页数据已经够用，直接处理
        for g in groups:
            if not needs_expand(g):
                process_group(store, g, page_url, args, seen, store_results)

        # 2) 有箭头的组：每个组独占一次页面加载（一次加载只能展开一次）
        for gi, g in enumerate(groups):
            if not needs_expand(g):
                continue
            prefix = norm_title(g.get("title"))[:36]
            log("    展开 gi%d | %s" % (gi, prefix[:30]))

            g2 = expand_fresh(
                sid, page_url, prefix,
                poll_times=rt["expand_poll_times"],
                poll_interval=rt["expand_poll_interval_seconds"],
            )
            if not g2:
                log("      [warn] gi%d 展开失败，本组跳过" % gi)
                store_results.append({
                    "store": store, "type": "异常", "ident": "组「%s」" % prefix[:22],
                    "reason": "展开失败（连续重试未出子行）",
                })
                continue

            if args.click_more:
                n = click_more_all(sid, gi, len(g2.get("rows") or []))
                log("      点开「查看更多促销」%d 处" % n)
                g3 = P.group_at(sid, gi)
                if g3:
                    g2 = g3

            process_group(store, g2, page_url, args, seen, store_results)

    n_act = sum(1 for x in store_results if x["type"] in ("可参加", "参加"))
    n_skip = sum(1 for x in store_results if x["type"] == "跳过")
    n_exc = sum(1 for x in store_results if x["type"] == "异常")
    log("  %s 合计：可参加/参加 %d，跳过 %d，异常 %d" % (store, n_act, n_skip, n_exc))

    all_results.extend(store_results)


def summarize(results):
    """按店铺 × 结果类型汇总。"""
    from collections import Counter, defaultdict

    per = defaultdict(Counter)
    for r in results:
        per[r["store"]][r["type"]] += 1

    out = {}
    for store, c in per.items():
        participated = [r for r in results if r["store"] == store and r["type"] == "参加"]
        res_counter = Counter(r.get("result") for r in participated)
        reasons = Counter(
            r.get("reason") for r in results
            if r["store"] == store and r["type"] == "跳过" and r.get("reason")
        )
        out[store] = {
            "可参加/参加": c.get("可参加", 0) + c.get("参加", 0),
            "跳过": c.get("跳过", 0),
            "异常": c.get("异常", 0),
            "成功": res_counter.get("成功", 0),
            "失败": res_counter.get("失败", 0),
            "待核验": res_counter.get("待核验", 0),
            "净收不达标取消": sum(1 for r in participated if r.get("result") == "跳过"),
            "跳过原因Top": dict(reasons.most_common(5)),
        }
    return out


def backup_previous_result():
    """把上一轮结果备份成带时间戳的文件。

    🔴 这条纪律的由来：2026-10-14 曾用 `--dry` 做排查，而 **--dry 同样会覆盖结果 JSON**；
       随后又把「跑前备份」当成上一轮文件拷回去（实际是更早的文件）→ 本轮结果彻底丢失。

    所以：
      ① 备份必须在跑之前做，且文件名带「上一轮内部的 ts」，不是当前时间；
      ② 绝不拿 --dry 做复核。
    """
    if not os.path.exists(C.RESULT_PATH):
        return None
    try:
        with open(C.RESULT_PATH, encoding="utf-8") as f:
            old = json.load(f)
        old_ts = str(old.get("ts") or "unknown").replace(":", "").replace(" ", "_")
    except Exception:
        old_ts = "corrupt"
    dst = C.RESULT_PATH.replace(".json", "_%s.bak.json" % old_ts)
    if os.path.exists(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(C.RESULT_PATH, "rb") as src, open(dst, "wb") as out:
        out.write(src.read())
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(description="美客多推广中心自动报活动")
    ap.add_argument("--dry", action="store_true",
                    help="只扫描判定，不点参加按钮（⚠️ 仍会覆盖结果 JSON）")
    ap.add_argument("--store", default=None,
                    help="只跑指定店铺，值必须是 config 里 stores 的 key")
    ap.add_argument("--click-more", action="store_true",
                    help="额外逐个点开「查看更多促销」（实测对子行无新增，默认关闭）")
    args = ap.parse_args(argv)

    cfg = C.load_config()
    stores = list(cfg["stores"].keys())

    if args.store:
        if args.store not in cfg["stores"]:
            raise SystemExit("[CONFIG_INVALID] --store %r 不在配置里。可选：%s"
                             % (args.store, ", ".join(stores)))
        stores = [args.store]

    # 启动前的健康检查
    st = sku_stats()
    log("MLB→SKU 映射：%d 个文件 / %d 个 MLB / %d 个带 SKU"
        % (st["files"], st["mlb_total"], st["with_sku"]))
    log("店铺：%s" % ", ".join(stores))
    log("模式：%s" % ("DRY（只判定，不点按钮）" if args.dry else "真实参加"))
    log("")

    bak = backup_previous_result()
    if bak:
        log("上一轮结果已备份 → %s" % os.path.basename(bak))

    t_start = time.time()
    all_results, seen = [], set()

    for store in stores:
        run_store(store, cfg["stores"][store], cfg, args, all_results, seen)

    os.makedirs(os.path.dirname(C.RESULT_PATH), exist_ok=True)
    payload = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry": args.dry,
        "stores": stores,
        "elapsed_seconds": round(time.time() - t_start, 1),
        "summary": summarize(all_results),
        "results": all_results,
    }
    with open(C.RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    log("")
    log("结果已写 %s" % C.RESULT_PATH)
    log("耗时 %.0f 秒" % payload["elapsed_seconds"])
    for store, s in payload["summary"].items():
        log("  %s：参与位 %d（成功 %d / 失败 %d / 待核验 %d / 取消 %d），跳过 %d，异常 %d"
            % (store, s["可参加/参加"], s["成功"], s["失败"], s["待核验"],
               s["净收不达标取消"], s["跳过"], s["异常"]))


if __name__ == "__main__":
    main()
