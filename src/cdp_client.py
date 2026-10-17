# -*- coding: utf-8 -*-
"""ziniao-cli 子进程客户端。

这一层只做一件事：**把一条 CLI 命令安全地跑起来，并把它的输出解析成 Python 对象。**
它不知道「店铺」「活动」「促销」是什么——那些属于 lt_common 层。

## 三个必须守住的细节

### 1. 直接调用 exe，绝不经过 shell

JS 脚本里必然出现 `>` `<` `|` `&`。一旦经过 cmd.exe，它们会被当成重定向和管道，
**脚本被静默截断**，报错是 `SyntaxError: Unexpected end of input`。
所以一律 `shell=False` + 参数逐项传。

### 2. exe 路径不能写死

WorkBuddy 的 Node 版本目录会随升级改名（`22.22.2-6` → `22.22.2-9`），
写死路径在升级后直接报 `WinError 2`，错误栈里完全看不出是路径过期。
用 glob 自动定位并取最新。

### 3. JS 脚本不压行

把多行 JS 合并成一行时，`//` 行注释会把后面整个脚本吃掉，同样表现为
`Unexpected end of input`。_prep() 只 strip()。
"""

import glob
import json
import os
import subprocess

# 定位失败时的兜底路径（仅用于给出更友好的报错信息，不作为主路径）
_FALLBACK_HINT = (
    r"C:\Users\<你>\.workbuddy\binaries\node\versions\<版本>\node_modules"
    r"\@ziniao-open\cli\bin\ziniao-cli.exe"
)


def find_cli_exe():
    """自动定位 ziniao-cli.exe。

    优先级：
      1. 环境变量 ZINIAO_CLI（显式指定，适合自建安装）
      2. PATH 里的 ziniao-cli（npm install -g 的常规情况）
      3. WorkBuddy 管理的 node 版本目录（glob，取最新）
    """
    env = os.environ.get("ZINIAO_CLI")
    if env and os.path.exists(env):
        return env

    from shutil import which

    on_path = which("ziniao-cli") or which("ziniao-cli.exe")
    if on_path:
        return on_path

    patterns = [
        # Windows / WorkBuddy 隔离工具链
        os.path.join(
            os.path.expanduser("~"), ".workbuddy", "binaries", "node", "versions",
            "*", "node_modules", "@ziniao-open", "cli", "bin", "ziniao-cli.exe",
        ),
        # 常规全局 npm
        os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "ziniao-cli.cmd"),
        "/usr/local/bin/ziniao-cli",
    ]
    hits = []
    for p in patterns:
        hits.extend(glob.glob(p))
    if hits:
        # 多个版本并存时取修改时间最新的那个（升级后旧目录不会被清理）
        hits.sort(key=lambda x: os.path.getmtime(x))
        return hits[-1]

    raise SystemExit(
        "[CLI_NOT_FOUND] 找不到 ziniao-cli.exe。\n"
        "  修复方式（任选其一）：\n"
        "    1) npm install -g @ziniao-open/cli\n"
        "    2) 设置环境变量 ZINIAO_CLI 指向 exe 完整路径\n"
        "  预期形态参考：%s" % _FALLBACK_HINT
    )


CLI_EXE_CACHE = None


def cli_exe():
    """惰性定位并缓存。

    不在模块级直接调用——否则光是 `import src.main` 就会去磁盘找 exe，
    单元测试和 CI 环境里没有紫鸟时会直接崩在 import 阶段。
    """
    global CLI_EXE_CACHE
    if CLI_EXE_CACHE is None:
        CLI_EXE_CACHE = find_cli_exe()
    return CLI_EXE_CACHE


def __getattr__(name):
    """兼容 `cdp_client.CLI_EXE` 这种旧写法，同时保持惰性。"""
    if name == "CLI_EXE":
        return cli_exe()
    raise AttributeError(name)


DEBUG = bool(os.environ.get("ML_DEBUG"))


def prep(script):
    """脚本原样传参，只做 strip。

    ⚠️ 历史坑：曾把多行 JS 压成单行以便传参 → `//` 之后的整段被吃掉。
    """
    return script.strip()


def run(args, timeout=120):
    """执行一条 ziniao-cli 命令。

    args 逐项传给 exe（shell=False），避免任何 cmd.exe 转义问题。
    """
    cmd = [cli_exe()] + [str(a) for a in args]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # 超时在真实运行里是常态（美客多偶尔极卡）。返回结构化结果让上层决定重试，
        # 而不是抛异常把整轮跑崩。
        return _Result(rc=-9, stdout="", stderr="__TIMEOUT__ after %ss" % timeout, cmd=cmd)

    if DEBUG:
        print("  $", " ".join(cmd)[:300], flush=True)
        print("   rc", r.returncode,
              "| out", (r.stdout or "")[:300].replace("\n", " "),
              "| err", (r.stderr or "")[:300].replace("\n", " "), flush=True)

    return _Result(rc=r.returncode, stdout=r.stdout, stderr=r.stderr, cmd=cmd)


class _Result:
    __slots__ = ("rc", "stdout", "stderr", "cmd")

    def __init__(self, rc, stdout, stderr, cmd):
        self.rc = rc
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.cmd = cmd

    @property
    def ok(self):
        return self.rc == 0

    def __repr__(self):
        return "<Result rc=%s out=%r>" % (self.rc, self.stdout[:80])


def unwrap(stdout, stderr=""):
    """把 CLI 的 JSON 外壳层层剥开。

    ziniao-cli 的返回结构在不同子命令下并不统一，实测见过三种：
        {"code":0, "data": {...}}
        {"code":0, "data": {"data": {...}}}
        {"code":0, "data": {"result": {...}}}
    这里统一剥到「真正有内容的那一层」。解析失败时不抛异常，
    而是带 __raw / __stderr 返回，让调用方自己判断（很多地方「读不到」是正常业务态）。
    """
    try:
        payload = json.loads(stdout)
    except Exception:
        try:
            err = json.loads(stderr)
        except Exception:
            err = {"stderr": (stderr or "")[:600]}
        return {"__raw": (stdout or "")[:400], "__stderr": err}

    node = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(node, dict) and "data" in node:
        node = node["data"]
    if isinstance(node, dict) and "result" in node:
        node = node["result"]
    return node


def call_json(args, timeout=120):
    """run + unwrap 的便捷组合。"""
    r = run(args, timeout=timeout)
    return unwrap(r.stdout, r.stderr)
