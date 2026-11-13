# 墟 · TypeScript 版

Python 版的同架构重写。保留了「零运行时依赖」这个底线：**不装任何 npm 包**，
用 Node 22 原生的类型剥离直接跑 `.ts`，`node --test` 做测试。

```bash
node --version          # 需要 >= 22.6
npm run selftest        # 无 Key 自检整条链路
npm test                # 113 项测试（12 个套件）

# 用起来（先配 Key：环境变量或 ts-agent/.env）
node --experimental-strip-types src/cli/main.ts "统计这个目录有多少文件"
node --experimental-strip-types src/cli/main.ts --mode plan "把项目文档整理一遍"
node --experimental-strip-types src/cli/main.ts --resume   # 接着上次的会话
node --experimental-strip-types src/cli/main.ts            # 交互模式（/undo /clear）
```

## 为什么不用 npm 依赖

| 做法 | 代价 |
| --- | --- |
| 装 tsx / ts-node 跑 TS | 引入几十个包的依赖树 |
| 用 **Node 22 的类型剥离** | 零依赖，但只能用「可擦除语法」（不能 enum / namespace / 构造函数参数属性） |
| 装 vitest 做测试 | 依赖树 + 配置 |
| 用 **`node --test`** | 零依赖，断言用 `node:assert` |

类型系统照样用（写代码时 IDE 就报错），只是不需要编译步骤。想跑类型检查时可选装
`typescript`：`npm i -D typescript && npx tsc --noEmit`（tsconfig 已配好）。

## 与 Python 版的对应关系

| Python | TypeScript | 状态 |
| --- | --- | --- |
| `markers.py` | `src/markers.ts` | ✅ 完成 |
| `tokens.py` | `src/tokens.ts` | ✅ 完成（含二分截断、句末收尾） |
| `wire.py` | `src/wire.ts` | ✅ 完成（统一事件 + SSE 外壳） |
| `protocols/openai_chat.py` | `src/model/protocols/openai.ts` | ✅ 完成 |
| `model/client.py` | `src/model/client.ts` | ✅ 完成（重试/超时/已吐字不重试） |
| `tools/{index,filesystem,runtime}.py` | `src/tools/*.ts` | ✅ 完成（`run_python` → `run_node`） |
| `tools/documents.py` | `src/tools/documents.ts` | ✅ 完成（docx/xlsx/pdf 手写解析，格式见下） |
| `core/session.py` | `src/core/session.ts` | ✅ 完成（单口入库 + 观察者） |
| `core/guard.py` | `src/core/guard.ts` | ✅ 完成（六类熔断） |
| `core/context.py` | `src/core/context.ts` | ✅ 完成（预算折叠 + 工具结果压缩 + 孤儿修复） |
| `core/prompt.py` | `src/core/prompt.ts` | ✅ 完成 |
| `core/loop.py`（ReAct） | `src/core/loop.ts` | ✅ 完成 |
| `core/loop.py`（Plan 模式） | `src/core/plan.ts` + loop 内 runPlan | ✅ 完成（含卡住重规划一次） |
| `core/completion.py` 验证式自检 | `src/core/completion.ts` | ✅ 完成（工具反证 + NOVERIFY 放行） |
| `core/persistence.py` 会话存档 | `src/core/persistence.ts` | ✅ 完成（JSONL + 崩溃修复 + --resume） |
| `tools/sandbox.py` 沙箱 | `src/tools/sandbox.ts` + `guard-prelude.mjs` | ✅ 完成（三层组合，见下） |
| `tools/checkpoints.py` 撤销 | `src/tools/checkpoints.ts` | ✅ 完成（环形 20 + `/undo`） |
| `mcp/*` | `src/mcp/{client,manager}.ts` | ✅ 完成（零依赖手写 JSON-RPC + 工具清单压缩） |
| `plugins/*` `skills/*` | `src/plugins.ts` · `src/skills.ts` · `src/zip.ts` | ✅ 完成（GitHub 安装，zip 自己解） |
| `ui/*` tkinter 界面 | `src/cli/{sink,tui}.ts` | ✅ 完成（富终端：实时状态行 + 计划进度） |
| 24 个测试套件 | 12 个套件 113 项 | ✅ 覆盖全部核心能力 |

## 语言差异带来的设计调整

1. **`run_python` → `run_node`**：用 Node 自己算数，不再依赖本机装没装 Python。
2. **沙箱换了实现方式**：Python 版用 ctypes 直接调 Windows Job Object（内核级隔离）。
   Node 没有对应 API，试过官方权限模型 `--experimental-permission`，但它在 Windows +
   中文路径下连自身模块加载都会被拒，不可用。最终用三层组合：

   | 层 | 做法 | 强度 |
   | --- | --- | --- |
   | 堆上限 | `--max-old-space-size`（默认 512MB，`XU_SANDBOX_HEAP_MB` 可调） | 硬 |
   | 杀树 | 超时 `taskkill /T /F`（Windows）/ 进程组 `SIGKILL`（POSIX） | 硬 |
   | 守卫 | 子进程内 patch `fs` / `child_process` / `net`，按白名单拦截；`Module._load` 钩子拦住 require | 软（常规代码绕不过） |

   再加静态预检：`import { readFileSync } from "node:fs"` 这类命名导入拿到的是模块快照，
   patch 管不到，所以入口直接拒绝并提示改成默认导入。**诚实结论**：这不如 Python 版的
   内核级隔离，但比不设防强得多，且保住了零依赖。
3. **超时杀树改用 `taskkill /T /F`**（Windows）/ `SIGKILL`（POSIX）—— 效果等价，实现不同。
4. **配置目录仍叫 `.agent`**，Key 仍然「默认没有，用户自己配」：`src/config.ts` 一个环境变量都没有内置值。

## 测试设计

假模型注入（`ScriptLLM implements Streamable`）驱动真实循环，不碰网络：

```
tests/tokens.test.ts       估算与截断（8 项）
tests/guard.test.ts        失败判定、重复调用、连续失败、人话报告（6 项）
tests/kernel.test.ts       闭环、越界拦截、熔断、步数上限、预算折叠、Plan 模式、验证返工（11 项）
tests/protocol.test.ts     请求形状、SSE 分块、tool_calls 拼装（7 项）
tests/tools.test.ts        执行器容错、边界、命令执行（9 项）
tests/plan.test.ts         计划 JSON 抠取、步骤上限、重规划、总结（9 项）
tests/completion.test.ts   快判打分、验证四种结果（10 项）
tests/persistence.test.ts  落盘、恢复、崩溃修复、列表与 resume（7 项）
tests/checkpoints.test.ts  撤销语义、链式回退、环形上限、二进制（7 项）
tests/sandbox.test.ts      白名单、进程/网络拦截、静态预检、堆上限、杀树（13 项）
tests/mcp.test.ts          握手、工具发现、调用、压缩、错误隔离（8 项）
tests/skills.test.ts       zip 读取、技能发现与安装、插件加载（9 项）
tests/documents.test.ts    docx/xlsx/pdf/文本/图片 解析（8 项）
```
