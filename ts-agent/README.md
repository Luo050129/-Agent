# 墟 · TypeScript 版

Python 版的同架构重写。保留了「零运行时依赖」这个底线：**不装任何 npm 包**，
用 Node 22 原生的类型剥离直接跑 `.ts`，`node --test` 做测试。

```bash
node --version          # 需要 >= 22.6
npm run selftest        # 无 Key 自检整条链路
npm test                # 38 项测试

# 用起来（先配 Key：环境变量或 ts-agent/.env）
node --experimental-strip-types src/cli/main.ts "统计这个目录有多少文件"
node --experimental-strip-types src/cli/main.ts            # 交互模式
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
| `core/session.py` | `src/core/session.ts` | ✅ 完成（单口入库 + 观察者） |
| `core/guard.py` | `src/core/guard.ts` | ✅ 完成（六类熔断） |
| `core/context.py` | `src/core/context.ts` | ✅ 完成（预算折叠 + 工具结果压缩 + 孤儿修复） |
| `core/prompt.py` | `src/core/prompt.ts` | ✅ 完成 |
| `core/loop.py`（ReAct） | `src/core/loop.ts` | ✅ 完成 |
| `core/loop.py`（Plan 模式） | — | ⬜ 待做 |
| `core/completion.py` 验证式自检 | — | ⬜ 待做 |
| `core/persistence.py` 会话存档 | — | ⬜ 待做 |
| `tools/sandbox.py` 沙箱 | — | ⬜ 待做（Node 无 Job Object，需另想） |
| `tools/checkpoints.py` 撤销 | — | ⬜ 待做 |
| `mcp/*` | — | ⬜ 待做（可用官方 SDK） |
| `plugins/*` `skills/*` | — | ⬜ 待做 |
| `ui/*` tkinter 界面 | `src/cli/*` 终端 | 部分：终端输出已通，TUI 待做 |
| 24 个测试套件 | 4 个套件 38 项 | 部分 |

## 语言差异带来的设计调整

1. **`run_python` → `run_node`**：用 Node 自己算数，不再依赖本机装没装 Python。
2. **没有 `ctypes` 直调 Win32**：Python 版的沙箱用 Job Object 做进程树隔离，
   Node 侧要等价能力得走 FFI 原生绑定（等于引入编译期依赖），所以沙箱留到后面单独设计。
3. **超时杀树改用 `taskkill /T /F`**（Windows）/ `SIGKILL`（POSIX）—— 效果等价，实现不同。
4. **配置目录仍叫 `.agent`**，Key 仍然「默认没有，用户自己配」：`src/config.ts` 一个环境变量都没有内置值。

## 测试设计

假模型注入（`ScriptLLM implements Streamable`）驱动真实循环，不碰网络：

```
tests/tokens.test.ts    估算与截断（8 项）
tests/guard.test.ts     失败判定、重复调用、连续失败、人话报告（6 项）
tests/kernel.test.ts    闭环、越界拦截、熔断、步数上限、预算折叠（8 项）
tests/protocol.test.ts  请求形状、SSE 分块、tool_calls 拼装（7 项）
tests/tools.test.ts     执行器容错、边界、命令执行（9 项）
```
