# AGENTS.md

给在本仓库工作的编码 Agent。人类看 `README.md`。

## First Message

用户第一条消息没给具体任务时，先读 `README.md`，再问要动哪一块。不要自己挑一块开工。

## Architecture

七层，依赖单向。反了就重写，别加注释解释为什么例外。

```
ui → core → tools → (skills, plugins, mcp) → config
model 独立，只依赖 config
tokens.py 是叶子：谁都能 import 它，它不 import 任何内部模块
```

| 层 | 目录 | 硬约束 |
| --- | --- | --- |
| ui | `miniagent/ui/` | 不写业务逻辑，不直接调模型 API |
| core | `miniagent/core/` | 不 print，不 import tkinter（`search.py` 这类纯逻辑放这儿才能脱离 GUI 测） |
| tools | `miniagent/tools/` | 不知道谁在调用它，不自己判权限 |
| skills | `miniagent/skills/` | 只发现和解析 md，不执行代码 |
| plugins | `miniagent/plugins/` | 不 import ui / core |
| mcp | `miniagent/mcp/` | **不 import tools / core 的包出口**（会成环），错误标记本地写 |
| model | `miniagent/model/` | 不处理工具 |

core 内部顺序：`state / events` ← `context / rules / completion` ← `loop`。
`ui` 允许直接 import `plugins`（插件管理窗口要用）。循环依赖一律算错。

## Code Quality

- NEVER 引入第三方依赖。标准库能做就用标准库，界面因此选了 Tkinter。
- NEVER 为了消掉类型或测试报错去删功能、降级逻辑。改根因。
- NEVER 在客户端里出现供应商分支。差异收在 `model/protocols/` 的适配器里。
- 注释写为什么，不写做什么。中文注释，英文标识符。
- 函数短，参数扁平，能返回字符串就别返回对象。
- 改一处之前先把整个文件读完，别靠 offset 猜上下文。

## Commands

改完代码（不是改文档）跑：

```bash
python agent.py --selftest        # 4 项，不需要 key
python tests/test_<topic>.py      # 只跑动到的那几个
C:\Python314\python.exe tests\test_gui.py   # 动了 ui 才跑
```

- NEVER 跑全套 16 个套件，除非用户明说。
- NEVER 提交，除非用户明说。
- tkinter 只装在 `C:\Python314`，默认 3.13 没有。

## Tool Usage

- NEVER 用 shell 的 `cat` / `type` / `sed` 读文件。用 `read_file`。
- 改文件前必须完整读过它。
- 搜代码用检索，不要遍历目录一个个打开。
- 改完工具注册表要调 `refresh()`，否则 Agent 看不到新工具。

## Failure Contract

工具失败一律 `return f"{ERR}原因"`。

- NEVER 让下游从文案里猜「这算不算失败」。
- 成功但内容含「失败」「不存在」这类词的，`execute()` 会自动补 `[OK]` 豁免。
- 关键词匹配只剩兜底，只看前 200 字符。

## Style

- 回答短。不要开场白、不要复述刚做过什么、不要「希望能帮到你」。
- 不要 emoji，除非用户在用。
- 技术陈述为主，直接但礼貌。
- 有数字就给数字，有路径就写路径。不确定就说不确定。

## Traps

这些都是实际踩过的，别踩回去。

**协议层**

- `tool_calls` 的函数名和参数是分段增量下发的，按 `index` 归并拼接。
- 模型给的参数 JSON 常不合法（字符串里有裸换行），用 `json.loads(..., strict=False)`。
- 超时可能在 `urlopen` 阶段抛，那里是 `TimeoutError` 不是 `URLError`，两处都要接。
- 已经吐过文字就不能重试，否则界面上出现重复内容。
- urllib 会改写请求头大小写（`x-api-key` → `X-api-key`），断言鉴权头时统一转小写。
- Anthropic 要求 user / assistant 严格交替，工具结果也是 user，连续同角色必须合并内容块。
- Gemini 不认 `additionalProperties`、`$schema`，发之前清掉。

**上下文层**

- 裁剪不能留孤儿 tool 消息，必须以 `assistant.tool_calls` 开头成对出现，否则 400。
- 摘要本身吃 token，只按消息算预算会超，`_summarize` 要限长并计入预算。
- `reserve` 在小预算下会算成负数，取 `min(..., budget // 2)`。

**循环层**

- 熔断只在 `core/guard.py` 判，循环里不自己数次数。加限制就加 `Limits` 字段 + `check_stop` 分支。
- 加工作模式 = 加一个 `_run_xxx(run)`。公共设施在 `_Run` 里，不复制循环样板。
- 计划模式的步骤成败不能听模型自夸：工具被拒且没成功过、结果里出现做不到，都算失败。
- 自评必须有次数上限，否则一次问答拖成死循环。
- 插话不能直接 append 到 messages，可能插在 `tool_calls` 和它的 `tool` 结果之间。走队列，在 `llm()` 开头取。
- 插话要重置熔断进度，但不退 `tool_calls` 预算。

**工具与边界**

- `tools.execute()` 永不抛异常，错误转文本回灌，Agent 靠报错自己纠错。
- 权限只在 `core/rules.py` 判，工具层不自查。拒绝也走回灌。
- 用户附加的文件只放开那一个路径，不是整个目录。
- shell 重定向能绕过文件白名单（`echo x > C:\Windows\y`），命令边界要单独校验 `>` / `copy` 的目标。
- 文档解析不许装包。docx/xlsx 是 zip+xml，pdf 自己解 FlateDecode，做不到就直说，不输出乱码。
- PDF 文字可能是字形编号，子集化 CID 字体必须靠 ToUnicode CMap 还原。
- PDF 的空格别按水平位移猜，很多 PDF 的空格本身就是字形。
- 技能是 md 不是代码，往 `skills/` 放文件即可。

**MCP**

- ALWAYS 走 `mcp_manager.tools()` 进注册表，工具名 `mcp__{server}__{tool}`，全进 `DANGEROUS`。
- NEVER 让 MCP 层的模块级 import core / tools —— tools import mcp、core 的出口又 import tools，必成环。需要常量就在本地写一份，用测试锁一致。
- 子进程的 stdout / stderr 都要在后台线程读完，少读一个就会堵死管道。
- 图片和 base64 NEVER 进上下文，只留元信息。
- 描述按 token 截断，不按字符 —— 中文和英文的字符:token 比差四倍。
- 改 MCP 后要 `refresh_tools()` + `session.refresh_system()`，摘要才会进提示词。

**界面**

- **按钮一律用 `Pill(variant=...)`**：`primary` / `secondary` / `danger` / `ghost`。手写 `fill` 时若漏了 `fg`，会白字压浅底看不见。
- **`Pill` 的 `bg` 是容器底色，不是按钮底色。** 传错会在圆角外面露出一圈黑框。
- **图标别用字体符号**（☰ ◧ 这类会掉成方框）。要图标就 `IconButton`，图形是渲染出来的图。
- **Canvas 图形一律渲染成 `PhotoImage`**，`create_oval` / `create_polygon(smooth=True)` 都没有抗锯齿。

- **应用显示名是「墟」**（`config.APP_NAME`），窗口标题、messagebox、命令行都从那儿取，别再写死字符串。
- **侧栏顺序固定**：搜索 → 墟品牌 → 新聊天 / 插件 → 项目 → 最近 → 用户区。设置和切换模型收在用户区菜单里，别再往导航加项。
- **侧栏宽度按窗口比例算**（`SIDEBAR_RATIO`，夹在 `SIDEBAR_MIN`~`SIDEBAR_MAX`），不要写死像素。
- **收起侧栏用 `_toggle_sidebar`**，收起后靠 `topbar` 里的把手展开，别另起一套开关。
- 默认头像走 `Avatar(image_path=AVATAR_PATH)`，圆形是逐像素 `transparency_set` 刷出来的（Tk 不能裁剪图片）。
- 侧栏底部只显示头像和用户名，**不显示当前模型**——模型信息在设置里看。
- 设置面板的内容放 `ScrollFrame` 里，窗口再小也能滚，不要靠缩字号硬塞。

- Tk 的 pack 顺序决定位置：`side="bottom"` 先 pack 的贴更底边。
- `messagebox` 是模态的，会卡死 `root.update()` 驱动的测试；测试里先关安全模式。
- 后台线程只能往 queue 塞事件，一切控件更新走主线程。
- Text 用 tag 的 `background` 做气泡会铺满整行，用户消息改成右对齐 + 变色。
- Markdown 必须按行渲染，流式里 `**` 会跨 chunk。
- 表格行太宽会被换行拆散，压成「首列加粗 + 其余 · 连接」。
- 插件导入的模块进 `sys.modules`，卸载时要 pop，否则重装拿到旧模块。
- NEVER 在函数签名里写 `bg=theme.BG` 这种默认值 —— 那是**模块导入时**求值的，换主题字号之后新控件还用旧值。一律 `None` + 函数体里 `or theme.XXX`。
- 换主题或改字体必须重建整个窗口，Tk 控件不会跟着模块变量变。重建走 `_reload_ui`，对话用 `_replay_history` 重放。
- 窗口尺寸要按 DPI 系数算（`apply_geometry`）。DPI 感知开了之后几何单位是物理像素，写死尺寸会让内容挤成一团。

**行为**

- 模型会自作主张改代码：读到文档里的「还没做的」就去实现。系统提示写死了「用户没让你改就别改」。
- 输入筛查只做加固，不做内容审查。命中就追加系统提醒 + 写审计，不在代码里硬拒用户。

## Rules

- 配置、路径白名单、预算都从 `config.py` 读环境变量，不在模块里写死。
- 规则文件分两个：`rules.md` 注入运行时（每轮都发，要短）；`agent.md` 是这份，运行时不读。往错了写等于没写。
- NEVER 提交 `.env`，NEVER 提交 `plugins/`（示例在 `examples/`）。
- 改风格只动 `ui/theme.py`，不在窗口代码里写死颜色。三套调色板在 `PALETTES`，正文对比度有测试锁着。
- 界面偏好存 `.agent/settings.json`，自定义模型存 `.agent/models.json`，两个都进 `.gitignore`。

## User Override

用户明确要求违反以上任何一条时，先说清会违反哪条、代价是什么，等到确认再动手。
不要说「好的」然后照做。
