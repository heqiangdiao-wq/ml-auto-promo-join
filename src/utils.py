# -*- coding: utf-8 -*-
"""纯函数工具：金额解析、绿字检测、标题归一。

这一层没有副作用、没有 IO、没有网络。**它是整个项目唯一能被单独单元测试的部分**，
所以边界必须干净：输入字符串，输出数字 / 布尔 / 字符串。

## 金额解析的两个坑

### 坑 1：`rstrip(".0")` 是按字符集剥离

```python
"10".rstrip(".0")   # → "1"     😱
"100".rstrip(".0")  # → "1"     😱
```
`rstrip` 的参数是**字符集合**，不是后缀。它会一直剥到遇见不在集合里的字符为止。
所以 "100" 会被剥成 "1"。数值归一化必须用下面这个 `norm_num()`，不能用 rstrip。

### 坑 2：巴西格式是 `1.239,70`

小数点用逗号，千分位用点。和 Python 的浮点字面量正好相反。
必须先把 `.` 全删掉，再把 `,` 换成 `.`。
"""

import re

from .constants import CAMP_TRUNC, GREEN_PATTERN, NET_LABELS


def norm_num(s):
    """把巴西格式的金额字符串转成 float。

    >>> norm_num("R$ 1.239,70")
    1239.7
    >>> norm_num("R$ 33,19")
    33.19
    >>> norm_num("—")
    """
    if s is None:
        return None
    s = str(s).replace("R$", "").replace(" ", "").strip()
    if not s:
        return None
    if "," in s and "." in s:
        # 千分位点 + 小数逗号 → 标准浮点
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def first_money(text):
    """取文本里**第一个** R$ 金额。

    为什么要「第一个」：弹窗单元格常带注释尾巴，例如
        「R$ 1.239,70包含 R$ 57,29 的销售佣金减免。」
    这里 1.239,70 是净收，57,29 是手续费减免额。取第一个才对。
    如果拿整串去 norm_num，会因为遇到非法字符直接返回 None。
    """
    m = re.search(r"R\$\s*([\d.,]+)", text or "")
    return norm_num(m.group(1)) if m else None


def parse_net(box_txt):
    """从活动卡片的拍平文本里取「净收款额」。

    卡片文本里「净收款额」后面紧跟金额，中间可能夹极短的分隔文字。
    标签有多种写法（净收款额 / 净收款 / 净收），依次兜底。
    """
    box_txt = box_txt or ""
    for label in NET_LABELS:
        m = re.search(label + r"[^R]{0,25}?R\$\s*([\d.,]+)", box_txt)
        if m:
            return norm_num(m.group(1))
    return None


def has_green(box_txt):
    """卡片上是否有「手续费减免」绿字。

    实测语料里出现过三种表述：
        「我们降低 R$ 3,98 你每笔订单产生的手续费」
        「减免 R$ 5,00 手续费」
        「手续费减免」
    统一用一组关键词命中任意一个即可。
    """
    return bool(re.search(GREEN_PATTERN, box_txt or ""))


_TITLE_NOISE = re.compile(r"^(\+\d+|通过广告销售更多|竞选中通过广告销售更多|广告)+")


def norm_title(t):
    """去掉组标题前的噪音前缀，便于跨 reload 定位同一个组。

    实测噪音形态：
        「+9 Caixa Transporte Pet Gato Cachorro」
        「通过广告销售更多 Bolsa Pet Transporte」
        「竞选中通过广告销售更多 Mochila Esportiva」
    这些前缀是平台注入的角标，会随广告状态变化。带着它做前缀匹配，
    重载后一旦角标消失就找不到组了。

    用 while 循环反复剥，因为可能叠了两层（例如「+9 通过广告销售更多 X」）。
    """
    t = (t or "").strip()
    prev = None
    while prev != t:
        prev = t
        t = _TITLE_NOISE.sub("", t).strip()
    return t


def title_of(head_text):
    """行文本里 '#' 之前的部分当作商品标题（大链接无 SKU 时的兜底标识）。

    行文本形如：`SKU NY-29223-RD #5094114567 Mochila ...`
    '#' 之后是 MLB 编号，'#' 之前是 SKU + 标题。
    对无 SKU 的大链接行，这个函数给出可读的商品名。
    """
    i = head_text.find("#")
    return head_text[:i].strip() if i > 0 else (head_text or "").strip()


def dedup_key(store, rec):
    """统一「身份证号」：店铺 + 标识 + 活动卡片文字前 N 字。

    ⚠️ 这条规则看起来琐碎，但它修过一个会**静默丢单**的 bug。

    早期实现里，「跳过」用 (store, mlb, camp)、「参加」用 (store, mlb, bi)，
    两套键混放进同一个 seen 集合，后果有二：

      1. 同一条活动在两条路径上算不出同一个键 → 去重失效、报告自相矛盾；
      2. **无 MLB 的大链接行** mlb=None 且 bi 恒为 0 → 不同商品算出同一个键
         → 第二个被当成「重复」直接丢弃，**根本不处理，且不报错**。

    现在统一成一套键，并用 ident（SKU / MLB#id 标题）兜底空 MLB。
    截断长度必须全项目一致（CAMP_TRUNC），否则同一条活动在跳过/参加
    两条路径上算出的键又会不一样。
    """
    return (
        store,
        rec.get("ident") or rec.get("mlb") or "",
        (rec.get("camp") or "")[:CAMP_TRUNC],
    )
