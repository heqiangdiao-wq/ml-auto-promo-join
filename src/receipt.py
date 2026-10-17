# -*- coding: utf-8 -*-
"""回执校验：snackbar 的三种语义。

## 为什么成败判据只能是 snackbar

点完确认按钮后，有很多「看起来成功了」的假象：

- 弹窗关闭了 → 可能是因为成功，也可能是因为平台拒绝了；
- 卡片状态变了 → 可能只是本地 DOM 更新，没落库；
- 回读卡片按钮 → 索引可能移位，读到的未必是刚操作的那张。

唯一可信的判据是**平台自己吐的 snackbar 回执**。

## 三种回执

| snackbar 文本 | 语义 | 记录为 |
|---|---|---|
| `完成！您的商品已加入促销活动` | 真落库 | **成功** |
| `完成！…已更新…` | 优化生效 | **成功** |
| `我们未能将您的发布添加到促销中。请重试` | 平台拒绝 | **失败** |
| 读不到（None / 空） | 无从判断 | **待核验** |

「待核验」不是失败。它是「动作发出去了，但平台没给回执」——
可能是 snackbar 显示时间太短没抓到，也可能是真的没落库。

**这类记录必须人工复核，不能自动重试**：重试可能造成重复参加。
"""

from .constants import SNACKBAR_FAIL, SNACKBAR_SUCCESS


def classify(snackbar_text):
    """把 snackbar 文本分类成 (result, detail)。

    result ∈ {"成功", "失败", "待核验"}
    detail 只在有额外信息时非空
    """
    txt = (snackbar_text or "").strip()

    if not txt:
        # 动作已发出（确认按钮点过了），但平台没吐回执。
        # 这是「待核验」而非「失败」——不能自动重试，否则可能重复参加。
        return "待核验", "点确认后无任何 snackbar"

    if any(k in txt for k in SNACKBAR_FAIL):
        return "失败", "平台回执：%s" % txt

    if any(k in txt for k in SNACKBAR_SUCCESS):
        return "成功", ""

    # 有回执但语义未知。归到待核验，并保留原文供人工判读。
    return "待核验", "未知回执：%s" % txt


def is_retryable(result):
    """这个结果值得在同一次加载里重试吗。

    只有「弹窗未打开」「未找到按钮」这类**动作没发出去**的失败才值得重试
    （reload + 重展开 + 重定位索引）。

    「待核验」绝不重试——动作已经发出去了，重试可能重复参加。
    """
    return result == "失败"


RETRYABLE_DETAILS = ("弹窗未打开", "未找到按钮")


def should_retry(result, detail):
    """失败是否因为「动作没发出去」。

    判定依据是 detail 里的关键词，而不是 result 本身——
    有些「失败」是平台拒绝（动作发出去了），那不能重试。
    """
    if not is_retryable(result):
        return False
    d = str(detail or "")
    return any(k in d for k in RETRYABLE_DETAILS)
