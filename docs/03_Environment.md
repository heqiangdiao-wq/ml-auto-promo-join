# 03 · 环境与依赖

## 1. 运行时

| 组件 | 版本要求 | 说明 |
|---|---|---|
| Python | **3.12+** | 只用标准库，无第三方运行时依赖 |
| 紫鸟浏览器 | 客户端最新版 | 店铺登录态在这里 |
| `@ziniao-open/cli` | 1.0.7+ | CDP 操作的唯一入口 |
| Node.js | 22.x（由紫鸟 CLI 自带依赖） | 不需要单独装 |

`requirements.txt` 里没有 `install_requires` 条目——这个项目**零第三方依赖**。
原因是所有能力都通过子进程调用 `ziniao-cli.exe` 实现，Python 侧只做数据处理：

```
subprocess → ziniao-cli.exe page exec    （注入 JS 读 DOM）
           → ziniao-cli.exe page click   （CDP 真实鼠标事件）
           → ziniao-cli.exe page visit   （导航）
```

## 2. 安装紫鸟 CLI

```bash
npm install -g @ziniao-open/cli
```

装完确认：

```bash
ziniao-cli --version
ziniao-cli store list          # 应该能列出你已登录的店铺
```

## 3. 找 storeId

`storeId` 是紫鸟给每个店铺分配的一串数字，**不是美客多的店铺编号**。

三个获取途径：

1. `ziniao-cli store list` 的输出里带 `storeId` 字段；
2. 紫鸟客户端 → 店铺管理 → 店铺详情；
3. 紫鸟开放平台后台的店铺列表。

拿到后填进 `config/stores.local.json`：

```json
{
  "stores": {
    "4店": "你的第一个店铺 storeId",
    "10店": "你的第二个店铺 storeId"
  }
}
```

> 店铺名（`4店` / `10店`）只是**你自己给店铺起的别名**，随便改。
> 但 `--store` 参数的值必须和这里的 key 完全一致。

## 4. 四个必须先知道的环境坑

### 4.1 CLI 路径会随 WorkBuddy / Node 升级改名

如果你在 WorkBuddy 环境里跑，`ziniao-cli.exe` 的路径长这样：

```
C:\Users\<你>\.workbuddy\binaries\node\versions\22.22.2-6\node_modules\@ziniao-open\cli\bin\ziniao-cli.exe
                                                    ^^^^^^^^ 这一段会变
```

每次 WorkBuddy 升级，`22.22.2-6` 可能变成 `22.22.2-9`。写死路径会在升级后直接报
`WinError 2`，而错误栈里完全看不出是路径过期。

所以 `lt_common._find_cli_exe()` 用 glob 自动定位：

```python
pats = glob.glob(r"**\node\versions\*\node_modules\@ziniao-open\cli\bin\ziniao-cli.exe")
pats.sort(key=os.path.getmtime)   # 多个版本并存时取最新的
return pats[-1]
```

### 4.2 必须直接调用 exe，不能走 .cmd 或 shell

JS 脚本里必然出现 `>` `<` `|` `&` 这些字符。一旦经过 `cmd.exe`，
它们会被当成重定向和管道，**脚本被静默截断**。

历史上一大批 `SyntaxError: Unexpected end of input` 的真正根因就是这个。

正确做法（`shell=False`，参数逐项传）：

```python
subprocess.run([CLI_EXE, "page", "exec", "--store-id", sid, "--script", script],
               capture_output=True, text=True, encoding="utf-8")
```

### 4.3 不要把 JS 压成单行

有人为了「方便传参」会把多行 JS 合并成一行。这会踩另一个坑：
JS 里的 `//` 行注释会把**后面整个脚本**都吃掉，同样表现为
`SyntaxError: Unexpected end of input`。

`lt_common._prep()` 只做 `strip()`，不压行。

### 4.4 exec_js / visit 的第一个参数必须是数字 storeId

传店铺名（`"4店"`）过去，紫鸟会返回：

```
无权限操作  错误码：102
```

这个错误码很误导——它看起来像权限问题，实际是参数类型问题。**传数字 ID。**

## 5. 验证环境是否就绪

```bash
# 1. CLI 能定位到
python -c "from src import lt_common as L; print(L.CLI_EXE)"

# 2. 配置能读到
python -c "from src.constants import load_config; print(load_config()['stores'])"

# 3. 能读到页面（这会真的打开浏览器）
python -c "
from src import lt_common as L, constants as C
cfg = C.load_config()
sid = list(cfg['stores'].values())[0]
L.visit(sid, C.PROMOS_URL, wait=12)
print(L.href(sid))
"
```

第三步的输出如果落在 `/login/`，说明店铺掉登录了。见 [10_Troubleshooting.md](10_Troubleshooting.md)。
