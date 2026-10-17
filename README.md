# ML Auto Promo Join · 美客多推广中心自动报活动

> 一个每天凌晨自动跑完两个店铺、遍历数百条促销提案、只在**净收更高**时才替你按下「立即参与」的机器人。

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](#)
[![CDP](https://img.shields.io/badge/Transport-CDP-6E4AFF)](#)
[![Platform](https://img.shields.io/badge/Platform-Mercado%20Libre-FFE600)](#)
[![Status](https://img.shields.io/badge/Status-production-2EA043)](#)

---

## 它解决什么问题

美客多卖家中心的「推广中心」会持续推送促销邀请：平台让你让出一点价格，换取曝光位。运营要做的判断只有一句——
**这个提案的净收款额，是不是比我手上正在跑的那档更高？**

听起来简单，做起来是灾难：

- 4 店 + 10 店，一个店铺的促销页就有 **2～3 页、每页 25 个商品组**；
- 一个商品组展开后是若干子链接（SKU/MLB），每个子链接下挂着 **2～11 张活动卡片**；
- 每张卡片要分别判断「有没有激活」「净收多少」「有没有手续费减免绿字」「按钮是立即参与还是优化折扣」；
- 全部判断完，才知道这 8～16 条提案里哪几条真值得点。

人工做一遍 25～35 分钟，每天做，且**漏一条就是白丢一次曝光**。

这个项目把它变成一条命令。全量真实跑耗时 **20～35 分钟**，产出 130～350 条结构化判定记录。

## 核心决策模型

```text
硬门槛：该链接必须有「激活」在跑的活动 ──── 无激活 → 整链接跳过
   ↓
基准线：base = min( 「激活」∪「已预约」的净收款额 )
   ↓
逐提案：
  · 无手续费减免绿字        → 跳过（join / opt 都要有）
  · 新提案净收 ≤ 基准       → 跳过
  · 立即参与 → 弹窗里再复核一次净收，仍 > 基准才确认
  · 优化折扣 → 弹窗里比「新提案 vs 当前档」，新 > 当前才确认
   ↓
落库校验：读 snackbar 回执，三种语义分别记 成功 / 失败 / 待核验
```

为什么基准要取「激活 ∪ 已预约」的最小值而不是只看激活——因为已预约同样是你**已经占住的档位**，只要打得过其中最差的那条，换上去就是净赚。这一条在早期版本里被漏掉，直接导致每次运行白丢机会。

## 目录结构

```text
ml-auto-promo-join/
├── src/
│   ├── constants.py        常量与选择器（平台改版时只改这里）
│   ├── cdp_client.py       对 ziniao-cli 的进程封装
│   ├── lt_common.py        店铺会话：exec_js / visit / click
│   ├── utils.py            金额解析、绿字检测、标题归一
│   ├── sku_map.py          MLB → SKU 映射
│   ├── js_snippets.py      注入页面的全部 JS 片段
│   ├── page_probe.py       页面探测、分页末页号
│   ├── group_expand.py     展开商品组（含「一次加载只能展开一次」铁律）
│   ├── analyze.py          决策引擎
│   ├── modal_reader.py     弹窗净收款额的结构化读取
│   ├── joiner.py           执行器：点按钮、填数量、确认
│   ├── receipt.py          snackbar 回执语义
│   └── main.py             编排
├── config/stores.example.json
├── schema/result.schema.json
├── docs/                   里程碑、业务背景、架构、决策规则、排障、实战复盘
└── samples/                真实运行的结果切片
```

## 快速开始

```bash
git clone <this-repo>
cd ml-auto-promo-join
pip install -r requirements.txt

# 1. 配置店铺（storeId 不进仓库）
cp config/stores.example.json config/stores.local.json
#    编辑 stores.local.json，填入你自己的 storeId

# 2. 空跑，只看判定不点按钮
python -m src.main --dry

# 3. 单店空跑
python -m src.main --store 4店 --dry

# 4. 真实参加
python -m src.main
```

## ⚠️ 使用前提

- 需要本机已安装 **紫鸟浏览器 + `@ziniao-open/cli`**，店铺已在紫鸟客户端里登录；
- 脚本通过 CDP 操作**真实浏览器**，运行期间请不要手动碰那两个标签页；
- 首次使用务必先 `--dry` 跑通，确认能读到活动列表再真实参加。

## 里程碑

构建过程拆成 20 个部分，归并为 4 个四分之一节点。切点不是按文件数均分，而是按**可用性跃迁**：每个节点结束时，项目刚好跨过一道能单独验证的门槛。

| 节点 | 覆盖 | Tag | 累计文件 | 结束时能做什么 |
|---|---|---|---|---|
| **1/4 打地基** | 01-05 | `q1-foundation` | 11 | 连上紫鸟浏览器，列出店铺，确认环境可用 |
| **2/4 取数** | 06-10 | `q2-capture` | 19 | 打开推广中心、翻页到底、展开商品组拿到全部子链接 |
| **3/4 判定与执行** | 11-15 | `q3-decide-execute` | 26 | `--dry` 跑出完整判定结果；去掉 `--dry` 即真实参加 |
| **4/4 工程化交付** | 16-20 | `q4-deliver` | 36 | 结果有 Schema 约束，他人可照文档独立复现 |

完整划分依据与每段验收标准见 [`docs/00_Milestones.md`](docs/00_Milestones.md)。

<details>
<summary>20 个部分明细</summary>

| # | 部分 | 状态 |
|---|---|---|
| 01 | 项目骨架与定位：README / .gitignore / LICENSE / 店铺配置模板 | ✅ |
| 02 | 业务背景：美客多「报活动」在做什么，六条规则与三个边界 | ✅ |
| 03 | 系统架构：分层、数据流、三条贯穿全局的约束 | ✅ |
| 04 | 环境与依赖：紫鸟 CLI、storeId、四个环境坑 | ✅ |
| 05 | CDP 客户端层：ziniao-cli 子进程封装与 exe 三级定位 | ✅ |
| 06 | 店铺会话层：exec_js / visit / click / 掉登录检测 | ✅ |
| 07 | 纯函数工具：巴西金额解析、绿字检测、标题归一、去重键 | ✅ |
| 08 | MLB → SKU 映射层 | ✅ |
| 09 | 页面探测与分页：整页快照、末页号、越界页陷阱 | ✅ |
| 10 | 商品组展开：一次页面加载只能展开一次 | ✅ |
| 11 | 决策引擎：三条件判定与基准池模型 | ✅ |
| 12 | 弹窗净收结构化读取：cols-3 与 cols-1 陷阱 | ✅ |
| 13 | 执行器：三层定位兜底、弹窗复核、限时抢购数量 | ✅ |
| 14 | 回执校验：snackbar 三种语义与重试边界 | ✅ |
| 15 | 编排层：分店串行、分页循环、异常记录、结果落库 | ✅ |
| 16 | 结果 JSON Schema 与字段诊断含义 | ✅ |
| 17 | 运行与用法：--dry / --store、定时任务、运行纪律 | ✅ |
| 18 | 排障手册：掉登录轮换、展开失败、索引失效、关键词失效 | ✅ |
| 19 | 实战复盘：九轮真实运行记录与六条结论 | ✅ |
| 20 | 合规边界、变更日志与完整交付 | ✅ |

</details>

**全部 20 部分已完成，对应 4 个里程碑 tag 均已打点。**

## License

MIT
