# 02 · 系统架构

## 1. 分层

```text
┌──────────────────────────────────────────────────────────────┐
│  main.py                 编排：分店串行 → 分页 → 分组 → 处理    │
└───────────────┬──────────────────────────────────────────────┘
                │
    ┌───────────┴───────────┐
    │                       │
┌───▼────────────┐   ┌──────▼─────────────┐
│  analyze.py    │   │  joiner.py          │
│  决策引擎       │   │  执行器             │
│  · 三条件判定   │   │  · click_action     │
│  · base_pool   │   │  · join_one         │
│  · relocate    │   │  · 弹窗复核         │
└───┬────────────┘   └──────┬─────────────┘
    │                       │
    │              ┌────────▼──────────┐
    │              │  modal_reader.py  │
    │              │  净收款额结构化读  │
    │              └────────┬──────────┘
    │                       │
┌───▼───────────────────────▼──────────────────────────────────┐
│  page_probe.py     group_expand.py     receipt.py            │
│  页面探测/分页      展开商品组           snackbar 回执语义      │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  lt_common.py      店铺会话：exec_js / visit / click           │
│  cdp_client.py     进程封装：subprocess → ziniao-cli.exe       │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  js_snippets.py     注入页面执行的全部 JS 片段                  │
│  utils.py           金额解析 / 绿字检测 / 标题归一              │
│  sku_map.py         MLB → SKU                                  │
│  constants.py       选择器与运行参数（平台改版只改这里）         │
└──────────────────────────────────────────────────────────────┘
```

## 2. 为什么不把所有东西塞进一个文件

原始实现是**单文件 979 行**。它能跑，但改一处要读全文，排障时定位成本极高。

拆分的边界不是按行数，而是按**变更频率**：

| 层 | 改版时要不要动 | 理由 |
|---|---|---|
| `constants.py` | ✅ 经常 | 平台换了 class 名，改这里 |
| `js_snippets.py` | ✅ 经常 | DOM 结构变了，改注入脚本 |
| `page_probe.py` / `group_expand.py` | 偶尔 | 分页控件、展开交互变了才动 |
| `analyze.py` | 🔸 很少 | 业务规则，用户定稿后不动 |
| `joiner.py` / `modal_reader.py` | 偶尔 | 弹窗结构变了才动 |
| `utils.py` | ❌ 几乎不动 | 纯函数 |

**平台改版 → 改前两层就够。业务调整 → 只改 analyze.py。** 这是拆分带来的全部好处。

## 3. 数据流：一条提案的完整生命周期

```text
① 探测
   visit(PROMOS_URL)  →  等 12s
   dump_page()        →  注入 DUMP_PAGE，一次拿回整页所有组的 JSON
   page_meta()        →  读末页号，避免请求越界页

② 分组处理
   无箭头组 → 直接 analyze()
   有箭头组 → expand_fresh()（独占一次页面加载）→ analyze()

③ 判定（analyze）
   对每个子链接行：
     ├─ 无激活            → skips: "无激活在跑活动"
     ├─ 算 base_pool      → min(激活 ∪ 已预约)
     └─ 对每张卡片：
          ├─ 无绿字        → skips: "无绿字"
          ├─ join 且 net≤base → skips: "净收不高于基准"
          └─ 否则          → actions 队列

④ 执行（joiner.join_one）
   click_action() → 等 7s → READ_MODAL
     ├─ opt：读净收列，新>当前 才继续，否则 cancel
     ├─ join：读净收，再跟 base 比一次，不达标 cancel
     ├─ 限时抢购：填数量
     └─ TAG_CONFIRM → click → 等 6s

⑤ 回执（receipt）
   READ_SNACKBAR
     ├─ "已加入/已更新"  → 成功
     ├─ "未能将您的发布…" → 失败
     └─ 读不到            → 待核验
```

## 4. 三条贯穿全局的设计约束

### 4.1 读靠 JS，写靠鼠标

React 页面里 `element.click()` 不会触发事件处理器。所有**写操作**必须走 CDP 原生鼠标事件。

所以有一个固定套路：**先注入 JS 给目标元素打标记属性，再用 CDP 按属性选择器点击。**

```python
# 1. JS 打标记
L.exec_js(store, TAG_ACTION % (texts, gi, ri, bi, attr))
# 2. CDP 真实点击
L.click(store, '[%s="1"]' % attr)
```

而且每次打标记前**必须先清掉旧标记**——否则 CDP 会命中文档里第一个残留标记元素，点错行。

### 4.2 索引不可信，签名才可信

DOM 会在操作过程中重排。第一次成功后，那条卡片可能从「立即参与」变成「已预约/修改」并换位，
导致后面几条的 `bi`（box 索引）全部失效。

应对是三层兜底：

1. **精确索引** `TAG_ACTION`
2. **同行扫描** `TAG_ACTION_SCAN`——在同一个行里按卡片文本签名重新找按钮
3. **重载重定位** `relocate_action()`——reload + 重展开后，用 `ident + camp 前 60 字` 找回同一条动作

### 4.3 每次读关键状态前先重载

session 里的 DOM ≠ 平台持久化的状态。任何「改完再看」的操作都必须先 reload，
否则看到的是自己刚才改的本地 DOM，不是平台真实结果。

## 5. 运行参数集中管理

所有「等多久」「重试几次」都收敛在 `config/stores.example.json` 的 `runtime` 段：

```json
"runtime": {
  "page_wait_seconds": 12,
  "expand_poll_times": 8,
  "expand_poll_interval_seconds": 4,
  "modal_wait_seconds": 7,
  "snackbar_wait_seconds": 6
}
```

这些数值不是拍脑袋定的：

- `expand_poll_times=8` × `interval=4s` —— 早期用 3 次 × 3s，美客多一卡就误判展开失败，误判率高；
- `page_wait_seconds=12` —— 促销页首屏带大量活动卡片，10s 偶尔读不到完整组列表。

改这些值之前，先看 [11_Run_Journal.md](11_Run_Journal.md) 里对应的排障记录。
