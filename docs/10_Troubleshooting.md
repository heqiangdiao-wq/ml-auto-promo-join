# 10 · 排障手册

按「你会看到什么」组织，每条都来自真实事故。

---

## A. 某店 0 条 —— 先查登录态，别急着下「今天没活动」的结论

### 症状

```text
==== 店铺 4店 ====
  第1页无组，结束分页
  4店 合计：可参加/参加 0，跳过 0
```

日志干净、EXIT=0、无 Traceback。**看起来像今天真的没有活动。**

### 真实原因

店铺掉登录了。页面停在：

```text
https://www.mercadolivre.com.br/login/identification?challenge_id=...
title: Digite seu e-mail ou telefone para iniciar sessão
```

登录页上当然没有商品组。`dump_page()` 读到 0 个组 → 判定「本页结束」→ 正常收手。
**整条链路没有任何异常。**

### 为什么这个错误特别危险

它混在正常输出里。往轮也会出现「跳过 0 条」的店吗？不会——正常店的跳过是 120+。
但如果你只看「成功几条」，4 店 0 条很容易被理解成「行情不好，今天没好提案」。

### 怎么确认

```bash
python -c "
from src import lt_common as L, constants as C
cfg = C.load_config()
sid = cfg['stores']['4店']
L.visit(sid, cfg['promos_url'], wait=12)
print('href  :', L.href(sid))
print('title :', L.page_title(sid))
"
```

`href` 里含 `/login/` 就是掉登录。

### 能不能自动恢复

**部分能。** 实测结论：

| 尝试 | 结果 |
|---|---|
| `close_store` → `open_store`（全新开） | ❌ 仍是登录页（2026-10-14 实证） |
| `store open`（reused=true） | ❌ 连访 3 次仍是登录页（2026-10-17 实证） |
| 隔一段时间自然恢复 | ✅ 有过（10-15 的 10 店自行恢复正常） |

**所以一旦确认掉登录，只能人工去紫鸟客户端重新登录该店铺。**

### 现版本的处理

`main.py` 在读到 0 个组时会强制做一次登录态检查：

```python
is_login, h, t = L.check_login(sid)
if is_login:
    log("  🔴 第%d页无组 = 店铺 %s 掉登录！" % (page, store))
    log("     → 需要在紫鸟客户端里人工重新登录该店铺，本次本店全部跳过")
    store_results.append({..., "reason": "店铺掉登录（需人工重登）"})
```

并在结果 JSON 里留一条 `type: "异常"` 记录。

### 掉登录会在两店之间轮换

实战记录：

| 日期 | 掉的店 | 表现 |
|---|---|---|
| 2026-10-14 | **10 店** | 整店 0 条，人工重登 |
| 2026-10-15 | — | 10 店自行恢复 |
| 2026-10-17 | **4 店** | 整店 0 条，需人工重登 |

**教训：任意一店 0 条都先查登录态，不要预设「掉登录只发生在某个店」。**

### 排查时的注意点

会话建立后**状态会残留**。如果你为了排查又跑了一次 `--dry`，会覆盖结果 JSON
（见 [08_Result_Schema.md](08_Result_Schema.md) 的 10-14 事故）。

排查顺序建议：

1. 先 `cp` 现有结果到带时间戳的备份，**核对里面的 `ts` 是上一轮**；
2. 用上面那段 `--store` 的 Python 单行直接查 `href`，**不跑主脚本**；
3. 确认是掉登录就去重登，不要试图用脚本绕过。

---

## B. 浏览器停在空页面，卡 40～60 秒

### 症状

屏幕上写着「目前无法创建促销活动」，然后什么都不发生。

日志：

```text
  第2页 3 组（2.8s，共?页）
  第3页无组，结束分页
```

第 2 页只有 3 组（不是 25），但还是请求了第 3 页。

### 原因

末页号没读到（`共?页`），于是退回「翻到空页为止」的老逻辑，
请求了不存在的 `?page=3`，平台渲染空态页，`dump_page` 把「0 个组」当成
「没加载好」重试 10 次 × 4s。

完整分析见 [04_Pagination_Trap.md](04_Pagination_Trap.md)。

### 怎么确认

看日志里 `共X页` 是不是 `?`。如果是 `?`，说明 `page_meta()` 没读到末页号。

单独验证：

```bash
python -c "
from src import lt_common as L, js_snippets as JS, constants as C
cfg = C.load_config()
sid = cfg['stores']['4店']
print(L.exec_js(sid, JS.PAGE_META))
"
```

正常应该看到 `{'empty': False, 'last_page': 2, 'items': 28, 'n_groups': 25}`。

`last_page` 是 `null` 就是选择器出问题了——检查 `constants.SEL_PAGINATION_SCOPES`。

---

## C. 展开后没有子行

### 症状

```text
    展开 gi22 | Forno Air Fryer 12l 1200w
      第1次展开 gi22 未出子行，重试
      第2次展开 gi22 未出子行，重试
      第3次展开 gi22 未出子行，重试
      [warn] gi22 展开失败，本组跳过
```

### 分两种情况

**① 偶发卡顿（多数）**

同一个组在别的页/别的时间能展开成功。实战记录：
`Caixa Transporte Pet Gato Cach` 第 1 页展开失败，第 2 页同名组展开成功。

**处理：什么都不用做。** 下一轮会自动重新处理。整组被跳过意味着这个商品这一轮的
提案没被判断——但明天还在池子里。

**② 组本身有问题**

连续多轮同一个 gi 都失败。可能原因：

- 这个组确实没有子行（箭头是渲染错误）；
- 子行加载接口持续报错。

**处理：** 手动打开这个商品在推广中心的页面看看。如果确实有子链接，
把 `norm_title` 后的组标题记下来，检查 `group_index_by_prefix` 是否能定位到它。

### 不要做的事

**不要加大重试次数。** 每次重试都是 13s 的 reload，成功率并不会提升。
`tries=3` 已经是实践最优。

---

## D. 卡片索引失效

### 症状

```text
[info] 索引失效，按扫描定位到第3个 box（非签名命中）
```

或者结果 JSON 里出现 `bi_orig` 字段。

### 原因

同一链接连续参加多个提案时，第一次成功后 DOM 重排
（新加入的那条变成「已预约/修改」并可能换位），后面几条的 `bi` 全部失效。

这是**正常现象**，不是 bug。脚本有三层兜底：

1. 精确索引；
2. 同行按卡片文本签名扫描；
3. reload + 重展开 + `relocate_action()` 重定位。

### 需要关注的信号

`btn_locate = "scan_first"`：

```json
{"btn_locate": "scan_first", "bi_orig": 5, "bi": 2}
```

含义是：签名没对上，退到了「这一行的第一个可点按钮」。

**这时点的可能不是预期的那张卡片。** 人工抽查这条记录：
看 `net_cells` 和 `camp` 是否对得上。如果对不上，
说明可能参加了一个非预期的提案（虽然净收判定仍然生效，不会亏，但记录会错位）。

---

## E. 「无绿字」突然变成 0 或暴涨

### 症状

往轮 `无绿字` 是 66～143 条，今天突然变成 0 条（或者反过来变成 300 条）。

### 原因

平台改了手续费减免的文案措辞。

```python
GREEN_PATTERN = r"我们降低|减免\s*R\$|手续费减免|每笔订单"
```

### 怎么确认

看一条实际的卡片文本：

```bash
python -c "
from src import lt_common as L, js_snippets as JS, constants as C
cfg = C.load_config()
sid = cfg['stores']['4店']
L.visit(sid, cfg['promos_url'], wait=12)
d = L.exec_js(sid, JS.DUMP_PAGE)
g = d['groups'][0]
for r in g['rows']:
    for c in r['campaigns'][:2]:
        print(repr(c['txt'][:300]))
        print('---')
"
```

把新的绿字表述补进 `constants.GREEN_PATTERN`。

### 同类风险

所有**文本判据**都有这个风险：

| 判据 | 常量 | 失效后果 |
|---|---|---|
| 激活 | `TXT_ACTIVATED` | 全部判成「无激活」→ 0 条参加 |
| 已预约 | `TXT_SCHEDULED` | 基准池变小 → 保守，漏机会 |
| 限时抢购 | `TXT_DEAL` | 不填数量 → 用平台默认值 |
| 空态页 | `TXT_EMPTY_PAGE` | 白等 40s |
| 绿字 | `GREEN_PATTERN` | 全过或全不过 |
| snackbar | `SNACKBAR_SUCCESS / FAIL` | 全变「待核验」 |

**「激活」失效是最致命的**——因为它是硬门槛，失效等于整轮 0 条。
所以 `constants.py` 里所有的文本判据都放在最显眼的位置。

---

## F. 结果 JSON 丢失

### 症状

`tmp/auto_join_promos.json` 的内容不是本轮想要的。

### 三个原因

| 原因 | 表现 |
|---|---|
| 跑了 `--dry` | `dry: true`，只包含 `--store` 指定的店 |
| 备份拿错了文件 | 恢复进去的是更早一轮的数据 |
| 跑崩了没写文件 | 文件还是上一轮的 |

### 检查顺序

```bash
# 1. 看 ts 是不是本轮
python -c "import json;d=json.load(open('tmp/auto_join_promos.json'));print(d['ts'],d['dry'],d.get('stores'))"

# 2. 看有没有备份
ls -lt tmp/*.bak.json | head -5

# 3. 核对备份里的 ts
for f in tmp/*.bak.json; do python -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
print(sys.argv[1], '->', d['ts'])
" "$f"; done
```

### 终极兜底

**读 stdout 日志。** 日志是追加的，不会丢，里面有每次运行的完整过程。

---

## G. 进程互抢标签页

### 症状

动作生效在错误的商品上，或者 `page click` 报找不到元素但页面上明明有。

### 原因

同时跑了两个实例。它们操作的是同一批标签页，CDP 命令互相穿插。

### 检查

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CommandLine | Format-List
```

### 预防

跑之前确认没有同名批处理在跑。定时任务不要设成重叠时间（全量耗时可到 35 分钟，
间隔至少留 60 分钟）。

---

## H. `WinError 2` / 找不到 ziniao-cli

### 症状

```text
FileNotFoundError: [WinError 2] 系统找不到指定的文件。
```

栈里完全看不出是路径问题。

### 原因

WorkBuddy 的 Node 版本目录改名了（`22.22.2-6` → `22.22.2-9`），
写死的 exe 路径失效。

### 现版本的处理

`cdp_client.find_cli_exe()` 用三级定位：环境变量 → PATH → glob 自动找最新。

### 确认

```bash
python -c "from src import cdp_client as C; print(C.cli_exe())"
```

如果报 `[CLI_NOT_FOUND]`，按提示装或用环境变量指定：

```bash
export ZINIAO_CLI="/path/to/ziniao-cli.exe"
```

### 另一个同源症状

```text
SyntaxError: Unexpected end of input
```

这不是路径问题，是 **JS 脚本被截断**。两个原因：

1. 经过 shell，`>` `<` `|` `&` 被当成重定向；
2. JS 被压成单行，`//` 注释吃掉了后面全部内容。

现版本已经用 `shell=False` + 不压行解决。如果你改了 `cdp_client.run()`，
小心别把这两个改回去。

---

## I. 页面读到的组数明显偏少

### 症状

往轮第 1 页 25 组，今天只有 8 组。

### 检查顺序

1. 登录态（见 A）；
2. 页面是否还在加载——加大 `page_wait_seconds`；
3. `dump_page` 的选择器 `.sc-list-row-group` 是否还命中（平台改版）；
4. 店铺的活动池是不是真的收缩了（看结果 JSON 的 `无激活` 计数）。

第 4 种是真实业务变化。实战记录里 10 店的「无激活」从 20 涨到 40，
就是活动池在收缩——这是业务信号，不是 bug。

---

## 排障时的顺序原则

```text
① 先看店铺登录态          ← 90% 的「0 条」都是这个
② 再看结果 JSON 的 ts/dry  ← 确认你在看的是哪一轮
③ 再看日志里的组数          ← 判断是「没读到」还是「读到了但都跳过」
④ 最后才怀疑判定逻辑        ← 判定逻辑最稳定，改它之前要有实证
```

反过来做（先怀疑逻辑）会浪费大量时间，因为逻辑部分有 8 轮实战验证，
而登录态每周都可能掉一次。
