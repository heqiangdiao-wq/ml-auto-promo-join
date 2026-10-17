# -*- coding: utf-8 -*-
"""常量与选择器。

平台改版时的**第一改动点**。这里集中了三类东西：
  1. 页面选择器（class 名一改，全项目的读写都会跟着断）
  2. 按钮文案（平台经常改写措辞）
  3. 运行参数（等待秒数、重试次数）

改动纪律：
  · 只改这一层，不要改调用方的逻辑；
  · 改完必须跑 --dry 验证，确认 dump 到的组数和往轮同量级。
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------- 路径 ----------------

CONFIG_PATH = os.path.join(BASE_DIR, "config", "stores.local.json")
EXAMPLE_CONFIG_PATH = os.path.join(BASE_DIR, "config", "stores.example.json")
RESULT_PATH = os.path.join(BASE_DIR, "tmp", "auto_join_promos.json")

# ---------------- 入口 ----------------

PROMOS_URL = "https://vendedores.mercadolivre.com.br/anuncios/lista/promos"

# ---------------- 按钮文案 ----------------
# 2026-10-09 实测补全。JOIN = 新提案的参加按钮；OPT = 已参加活动的优化入口。
# ⚠️ 只认「立即参与」会整条漏掉「优化折扣」入口（实测当天 0 条记录）。

JOIN_BTNS = ("立即参与",)
OPT_BTNS = ("优化折扣",)

# ---------------- 页面选择器 ----------------

# 商品组容器
SEL_GROUP = ".sc-list-row-group"
# 组左侧（箭头所在区域）
SEL_GROUP_ASIDE = ".sc-list-row-group__aside"
# 箭头本身。
# ⚠️ 展开后类名会从 sc-ui-chevron--down 变成 sc-ui-chevron--up。
#    只查 --down 会导致展开后 has_chevron 变 False → 整组落到「无箭头」分支
#    → 把无 SKU 的聚合父行也当链接行判定。用「存在任意 chevron」即可（收起/展开都成立）。
SEL_CHEVRON = '[class*="sc-ui-chevron"]'
# 子链接行
SEL_ROW = ".sc-list-row"
# 活动卡片
SEL_BOX = ".sc-list-collapsible-row__promotion-box"
# 「查看更多促销」触发器（纯视觉折叠，默认不点）
SEL_MORE_TRIGGER = ".sc-list-collapsible-row__trigger"
# 弹窗根节点
SEL_MODAL = ".promotion-modal-cdp2026"
# 弹窗里的数量输入行
SEL_MODAL_INPUT_ROW = ".promotion-modal-cdp2026__row--input"
# 弹窗底部动作区（确认按钮在 .andes-modal__actions 里，不在 modal 根节点内）
SEL_MODAL_ACTIONS = ".andes-modal__actions"
# 净收款额多列容器
SEL_MULTI_CHARGES = ".sc-multi-column-charges"
# 分页控件。
# ⚠️ 绝对不能用 [class*="pagination"] —— 页面里 communications-carousel__pagination
#    （轮播图的分页容器，文本为空）在文档顺序上排在列表分页之前，querySelector 会先命中它
#    → 读到 0 个页码 → last_page=None → 末页收口失效 → 请求越界页 → 浏览器停在空态页。
SEL_PAGINATION_SCOPES = (".sc-list-pagination", ".andes-pagination")

# ---------------- 卡片文本特征 ----------------

TXT_ACTIVATED = "激活"
TXT_SCHEDULED = "已预约"
TXT_NET_LABEL = "净收款额"
TXT_DEAL = "限时抢购"
TXT_EMPTY_PAGE = "无法创建促销活动"

# 手续费减免绿字的识别模式
GREEN_PATTERN = r"我们降低|减免\s*R\$|手续费减免|每笔订单"

# 净收标签的兜底写法
NET_LABELS = ("净收款额", "净收款", "净收")

# ---------------- 回执语义 ----------------

SNACKBAR_SUCCESS = ("已加入", "已更新")
SNACKBAR_FAIL = ("未能将您的发布", "请重试")

# ---------------- 去重 ----------------

# 活动卡片文本截断长度。
# ⚠️ 必须全项目一致：跳过路径与参加路径算出的键要对上，否则去重失效、报告自相矛盾。
CAMP_TRUNC = 80
# 重载后按签名重定位时用的截断长度
CAMP_TRUNC_SIG = 60

# ---------------- 运行参数默认值 ----------------

DEFAULT_RUNTIME = {
    "page_wait_seconds": 12,
    "expand_poll_times": 8,
    "expand_poll_interval_seconds": 4,
    "modal_wait_seconds": 7,
    "snackbar_wait_seconds": 6,
}


def load_config():
    """读本地配置。stores.local.json 不存在时给出可执行的报错，不猜。"""
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else None
    if not path:
        raise SystemExit(
            "[CONFIG_MISSING] 缺少 %s\n"
            "  → cp config/stores.example.json config/stores.local.json\n"
            "  → 再填入你自己的 storeId（该文件已被 .gitignore 排除）"
            % os.path.relpath(CONFIG_PATH, BASE_DIR)
        )
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    stores = cfg.get("stores") or {}
    if not stores:
        raise SystemExit("[CONFIG_INVALID] stores.local.json 里没有 stores 字段")
    for name, sid in stores.items():
        if not sid or not str(sid).isdigit():
            # 占位值 00000000000000 会被这里拦下——这是有意的，别把示例 ID 当真实 ID 跑。
            raise SystemExit(
                "[CONFIG_INVALID] 店铺「%s」的 storeId=%r 不是纯数字，请填真实 storeId" % (name, sid)
            )

    rt = dict(DEFAULT_RUNTIME)
    rt.update(cfg.get("runtime") or {})

    return {
        "stores": stores,
        "promos_url": cfg.get("promos_url") or PROMOS_URL,
        "runtime": rt,
    }
