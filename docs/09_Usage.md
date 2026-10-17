# 09 · 运行与用法

## 三种运行方式

### 1. 空跑（只判定，不点按钮）

```bash
python -m src.main --dry
```

这是**新环境第一次跑必须做的**。它会完整走一遍页面探测、展开、判定，
只是不点「立即参与 / 优化折扣 / 确认」。

输出里 `type` 全是 `可参加`，而不是 `参加`。

> ⚠️ **`--dry` 仍然会覆盖结果 JSON。** 不要拿它做「复核」——
> 复核应该读 stdout 日志，或者先手动 `cp` 备份。

### 2. 单店空跑

```bash
python -m src.main --store 4店 --dry
```

`--store` 的值必须是 `config/stores.local.json` 里 `stores` 的 key。

排查「某店数据异常」时用这个，能把跑动量压到一半。

### 3. 真实参加（生产用法）

```bash
python -m src.main
```

两店串行，全量。耗时 **20～35 分钟**。

## 参数速查

| 参数 | 作用 | 注意 |
|---|---|---|
| （无） | 两店串行，真实参加 | 生产默认 |
| `--dry` | 只判定不点按钮 | **仍覆盖结果 JSON** |
| `--store <key>` | 只跑一个店 | key 必须与配置一致 |
| `--click-more` | 额外逐个点开「查看更多促销」 | 实测无收益，默认关闭 |

`--click-more` 为什么默认关闭：

「查看更多促销」实测是**纯视觉折叠开关**（列表高度从 222px 变 841px，触发器文案变成
「查看更少促销活动」），点开前后 box 数量完全不变（8 → 8）。

也就是说：**视觉上被折叠的活动，DOM 里本来就全都在**，脚本一直读到了。
只有聚合父行有懒渲染（2 → 7），而父行我们不看。

所以这个开关留着只是因为「万一平台改版后它有意义」，正常不要开。

## 定时任务

推荐每天凌晨跑一次（巴西时间 0 点前后，此时美国站促销池刚刷新）。

Linux / macOS（crontab）：

```cron
0 1 * * * cd /path/to/ml-auto-promo-join && /usr/bin/python3 -m src.main >> logs/run.log 2>&1
```

Windows（任务计划程序）：

```bat
schtasks /Create /SC DAILY /ST 01:00 /TN "ml-auto-promo-join" ^
  /TR "cmd /c cd /d D:\path\to\ml-auto-promo-join && python -m src.main >> logs\run.log 2>&1"
```

或者用 `run.bat`（本项目自带）：

```bat
run.bat
```

## 日志

脚本本身只往 stdout 打日志，不写文件。重定向即可：

```bash
python -m src.main > logs/real_$(date +%Y%m%d_%H%M).log 2>&1
```

日志的正常形态：

```text
MLB→SKU 映射：2 个文件 / 344 个 MLB / 300 个带 SKU
店铺：4店, 10店
模式：真实参加

上一轮结果已备份 → auto_join_promos_20261016_013714.bak.json
==== 店铺 4店 ====
  第1页 25 组（3.2s，共2页）
    展开 gi1 | Caixa Transporte Pet Gato
    参加 NY-29223-RD | 净收34.17>33.19
      -> 成功 完成！您的商品已加入促销活动
    ...
  第2页 3 组（2.8s，共2页）
  已到末页 2，结束分页（不再探越界页）
  4店 合计：可参加/参加 17，跳过 134，异常 0
==== 店铺 10店 ====
  ...
结果已写 tmp/auto_join_promos.json
耗时 1850 秒
  4店：参与位 17（成功 6 / 失败 2 / 待核验 0 / 取消 9），跳过 134，异常 0
  10店：参与位 5（成功 2 / 失败 0 / 待核验 0 / 取消 3），跳过 126，异常 0
```

## 运行期间的三条纪律

### 1. 不要碰那两个标签页

脚本通过 CDP 操作真实浏览器。你在运行期间点一下、滚一下，
都可能让 `page click` 落到错误的元素上。

**点错位置不报错**——它可能点了别的商品的「立即参与」。

### 2. 不要启动第二个实例

两个实例会互相抢标签页。启动前确认没有同名批处理在跑：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CommandLine
```

### 3. 跑之前先看店铺登录态

这是**最高频的假故障来源**。见 [10_Troubleshooting.md](10_Troubleshooting.md)。

## 结果怎么读

```bash
# 看汇总
python -c "
import json
d = json.load(open('tmp/auto_join_promos.json'))
print(d['ts'], d['dry'], d['elapsed_seconds'])
print(json.dumps(d['summary'], ensure_ascii=False, indent=2))
"

# 看成功的
python -c "
import json
d = json.load(open('tmp/auto_join_promos.json'))
for r in d['results']:
    if r['type']=='参加' and r.get('result')=='成功':
        print(r['store'], r['ident'], r['kind'], r['net'], '>', r['base'])
"

# 看跳过的
python -c "
import json
from collections import Counter
d = json.load(open('tmp/auto_join_promos.json'))
print(Counter(r.get('reason') for r in d['results'] if r['type']=='跳过'))
"
```

完整的字段说明见 [08_Result_Schema.md](08_Result_Schema.md)。

## 什么时候该看文档而不是看代码

| 你遇到的症状 | 看哪里 |
|---|---|
| 某店 0 条 | [10_Troubleshooting.md](10_Troubleshooting.md) → 掉登录 |
| 浏览器卡在空页面 40 秒 | [04_Pagination_Trap.md](04_Pagination_Trap.md) |
| 展开后没有子行 | [05_Expand_Rule.md](05_Expand_Rule.md) |
| 读到的净收明显不对 | [07_Modal_NetRead.md](07_Modal_NetRead.md) |
| 「优化折扣」一条都没有 | [06_Decision_Rules.md](06_Decision_Rules.md) → 规则 6 |
| 「无绿字」突然变成 0 或暴涨 | [10_Troubleshooting.md](10_Troubleshooting.md) → 关键词失效 |
| 想改判定规则 | [06_Decision_Rules.md](06_Decision_Rules.md) 全文 |
