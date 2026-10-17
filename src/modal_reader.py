# -*- coding: utf-8 -*-
"""弹窗读取与取消。

## 这个模块存在的唯一理由

**「在拍平的文本里数第几个 R$」会读错。**

弹窗里同时存在多种金额：当前净收、新提案净收、折扣额、原价、运费、手续费减免额……
靠出现顺序取数，只要顺序与预期稍有不一致，或混进一个额外金额，就会读错。

而读错的后果不是报错，是**静默做出错误决策**：
把手续费减免额当成新提案净收，然后拿这个假数字去比基准，超出 → 点确认 → 真的参加了。

所以这里改用**按列结构化读取**（`READ_NET_CELLS`）：

```text
以「净收款额」这个文本叶子为锚
  → 取它父节点
  → 父节点的后续兄弟就是各列单元格（顺序 = 列顺序）
  → 每个单元格取其中**第一个** R$ 数字
```

## 弹窗的两种形态（实测）

| 触发来源 | 列数 class | 列的含义 |
|---|---|---|
| 优化折扣 | `--cols-3` | [当前, 新提案, 可选] |
| 立即参与（限时抢购） | `--cols-1` | **只有新提案一个值** |

`--cols-1` 是那个著名的陷阱：只有一列时，「当前」必须从别处取，
而且这一列的单元格常带注释尾巴：

```
cells = ['R$ 1.239,70包含 R$ 57,29 的销售佣金减免。']
```

直接 `norm_num(整串)` 会返回 None（遇到非法字符）。
如果改用「取前两个 R$」又会把 57,29（手续费减免额）当成新提案 → **完全读错**。
"""

import re

from . import js_snippets as JS
from . import lt_common as L
from .utils import first_money, norm_num


def read_modal(store):
    """读弹窗整体状态。未打开返回 {'open': False}。"""
    m = L.exec_js(store, JS.READ_MODAL, timeout=90)
    return m if isinstance(m, dict) else {"open": False}


def read_net_nums(store):
    """【主用】按「列」结构化读「净收款额」各列数值。

    返回 (nums, cells)：
        nums  = [float, ...]  按列顺序（当前 / 新提案 / 可选）
        cells = [str, ...]    原始单元格文本，用于日志与排障

    每个单元格取**其中第一个 R$ 数字**——因为单元格常带注释尾巴。
    """
    nc = L.exec_js(store, JS.READ_NET_CELLS, timeout=90)
    if not (isinstance(nc, dict) and nc.get("found")):
        return [], None

    cells = nc.get("cells") or []
    nums = []
    for x in cells:
        v = first_money(x)
        if v is not None:
            nums.append(v)
    return nums, cells


def read_net_cells(store):
    """优化折扣专用薄封装 → (当前净收, 新提案净收, 原始单元格)。

    列不足 2 视为读不到（优化折扣弹窗正常情况下是 cols-3，最少也该有 2 列）。
    """
    nums, cells = read_net_nums(store)
    if len(nums) < 2:
        return None, None, cells
    return nums[0], nums[1], cells


def read_modal_nets_flat(modal):
    """【兜底，非主用】在拍平文本里「取『净收款额』后前两个 R$ 数字」。

    ⚠️ 这种方式认的是**出现顺序**不是标签。只要弹窗里混进别的金额
    （折扣额 / 原价 / 运费 / 手续费 / 批发价阶梯…）或顺序与预期相反就会读错。

    实测反例：PB-20K45-SKI 读出「新 = 3,98」，明显是手续费减免额。

    只在结构化读取完全失败、但弹窗确实有「净收款额」字样时使用，
    且只能安全地取**第一个**数（第一个数是净收的可能性最高）。
    第二个数绝不可信。
    """
    txt = modal.get("txt") or ""
    i = txt.find("净收款额")
    seg = txt[i:i + 240] if i >= 0 else txt
    nums = [norm_num(x) for x in re.findall(r"R\$\s*([\d.,]+)", seg)]
    nums = [n for n in nums if n is not None]
    return (nums[0] if len(nums) > 0 else None), (nums[1] if len(nums) > 1 else None)


def cancel_modal(store):
    """点弹窗里的「取消」。

    所有「读数不达标 / 读不到」的分支都必须走这里。
    **宁可放弃一次机会，也不要在信息不完整时点确认。**
    """
    cx = L.exec_js(store, JS.TAG_CANCEL, timeout=60)
    if isinstance(cx, dict) and cx.get("ok"):
        L.click(store, '[data-ajcancel="1"]', timeout=90)
        return True
    return False


def resolve_opt_nets(store, act, modal):
    """解析「优化折扣」弹窗的 (当前净收, 新提案净收)。

    act 需要：base（基准）、net（卡片上显示的该档净收）
    modal 需要：txt（用于判断有没有「净收款额」）

    返回 (cur_net, new_net, src)

    三条路径，按可靠性递减：

      structured_pair   弹窗给了 ≥2 列 → 直接用 [当前, 新提案]
      structured_single 只给 1 列 → 这一列是新提案；「当前」取该卡片自己显示的净收
      flat_fallback     结构化全失败但弹窗有「净收款额」→ 取第一个 R$ 当新提案
      none              什么都没有

    ⚠️ structured_single 里「当前」为什么取 act["net"] 而不是 base：

        base 现在是 min(激活 ∪ 已预约)。如果已预约那条更低，base 会被压到
        该促销档当前值之下 → 变成「允许把这个档位降级」，方向反了。

        而卡片上这张 opt box 自己显示的净收，实测就等于弹窗里的当前列
        （10 店实测：box 净收 == 弹窗 col1）。所以用它才是对的。
    """
    nums, cells = read_net_nums(store)
    act["net_cells"] = cells

    base = act.get("base")
    box_cur = act.get("net")

    if len(nums) >= 2:
        return nums[0], nums[1], "structured_pair"

    if nums:
        cur = box_cur if box_cur is not None else base
        return cur, nums[0], "structured_single"

    if "净收款额" in (modal.get("txt") or ""):
        cur = box_cur if box_cur is not None else base
        return cur, read_modal_nets_flat(modal)[0], "flat_fallback"

    return None, None, "none"


def resolve_join_nets(store, act, modal):
    """解析「立即参与」弹窗的 (当前净收, 新提案净收)。

    限时抢购弹窗实测是 `--cols-1`（只有新提案一个值，没有「当前」列）。
    若弹窗意外给了两列，则比「新 > 当前」；否则只取新提案值，由调用方跟 base 比。

    返回 (cur_net_or_None, new_net, src)
    """
    nums, cells = read_net_nums(store)
    act["net_cells"] = cells

    if len(nums) >= 2:
        return nums[0], nums[1], "modal_pair"

    if nums:
        return None, nums[0], "modal_single"

    if "净收款额" in (modal.get("txt") or ""):
        return None, read_modal_nets_flat(modal)[0], "flat_fallback"

    return None, None, "none"
