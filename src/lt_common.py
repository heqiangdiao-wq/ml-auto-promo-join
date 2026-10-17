# -*- coding: utf-8 -*-
"""店铺会话层：对「某个店铺的当前标签页」做动作。

向上提供四个动词，全部以**数字 storeId** 为第一参数：

    exec_js(sid, script)   注入 JS 并取回解析后的 JSON
    visit(sid, url, wait)  导航并等待
    click(sid, selector)   CDP 真实鼠标点击
    href(sid)              当前地址（用于登录态与其它状态判断）

## 为什么第一参数必须是数字 storeId

传店铺名（`"4店"`）过去，紫鸟返回 `无权限操作  错误码：102`。
这个错误码极具误导性——它看起来像权限问题，实际是参数类型问题。

## 为什么读用 JS、写用鼠标

页面是 React 渲染的。`element.click()` 不会触发 React 的事件处理器，
所以所有**写操作**必须走 CDP 原生鼠标事件（`page click`）。
**读**则可以用 JS，因为读不改变状态，JS 更快也更灵活。

## 登录态检查

掉登录是这个项目最高频的「假故障」：页面停在 login 页，DOM 里没有商品组，
脚本读到 0 个组就正常收手，输出看起来像「今天没活动」。
所以 `check_login()` 必须在下结论「本店 0 条」之前调用一次。
"""

import time

from . import cdp_client as C

# 登录页的特征串
LOGIN_MARKERS = ("/login/", "challenge_id", "/identification")


def exec_js(store_id, script, timeout=120):
    """注入页面 JS；脚本须 return JSON 字符串。返回已解析对象。"""
    r = C.run(["page", "exec", "--store-id", store_id, "--script", C.prep(script)], timeout=timeout)
    node = C.unwrap(r.stdout, r.stderr)
    if isinstance(node, str):
        try:
            import json
            return json.loads(node)
        except Exception:
            return node
    return node


def exec_js_raw(store_id, script, timeout=120):
    """注入 JS，返回**未解析**的原始结果。"""
    r = C.run(["page", "exec", "--store-id", store_id, "--script", C.prep(script)], timeout=timeout)
    return C.unwrap(r.stdout, r.stderr)


def visit(store_id, url, wait=8):
    """导航到 url 并等待 wait 秒。

    wait 不是「保守估计」而是实测值：促销页首屏要渲染几十张活动卡片，
    10s 偶尔读到不完整的组列表，12s 稳定。
    """
    C.run(["page", "visit", "--store-id", store_id, "--url", url], timeout=180)
    time.sleep(wait)


def click(store_id, selector, timeout=120):
    """CDP 真实鼠标点击。

    selector 里的双引号在调用处转义为 \\"，因为整个 selector 要作为一个进程参数传递。
    """
    r = C.run(["page", "click", "--store-id", store_id, "--selector", selector], timeout=timeout)
    return (r.stdout or "")[:300]


def href(store_id):
    """当前地址。返回值可能是 str，也可能是带 __raw 的 dict（页面正在导航时）。"""
    return exec_js_raw(store_id, "location.href")


def page_title(store_id):
    return exec_js_raw(store_id, "document.title")


def check_login(store_id):
    """判断当前标签页是否掉登录。

    返回 (is_login_page: bool, href: str, title: str)

    ⚠️ 只在「读到 0 个商品组」之后调用。正常情况下不额外请求，零开销。
    """
    h = href(store_id)
    h = h if isinstance(h, str) else str(h.get("__raw") or "")
    t = page_title(store_id)
    t = t if isinstance(t, str) else str(t.get("__raw") or "")

    is_login = any(m in h for m in LOGIN_MARKERS) or "iniciar sessão" in t.lower()
    return is_login, h, t
