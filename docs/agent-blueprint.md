# 从零造一个同款 Agent：可复用架构方案

> 来源：墟（mini_agent）项目复盘。约 10,500 行 Python、零第三方运行时依赖、
> 22 个测试套件 ~950 断言全绿。本文把「哪些东西可以直接搬走、哪些设计决策
> 为什么这么定、按什么顺序做」写成一份可执行的蓝图。
> 目标：照着做，能造出一个能力对齐的桌面 Agent。

---

## 0. 适用边界

适合：本地单用户的编码/任务型 Agent（对话 + 工具调用 + 文件操作 + 外部工具协议）。
不适合照搬：多用户服务端、浏览器自动化为主体的 Agent、强沙箱隔离场景——
这些在本方案里只有「留了接口」没有「做完」，见第 9 节。

---

## 1. 七条铁律（先立规矩，后写代码）

1. **依赖严格单向**：`ui → core → tools → (skills/plugins/mcp) → config`，`model` 独立。
   违反单向的 import 一律视为 bug。这是整个项目能持续演进不出 spaghetti 的根。
2. **消息入库只有一个口**：所有历史写入（用户输入/模型回复/工具结果/插话）走
   `Session.append()`。持久化、审计、回放都是挂在口上的观察者。旁路写入 = 漏记。
3. **工具结果必须自带成败语义**：`[ERR] `/`[OK] ` 结构化前缀，不让下游猜词。
   标记常量放零依赖叶子模块（markers.py），全包单一来源。
4. **按 token 卡，不按字符**：中文 1 字 ≈ 1 token、英文 4 字符 ≈ 1 token。
   截断、预算、压缩全部用统一的 `tokens.py`，禁止各处自算长度。
5. **收手不抛异常**：熔断触发时产出一份「卡在哪、试过什么、下一步」的人话报告
   作为答复交付。用户不能拿到一片空白。
6. **协议分支不下沉客户端**：客户端只认统一事件；出现 `if 供应商` 就该写适配器了。
7. **每个阈值有依据**：默认值先拍脑袋没关系，但必须补实测校准 + 自洽性回归锁。

---

## 2. 总体架构

```
gui.py / agent.py            入口（GUI 用 pythonw + tkinter；CLI 用 argparse）
        │
   ┌────▼────┐
   │    ui   │  窗口、主题（三套调色板）、DPI 适配、对话框、事件泵
   └────┬────┘
   ┌────▼────┐
   │  core   │  loop（ReAct/Plan 双模式）· session · context · guard
   │         │  completion · safety · rules · state · persistence · search
   └────┬────┘
   ┌────▼────┐        ┌─────────────┐
   │  tools  │        │ skills / plugins / mcp   （扩展层，mcp 不许 import core/tools）
   └────┬────┘        └─────────────┘
   ┌────▼────────────────────┐
   │ config（路径/环境/预算） │
   └─────────────────────────┘

   model/（独立层）client · registry（37 供应商）· wire（统一中间表示）
                └ protocols/（4 个协议适配器）

   叶子模块（谁都能 import，自己不 import 包内任何东西）：
   tokens.py（token 估算与截断）· markers.py（[ERR]/[OK] 标记）
```

**为什么这么分**：内核可独立测试（不碰网络、不碰 UI）；UI 可整层销毁重建
（换主题/换字体）；扩展层可以整体不存在（不用 MCP 的人零开销）。

---

## 3. 模块复用清单

### 3.1 可以几乎原样搬走（约 2,600 行，占包体 1/4）

| 文件 | 行数 | 提供什么 | 改动需求 |
| --- | --- | --- | --- |
| `tokens.py` | ~90 | estimate_text / take_prefix / take_suffix / clip（二分找 token 边界） | 无 |
| `fetch.py` | ~120 | GitHub 下载 / zip 解析 / 路径穿越防护（插件与技能安装共用） | 无 |
| `markers.py` | 23 | [ERR]/[OK] 标记单一来源 | 无 |
| `core/context.py` | 199 | 预算裁剪 + 摘要折叠 + 工具结果压缩 + RecordLog | 改导入路径 |
| `core/guard.py` | 257 | 六类熔断 + 三层失败判定，阈值全部环境变量可调 | 无 |
| `core/persistence.py` | 215 | 会话 JSONL 存档/恢复/列表，崩溃自修复 | 无 |
| `core/completion.py` | ~100 | 验证式完成度判定 | 无 |
| `core/safety.py` | ~130 | 注入筛查 + 审计 | 可换词表 |
| `core/session.py` | ~110 | 消息单口入库 + 插话队列（线程安全） | 无 |
| `model/wire.py` | ~120 | 统一中间表示（4 类事件） | 无 |
| `model/protocols/*` | ~500 | OpenAI Chat / Anthropic / Gemini / Responses 适配器 | 按需删减 |
| `mcp/*` | ~1,000 | stdio client + schema 压缩 + 管理器 + 元工具 | 无 |
| `tests/bench_limits.py` | ~200 | 阈值实测校准方法论 | 改任务集 |

### 3.2 需要按自己的业务改写

- `tools/*` 内置工具（文件/命令/网页/文档解析）：接口约定（见 4.2）保留，实现可换
- `ui/*`：tkinter 自绘控件是本项目特有投入，换技术栈时只复用「事件泵 + 状态快照」模式
- `core/rules.py`：边界白名单与危险操作清单，按你的安全域重写

### 3.3 设计资产（无形但最值钱）

- 六类跑飞模型 + 每个阈值的推导依据（第 5.2 节，直接抄表）
- 25 条踩坑清单（第 8 节精选）
- 零依赖测试方法论（第 6 节）

---

## 4. 分层详解

### 4.1 配置与叶子模块

```python
# config.py：唯一读 os.environ 的地方，其他层都从这取
BASE_DIR / AGENT_DIR / RECORDS_DIR / SESSIONS_DIR
context_budget() -> int        # 环境变量可覆盖，带下限保护
mcp_tool_budget() -> int       # 每个预算一个函数，带 max() 下限

# tokens.py：全项目唯一的长度单位
estimate_text(text) -> int                     # 中文 1 字 1 tok，英文 4 字符 1 tok
take_prefix(text, max_tokens) / take_suffix()  # 二分找边界，可在句号处收尾
clip(text, max_tokens, sentence=True)          # 通用截断

# markers.py：工具结果协议
ERR = "[ERR] "    # 工具拼结果用
ERR_TAG = "[ERR]" # guard 做 startswith 判定用
```

**要点**：预算类配置全部走「环境变量 + 默认值 + max() 下限」三件套，运维时不用改代码。

### 4.2 工具层

```python
# 注册表：名字 -> (schema, 实现)。schema 就是 OpenAI function calling 形状
TOOL_SCHEMAS: list[dict]
DANGEROUS: set[str]            # 危险工具集合（shell/写文件/跑代码），安全模式确认用
def execute(name: str, raw_args_json: str) -> str   # 永不抛异常，错误转 [ERR] 文本
```

工具实现的四条约定：
1. 失败返回 `ERR + 人话原因`，成功返回内容（歧义词由 `tag_result` 自动补 OK 豁免）
2. 单条输出用 `truncate()` 截到 `MAX_TOOL_OUTPUT`（默认 8000 字符）
3. 参数是 JSON 字符串，解析失败也返回 `[ERR]`，不抛
4. 所有路径操作过 `rules.py` 白名单，越界返回 `被规则拒绝：…`

**为什么不抛异常**：内核的循环里没有 try/accept 散弹，一条路径走到底；
熔断只需要看文本前缀。这让整个内核确定性极强、好测。

### 4.3 模型层（统一中间表示 + 适配器）

```python
# wire.py：所有供应商的原生协议在适配器边界翻译成这一套
@dataclass
class RequestContext:  # model_id / messages / tools / temperature / max_tokens
                       # / stream / api_key / base_url（带 .base 属性去尾斜杠）/ quirks

@dataclass
class HttpRequest:     # url / headers / body —— 适配器产物，可直接发

# 事件只有 4 类：TextDelta / ToolCallDelta / Usage / Finished
# 新增供应商 = 继承 Protocol 实现 build() + parser()，客户端零改动

# client.py：统一出口，内置
#   - 读超时 60s + 请求总时限 180s + 指数退避重试 2 次
#   - 只有 5xx/429/网络错误/流停滞才重试；4xx 立刻失败
#   - 已经吐出文字就不重试（否则界面重复）；重试成功要告知用户
```

`quirks` 字典处理供应商怪癖（如某家鉴权头不同），避免 if-else 扩散。

### 4.4 内核（六大件）

**Session（会话状态）**

```python
class Session:
    messages: list[dict]          # system + user/assistant/tool
    steps: int; tool_calls: int
    def append(self, msg)         # 唯一入库口，触发观察者
    def attach_observer(fn)       # 持久化挂这里；观察者抛异常自动摘除
    def interject(text) -> bool   # 中途插话队列（上限 5，满了挤最旧），线程安全
```

**ContextManager（上下文预算）**

- `prepare(messages)`：超预算时从最新往回捡，被裁掉的历史压成一条
  规则摘要（用户问过什么/调过什么工具/当时的结论）塞回窗口头部；
  不留孤儿 tool 消息（tool 必须跟在带 tool_calls 的 assistant 后面）
- `compact_tool_result(text)`：超 750 tok 的结果留头 2/3 留尾 1/3，
  中间插「已压缩 X → Y tok」标记
- 摘要也算 token，逐条剔除直到装下为止

**Guard（熔断六类）** —— 见 5.2 节阈值表。接口：

```python
guard.before_tool(name, args) -> str|None   # 拦截原因，None=放行
guard.after_tool(name, args, result, denied) # 记账
guard.check_stop() -> str|None              # 每步结束问一次，非空=该收手
guard.report(reason) -> str                 # 人话收尾报告
```

**CompletionJudge（完成度）** —— 两层：

1. `heuristic(state, recent_results)`：纯规则快判 0.1~0.95（有错误词扣分），每步更新
2. `verify(messages, tools)`：出口探针。不问「做完了吗」（同模型自评等于自证），
   要求模型给出一只能**机械证明完成**的工具调用（读回文件/跑测试/列目录），
   内核真执行、`is_failure` 判结果；失败打回返工（预算 1 次）；纯问答允许
   `NOVERIFY:` 放行但不给「已验证」标记

**safety / rules（安全双闸）**

- safety：模式匹配筛查输入（覆盖规则/改写身份/套提示词/危险品），命中不拒绝对话，
  而是往系统提示追加硬提醒 + 写审计 jsonl
- rules：路径白名单 + 危险操作清单，`check(name, args) -> 拒绝原因`。
  真正的兜底是这里——模型就算被注入说服，写盘照样拦

**persistence（会话存档）**

- 格式：`.agent/sessions/{id}.jsonl`，一行一事件（meta/msg/title），**追加写**
- 惰性建文件（没聊过不留空壳）；首条用户消息自动取标题（前 24 字，不调模型）
- 恢复三修正：剥掉尾部配不上对的 tool/assistant（崩溃残留）；丢弃存档里的
  system 用当前 `system_prompt()` 重建（MCP 工具清单可能变了）；steps/tool_calls
  从消息重算
- 依赖「铁律 2」：append 单口 + observer，所以插话、内核注入一条不漏

**loop（双模式循环）**

```python
class Agent:
    def run(session, sink, safe, cancel) -> str
    def _run_react(run)    # 边做边看：问模型 -> 要工具就执行回灌 -> 再问
    def _run_plan(run)     # 先出 JSON 步骤清单（≤6 步，必须有可验证的 done_when）
                           # -> 逐步执行 -> 卡住重规划（≤1 次）
```

- 模式只是策略：`_Run` 运行时（状态/熔断/插话/执行/收尾）两模式共用，
  加新模式 = 写一个 `_run_xxx`，公共部分不动
- 插话在「下一次模型请求前」这个安全点转成消息，绝不插在
  assistant.tool_calls 和它的 tool 结果之间（会断配对）
- 插话触发重规划时重置「原地打转」计数（方向变了旧计数不该压着）

### 4.5 交互层

- **Sink 事件协议**：内核只调 `sink.text/tool_call/tool_result/step_done/state/
  notice/plan/error/confirm` 九个方法。CLI 打终端、GUI 投队列由实现决定。
  **新界面 = 实现一个 Sink，内核零改动**
- **GUI 模式**：内核跑后台线程 → 事件进 `queue.Queue` → UI 线程 `after()` 泵。
  tkinter 单线程模型下这是唯一不冻界面的写法
- 状态快照（TaskState.snapshot()）每步发：step/tool_calls/context_tokens/
  completion/verified/note——界面只显示，不反查内核

### 4.6 扩展层

**MCP（外部工具进程，值最多也最贵）**：

- client：stdio JSON-RPC，注意三条保命规则——stderr 必须抽干（不读会堵死管道）、
  `poll()` 返回 None 不代表活着（判活要 poll + 读线程一起看）、请求要带超时
- **token 四层压缩**（这是接 MCP 最贵的坑，37 工具原始 6313 tok → 压完 1480，省 77%）：
  ① 描述按 token 截断 ② 输入 schema 压缩（去 default/示例，只留必填）③ 总预算
  1500 tok 超了降级成文字摘要 ④ 元工具 `mcp_call(server, tool, args)`——清单里
  只放一个工具，调用时再查真 schema
- **懒启动**：配置解析时不启子进程，工具清单缓存到 `.agent/mcp-cache/`，
  不接 MCP 的人零开销
- 错误永转 `[ERR]` 文本，永不抛

**插件**：zip 包（GitHub release 直装）+ 入口注册。**技能**：本地 md 提示词包 +
`use_skill` 工具。两者与 MCP 的分工：插件=代码能力，技能=流程知识，MCP=外部服务。

---

## 5. 关键设计决策与依据

### 5.1 失败判定三层（由确定到模糊）

1. 工具主动打 `[ERR]/[OK]` 前缀 → 直接采信，零歧义
2. 无前缀（第三方插件）→ 只看**开头 200 字符**的关键词兜底（防正文误伤）
3. 都没有 → 当作成功，交给模型自己判断

### 5.2 熔断阈值表（可直接抄）

| 限额 | 默认 | 依据 | 类型 |
| --- | --- | --- | --- |
| max_same_call | 3 | 相同「工具+参数」第二次信息增益≈0；3 是容忍有外部状态的工具 | 信息论 |
| max_call_failures | 2 | 瞬时失败值得再试 1 次，确定性失败第 2 次就确认 = 1 容错 + 1 确认 | 失败分类 |
| max_unproductive | 4 | 连续相同结果 ≥3 次才算「原地打转」+1 次容错 | 推导 |
| max_fail_streak | 6 | 一轮并行 2~3 工具，两轮全败 = 工具链整体不可用 | 启发式 |
| max_tool_calls | 40 | 40 × 单步 ~1.8k tok ≈ 预算 32k 的 2.2 倍，再多挤成摘要 | 预算推导 |
| max_seconds | 300 | 防挂死保险丝：≥3 次最慢工具（60s），实测正常任务几秒结束 | 最坏情况 |

**校准方法**：写一个 bench 脚本跑 4 类代表任务 + 统计 N 个历史会话的调用分布，
用实测分布给上限留 6 倍余量；再用 `test_limits.py` 锁自洽关系
（`max_tool_calls × 单步估算 ≤ 预算 × 2.5`、`max_seconds ≥ 3 × 最慢工具`、
`max_tool_calls ≥ max_steps`）。**40 和 300 是兜底不是调优，天天生效的是两个小的。**

### 5.3 token 预算分层

| 去向 | 预算 | 手段 |
| --- | --- | --- |
| 系统提示（规则注入） | ~576 tok（原 2088） | 规则外置 rules.md，按章节预算分配 + mtime 缓存 |
| MCP 工具 schema | 1500 tok | 四层压缩（见 4.6） |
| 对话上下文 | 32000 tok | 裁剪 + 摘要折叠 |
| 单条工具结果 | 750 tok | 头尾保留、中间折叠 |

### 5.4 完成度为什么必须「工具反证」

同一个模型判断自己刚做的事 = 自证。它可能刚写坏一个文件然后自信输出 DONE。
验证探针把「做完了吗」变成「拿什么证明」：模型给工具调用 → 内核真执行 →
用失败判定说话。已知盲区（模型可能给不相干的验证调用）如实写进文档，
不要假装解决。

---

## 6. 质量体系（零第三方依赖怎么做测试）

1. **每模块一个可独立运行的测试文件**：`python tests/test_xxx.py` 直接跑，
   输出 PASS/FAIL 明细，main() 返回码非零即失败。不需要 pytest。
2. **假模型模式**：`ScriptLLM` 按脚本回放（回复序列 + 探针回复），内核测试
   不碰网络、不花钱、毫秒级。探针识别用「最后一条用户消息 == VERIFY_PROMPT」
   判断，不要用 `tools is None`（探针现在是带工具的）。
3. **断言锁一致性**：跨模块约定（如标记常量）曾经各自定义，用测试断言锁；
   收拢到单一来源后锁自然解除。
4. **GUI 冒烟测试**：真实窗口 + 假模型 + `wait_until_idle`，短暂弹窗自动关闭。
   副作用（存档目录）patch 到临时目录防污染。
5. **selftest**：一条命令无 Key 自检整条链路（模型→工具→回灌→答复），交付前必跑。
6. **bench 校准**：阈值不是一次定的，bench 脚本随时复测（几分钱模型费）。

---

## 7. 实施路线图（七步 MVP → 完整）

| 步 | 交付物 | 验收标准 | 量级 |
| --- | --- | --- | --- |
| 1 | config + tokens + markers 叶子层 | import 图无环；估算单测过 | 0.5 天 |
| 2 | tools 层 + 注册表 + truncate | execute 永不抛；边界拦截生效 | 1 天 |
| 3 | model 层（先只做 OpenAI Chat 协议） | 流式 + 重试 + 超时可用 | 1 天 |
| 4 | 内核最小闭环：Session + loop(ReAct) + Sink(CLI) | 假模型全链路跑通 | 1 天 |
| 5 | Guard 六类熔断 + safety + rules | 假模型注入跑飞场景全被拦 | 1 天 |
| 6 | ContextManager 预算裁剪 | 32k 预算下长任务不死 | 1 天 |
| 7 | GUI（Sink + 事件泵 + 最小窗口） | 流式渲染不冻界面 | 1~2 天 |

**MVP 完成后按需补**（每项独立，顺序无强依赖）：
Plan 模式(1d) → 验证式自检(1d) → 持久化+历史(1.5d) → MCP(2d) →
插件/技能(1d) → 多协议适配器(1d/个) → 文档解析(1d) → 主题/DPI(1d)

总计：MVP 约 6~7 天，完整对齐本项目约 15~18 天（单人）。

---

## 8. 踩坑清单精选（原文 25 条，这里挑最通用的 12 条）

1. **默认参数陷阱**：`def f(bg: str = theme.BG)` 在导入时求值，换主题后全错。
   统一 `bg: str | None = None` + 函数体 `bg or theme.XXX`
2. **tkinter 的 `Misc._w`**：自定义控件别用 `self._w/_h` 命名，会和库属性打架
3. **`Expand` 占位控件最后 pack**：否则底栏被挤成 1px
4. **DPI 感知必须在 `tk.Tk()` 之前**：`SetProcessDpiAwareness(2)` 放 main 开头
5. **heredoc 会吃转义**：多行代码改动用编辑工具，别往 shell 里塞
6. **流式重试的重复输出**：已经吐出文字就不重试
7. **MCP stderr 必须抽干**；`poll()` None ≠ 活着
8. **工具结果按字符截是错的**：中文场景差四倍
9. **探针带工具后，假模型识别方式要跟着改**（改接口时全项目的假桩要过一遍）
10. **持久化标题派生要在写 meta 之前**，否则标题永远为空
11. **孤儿 tool 消息**：裁剪/恢复时 tool 必须跟着它的 assistant 父消息，
    否则严格的服务端直接报错
12. **收尾报告优先于异常**：任何终止路径都要给人话，这是 Agent 不是脚本

---

## 9. 已知留白（诚实清单）

- **边界是启发式不是沙箱** → **已补进程级沙箱**（`tools/sandbox.py`）：Job Object
  进程树隔离（kill-on-close + 内存上限）+ `run_python` 子进程守卫（patch
  open/os/shutil/subprocess/socket，按读/写白名单拦截）+ shell 读目标检查。
  残余盲区：C 扩展级逃逸（ctypes）与 `import 任意 .py` 未检查，常规绕过已封死。
  实现要点：守卫源码先 import 完再 patch；sys.prefix 与临时目录天然放行；
  开关关闭时不注入守卫（否则白名单环境变量缺失会把一切拦死）。
- **验证探针的相关性盲区**：模型可能给不相干的验证调用，is_failure 只判成败
- **无子 Agent 编排、无跨会话记忆、无 Agent 级评测基准**——接口位都留了
  （tools 注册表可挂 spawn_subagent；sessions jsonl 可做训练/评测数据），没做完
- **编辑检查点已补**（`tools/checkpoints.py`）：write_file 覆盖前快照到
  `.agent/checkpoints/`（环形 20 次），GUI「↩ 撤销写入」按钮 + CLI `/undo`，
  链式回退；写入失败不留伪检查点，二进制文件不给假保证。run_shell 重定向
  写文件不走快照，是已知盲区。
- **GitHub 装技能已补**：共享下载层 `miniagent/fetch.py`（download / github_zip /
  extract_zip / zip_prefix / zip_list），插件与技能共用；`skills.install()`
  支持仓库 / 子目录 / 单 .md / 本地路径，子目录扁平化防同名，剥 zip 顶层目录；
  UI 在插件窗口加「装为技能」（确认话术强调纯 md 无代码风险），CLI 加 `/skill`。

---

## 附：一条最小的启动顺序（给动手的人）

```bash
# 第 1 天你只需要这些文件能跑起来：
config.py → tokens.py → markers.py → tools/{_util,filesystem}.py
         → model/{wire,client,protocols/openai_chat}.py
         → core/{session,context,guard}.py → core/loop.py → ui/console.py
# 加一个 .env 写 DEEPSEEK_API_KEY=sk-xxx，就能对话了。
# 之后每加一层，测试文件跟着加一个，断言数就是你的人数。
```
