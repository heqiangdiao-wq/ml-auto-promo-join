# 07 · 弹窗净收读取：一个会「静默做错决定」的坑

## 为什么这个坑比别的坑更危险

大部分 bug 会报错、会中断、会在日志里留下痕迹。

这个 bug **不报错**。它会：

1. 从弹窗里读到一个错误的数字；
2. 拿这个数字跟基准比；
3. 因为数字偏高（实际上是手续费减免额），判定「划算」；
4. 点确认；
5. 平台回执「已加入」——**成功**。

于是你每天跑脚本，每天有若干条提案被错误地参加，而报表上全是「成功」。
**要等到月底对账发现净收少了一截，才会有人怀疑。**

---

## 弹窗里到底有多少个金额

优化折扣弹窗（`--cols-3`）的 DOM：

```html
<div class="sc-multi-column-charges sc-multi-column-charges--cols-3 …">
  <div class="sc-multi-column-charges__summary">净收款额 查看详情</div>
  <div>R$ 110,64</div>        <!-- 第1列 = 当前 -->
  <div>R$ 93,94</div>         <!-- 第2列 = 新提案 -->
  <div>R$ 85,15</div>         <!-- 第3列 = 可选 -->
</div>
```

限时抢购弹窗（`--cols-1`）：

```html
<div class="sc-multi-column-charges sc-multi-column-charges--cols-1 …">
  <div class="sc-multi-column-charges__summary">净收款额 查看详情</div>
  <div>R$ 1.239,70包含 R$ 57,29 的销售佣金减免。</div>   <!-- 只有新提案 -->
</div>
```

注意第二种：**一个单元格里有两个 R$ 数字**。1.239,70 是净收，57,29 是手续费减免额。

而弹窗文本里还散落着其它金额：折扣百分比、原价、活动价、运费……

---

## 三种读法，只有一种可靠

### 读法 A：在拍平文本里数第几个 R$（❌ 已降级为兜底）

```python
txt = modal["txt"]
i = txt.find("净收款额")
nums = re.findall(r"R\$\s*([\d.,]+)", txt[i:i+240])
new_net = norm_num(nums[0])      # 第一个
```

**错在哪**：认的是**出现顺序**，不是标签。只要：

- 弹窗里混进别的金额（折扣额 / 原价 / 运费 / 手续费）；
- 或者顺序与预期相反；

就会读错。实测反例：

> `PB-20K45-SKI` 读出「新 = 3,98」——3,98 明显是手续费减免额，不是净收。

更阴险的情况在 `--cols-1` 里：单元格是
`R$ 1.239,70包含 R$ 57,29 的销售佣金减免。`，
如果取「前两个 R$」，第二个数是 57,29 → 判定新提案净收只有 57 块 → 拒绝掉一个
本来净收 1239 的好机会。**这次是漏判，方向相反但同样损失。**

### 读法 B：正则直接匹配整串（❌ 直接失败）

```python
norm_num("R$ 1.239,70包含 R$ 57,29 的销售佣金减免。")   # → None
```

因为 `norm_num` 遇到非数字字符会返回 None。所以**必须**先只取第一个 R$：

```python
first_money("R$ 1.239,70包含 R$ 57,29 的销售佣金减免。")   # → 1239.7
```

### 读法 C：按列结构化读取（✅ 主用）

```javascript
// 以「净收款额」这个文本叶子为锚
var sum = null;
m.querySelectorAll('*').forEach(function(e){
  if (sum) return;
  if (!e.children.length && (e.textContent||'').trim() === '净收款额') sum = e.parentElement;
});

// 取它父节点的后续兄弟 = 各列单元格（顺序 = 列顺序）
var wrap = sum.parentElement;
var cells = [];
Array.prototype.slice.call(wrap.children).forEach(function(ch){
  if (ch === sum) return;
  cells.push(ch.textContent.trim());
});
```

然后在 Python 侧对每个单元格取**第一个** R$：

```python
for x in cells:
    v = first_money(x)      # 只取第一个，忽略尾巴
    if v is not None:
        nums.append(v)
```

**为什么这个可靠**：它认的是**结构**（列位置），不是出现顺序。
弹窗里混进多少别的金额都不影响，因为只读「净收款额」这一行的兄弟节点。

锚点唯一性已验证：全弹窗「净收款额」只有 1 处（不是 visually-hidden 干扰）。

---

## `--cols-1` 的特殊处理：当前值从哪来

`--cols-3` 很好办：[当前, 新提案, 可选]，直接比「新 > 当前」。

`--cols-1` 只有新提案一个值。「当前是多少」必须从别处取。候选有三个：

| 候选 | 问题 |
|---|---|
| 基准 `base` | ❌ base 是 `min(激活 ∪ 已预约)`，可能低于这个档位本身的当前值 |
| 卡片上的净收 `act["net"]` | ✅ 实测等于弹窗的当前列 |
| 弹窗第一列 | ❌ 不存在这一列 |

### 为什么不能用 base

假设：

- 激活档净收 110
- 已预约档净收 95
- 你正在看的这张 opt 卡片属于**激活**那一档（当前 110）

按规则 2，`base = min(110, 95) = 95`。

如果用 base 当「当前」，判定条件是「新提案 > 95」。平台给一个 100 的提案 →
判定通过 → 确认。但实际上你把**激活档从 110 降到了 100**——这是降级，不是优化。

用卡片自己的净收（110）当「当前」，判定就是「100 > 110」→ 不通过 → 取消。**正确。**

### 实测依据

10 店实测：卡片上 opt box 显示的净收 == 弹窗 cols-1 里该档的当前值（例如
`ES-C16.5-SIY` 799,61 两处一致）。

所以规则是：

```python
if len(nums) >= 2:
    cur, new, src = nums[0], nums[1], "structured_pair"
elif nums:
    cur = box_cur if box_cur is not None else base     # ← 优先用卡片读数
    new = nums[0]
    src = "structured_single"
```

**box 净收读不到时才退回 base**（这是最后兜底，会让判定变宽松，有风险，
但比「直接取消」要好——因为至少有 base 这个下限保护）。

---

## 读不到怎么办

一律 **取消**。

```python
if cur_net is None or new_net is None:
    cancel_modal(store)
    return {"result": "跳过", "detail": "弹窗净收读取失败，已取消"}
```

宁可在信息不完整时放弃一次机会，也不要在读数不可信时点确认。

> 实战数据：8 轮运行里，`flat_fallback` 路径几乎没有被走到——
> 说明结构化读取的成功率足够高。`structured_single` 每次都会命中
> `PB-20K22-BLK` / `PB-20K45-SKI` 这两条，读数正确。

---

## 「立即参与」也要复核

初期实现认为：卡片层已经筛过「净收 > 基准」了，弹窗里直接确认就行。

**不对。** 卡片上写的净收和弹窗里最终的净收可能不一致（平台在弹窗里才给出最终结算明细）。

所以 `join` 也要在弹窗里再比一次：

```python
if len(nums) >= 2:
    if nums[1] <= nums[0]:
        cancel_modal()          # 弹窗给出当前列 → 比「新 > 当前」
        return ...
    mn = nums[1]
else:
    mn = nums[0]                # 只有一列 → 拿它跟基准比

if base is not None and mn <= base:
    cancel_modal()
    return ...
```

> 实战数据：这个二次复核机制是**有效的**。
> 2026-10-10 那轮拦下了 1 条（`NY-29223-RD` 的 9/10 月档，弹窗 30.09 < 基准 33.19）。
> 如果没有这道复核，它会以「成功」被记录下来，实际是亏的。
