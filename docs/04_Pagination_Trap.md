# 04 · 分页陷阱：为什么会去请求一个不存在的第 3 页

## 现象

脚本跑着跑着，浏览器停在一个空页面，屏幕上写着「目前无法创建促销活动」，
然后什么都不发生，等 40～60 秒才继续。

日志里看起来一切正常：

```
  第1页 25 组（3.2s，共?页）
  第2页 3 组（2.8s，共?页）
  第3页无组，结束分页
```

那 40～60 秒去哪了？

---

## 成因

促销活动页的规律是：**每页 25 条**。

以 4 店实测为例：促销页签共 **28 条**活动 → 只有 2 页（第 1 页 25 组 + 第 2 页 3 组）。

原始实现的分页逻辑是「一直往后翻，直到翻到空页」：

```python
while True:
    page += 1
    data = dump_page(store)
    if not data["groups"]:
        break          # ← 靠空页来判断结束
```

于是它**必然**会请求 `?page=3`。平台对这个越界页的处理是渲染一个空态页面：

> 目前无法创建促销活动

这个页面确实「没有商品组」。而 `dump_page()` 的容错逻辑是：

```python
def dump_page(store, retries=10):
    for i in range(retries):
        d = exec_js(store, DUMP_PAGE)
        if d.get("groups"):
            return d
        time.sleep(4)      # ← 每轮白等 4s
    return d
```

**「读到 0 个组」被当成了「页面还没加载好」。** 10 次 × 4s = 40s，
加上 exec_js 本身的往返，就是 40～60 秒。

更糟的是观感：浏览器停在空态页不动，看起来像死了。

---

## 修法（三层，从根到叶）

### 第一层：先读末页号，到末页就收手

不要靠「翻到空页」来判断结束——要**先问平台一共几页**。

```python
last_page = page_meta(sid)["last_page"]
...
if last_page is not None and page >= last_page:
    print("已到末页 %d，结束分页（不再探越界页）" % last_page)
    break
```

这样从根本上就不会请求 `?page=3`。

### 第二层：万一还是落到空态，立刻退

把空态文案变成一个**可识别的信号**，而不是当成加载失败：

```js
var empty = /无法创建促销活动/.test(document.body.innerText);
```

`dump_page` 在「读到 0 个组」时多查一次 `PAGE_META`，命中空态就立刻返回：

```python
if i < 3:
    m = page_meta(store)
    if m.get("empty"):
        return {"groups": [], "_empty": True, "last_page": m.get("last_page")}
```

注意这个检查只在异常分支里做。**正常路径一行没动**——第一次就拿到 groups 时，
`page_meta()` 根本不会被调用，零额外开销。重试次数也保持 10 不变，慢页面的容错能力不降级。

### 第三层：满页 ≠ 末页

`last_page` 的读取依赖分页控件的 DOM：

```text
« 1 2 3 ›
```

但平台不总是渲染全部页码。当页数较多时，控件可能只显示当前页附近的几个，
于是 `Math.max(...)` 读到的 max 会**小于真实末页**。

如果直接信这个值，就会提前收口，**后半截商品全部漏掉**。

兜底判据：**满页（≥20 组）说明后面一定还有**。

```python
if last_page is not None and last_page <= page and len(groups) >= 20:
    last_page = page + 1     # 末页号读错了，往后推一页
```

这就是为什么日志里末页号有时显示 `?`，有时显示一个被修正过的数字。

---

## 选择器层面的坑（同源问题）

判断「末页号」时，容器的选择器也踩过坑。

**错误写法**：

```javascript
document.querySelector('[class*="pagination"]')
```

**为什么错**：页面上有两个匹配的容器——

| 容器 | 身份 | 文本 |
|---|---|---|
| `div.communications-carousel__pagination` | 顶部**轮播图**的分页点 | 空 |
| `div.sc-list-pagination` | **列表**的分页控件 | 1 2 3 |

`querySelector` 按**文档顺序**返回第一个匹配，轮播图那个排在前面 →
读到 0 个页码 → `last_page = null` → 末页收口完全失效。

**正确写法**：指定列表自己的容器，并逐级降级：

```javascript
var scopes = ['.sc-list-pagination', '.andes-pagination'];
```

---

## 修复前后对比

| | 修复前 | 修复后 |
|---|---|---|
| 越界请求 | 每店每次都有 | 不再发生 |
| 白等时长 | 40～60s / 店 | 0 |
| 落空态时行为 | 卡住 40s 后放弃 | 秒退 |
| 末页号读错时 | 漏掉后半截商品 | 满页判据纠正 |
| 正常路径开销 | — | **无变化** |

两店串行，这个修复每轮省下约 **1.5～2 分钟**，且消除了「浏览器像卡死」的误判。
