/**
 * 命令行入口。对应 Python 版的 agent.ts。
 *
 * 用法：
 *   node --experimental-strip-types src/cli/main.ts "统计这个项目有多少文件"
 *   node --experimental-strip-types src/cli/main.ts --mode plan "..."
 *   node --experimental-strip-types src/cli/main.ts --resume last
 *   node --experimental-strip-types src/cli/main.ts --selftest
 *
 * 交互里：/undo 撤销写入、/clear 开新会话、/exit 退出。
 */

import { createInterface } from "node:readline/promises";
import { ConfigError, loadEnv } from "../config.ts";
import { normalizeMode } from "../core/plan.ts";
import { SessionStore } from "../core/persistence.ts";
import { searchSessions } from "../core/search.ts";
import { Agent, type Streamable } from "../core/loop.ts";
import { Session } from "../core/session.ts";
import { systemPrompt } from "../core/prompt.ts";
import { LLM } from "../model/client.ts";
import { undoLast } from "../tools/checkpoints.ts";
import type { Message, ToolCall } from "../wire.ts";
import { ConsoleSink, color } from "./sink.ts";
import { TuiSink } from "./tui.ts";
import { mcpManager } from "../mcp/manager.ts";
import { loadTools as loadPluginTools } from "../plugins.ts";
import { setExtraTools } from "../tools/index.ts";

interface Args {
  prompt?: string;
  model?: string;
  baseUrl?: string;
  apiKey?: string;
  maxSteps: number;
  mode: string;
  resume?: string;
  persist: boolean;
  tui: boolean;
  safe: boolean;
  yes: boolean;
  selftest: boolean;
  help: boolean;
}

function parseArgs(argv: string[]): Args {
  const args: Args = { maxSteps: 20, mode: "react", persist: true, tui: false, safe: false, yes: false, selftest: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i] as string;
    const next = (): string => {
      const value = argv[i + 1];
      if (value === undefined) throw new ConfigError(`${flag} 缺少参数值`);
      i += 1;
      return value;
    };
    switch (flag) {
      case "-p":
      case "--prompt":
        args.prompt = next();
        break;
      case "-m":
      case "--model":
        args.model = next();
        break;
      case "--base-url":
        args.baseUrl = next();
        break;
      case "--api-key":
        args.apiKey = next();
        break;
      case "--max-steps":
        args.maxSteps = Number.parseInt(next(), 10) || 20;
        break;
      case "--mode":
        args.mode = next();
        break;
      case "--resume":
        args.resume = argv[i + 1]?.startsWith("-") ? "last" : next();
        break;
      case "--no-persist":
        args.persist = false;
        break;
      case "--tui":
        args.tui = true;
        break;
      case "--safe":
        args.safe = true;
        break;
      case "--yes":
        args.yes = true;
        break;
      case "--selftest":
        args.selftest = true;
        break;
      case "-h":
      case "--help":
        args.help = true;
        break;
      default:
        if (!flag.startsWith("-") && args.prompt === undefined) args.prompt = flag;
        break;
    }
  }
  return args;
}

/** 自检用假模型：第一轮要工具，第二轮给答复，验证探针回 NOVERIFY */
class ScriptedLLM implements Streamable {
  readonly label = "fake-scripted";
  readonly model = "fake-scripted";
  turns = 0;

  async stream(
    messages: Message[],
    tools: Array<Record<string, unknown>> | null,
    onText?: (piece: string) => void,
  ): Promise<{ text: string; calls: ToolCall[] }> {
    // 验证探针带工具、且最后一条是 VERIFY 提示
    const last = messages[messages.length - 1];
    if (tools && typeof last?.content === "string" && last.content.startsWith("只做一件事")) {
      return { text: "NOVERIFY: 自检不涉及产物", calls: [] };
    }
    if (Array.isArray(messages) && messages.some((m) => m.role === "tool")) {
      const text = "自检通过：模型→工具→回灌→答复 链路正常。";
      onText?.(text);
      return { text, calls: [] };
    }
    this.turns += 1;
    return {
      text: "",
      calls: [
        {
          id: "call_selftest",
          type: "function",
          function: { name: "list_dir", arguments: JSON.stringify({ path: "." }) },
        },
      ],
    };
  }
}

async function selftest(): Promise<boolean> {
  const llm = new ScriptedLLM();
  const session = new Session(systemPrompt());
  session.addUser("看看当前目录");
  const sink = new ConsoleSink();
  const answer = await new Agent(llm, { maxSteps: 5 }).run(session, sink);
  sink.printState();

  const ok = answer.includes("自检通过") && session.toolCalls >= 1;
  console.log(ok ? color.green("[selftest] 通过") : color.red("[selftest] 失败"));
  return ok;
}

function printHelp(): void {
  console.log(
    [
      "用法：node --experimental-strip-types src/cli/main.ts [选项] [问题]",
      "",
      "  -p, --prompt <文本>   一次性执行",
      "  -m, --model <名字>    模型名",
      "      --base-url <地址> OpenAI 兼容接口地址",
      "      --api-key <key>   直接传 Key（默认读环境变量或 .env）",
      "      --max-steps <n>   单轮最大步数，默认 20",
      "      --mode <react|plan>  边做边看 / 先出计划，默认 react",
      "      --resume [id]     恢复历史会话，省略 id 就是最近一次",
      "      --no-persist      本次对话不落盘",
      "      --tui             富终端界面（实时状态行 + 计划进度）",
      "      --safe            安全模式：写文件/跑命令前先确认",
      "      --yes             跳过安全确认（脚本/自动化用）",
      "      --selftest        无 Key 自检整条链路",
      "",
      "交互命令：/undo 撤销写入 · /find 关键词 搜历史 · /sessions 列存档 · /clear 新会话 · /exit 退出",
    ].join("\n"),
  );
}

/** 建会话：需要存档就挂上，--resume 则从磁盘恢复 */
function openSession(args: Args): { session: Session; store: SessionStore | null } {
  if (!args.persist) return { session: new Session(systemPrompt()), store: null };

  if (args.resume) {
    const store = SessionStore.resume(args.resume);
    const session = store.load();
    console.log(color.dim(`已恢复会话 ${store.sessionId}（${store.title || "未命名"}）`));
    return { session, store };
  }

  const store = new SessionStore();
  const session = new Session(systemPrompt());
  store.attach(session);
  return { session, store };
}

async function main(): Promise<number> {
  loadEnv();
  const args = parseArgs(process.argv.slice(2));

  if (args.help) {
    printHelp();
    return 0;
  }
  if (args.selftest) return (await selftest()) ? 0 : 1;

  let llm: LLM;
  try {
    llm = new LLM(args.model, { baseUrl: args.baseUrl, apiKey: args.apiKey });
  } catch (err) {
    if (err instanceof ConfigError) {
      console.error(color.red(err.message));
      return 2;
    }
    throw err;
  }

  const mode = normalizeMode(args.mode);

  // 先装扩展再建会话：技能清单和 MCP 工具都要进系统提示
  const mcpTools = await mcpManager.load().catch(() => []);
  const pluginTools = await loadPluginTools();
  const extra = setExtraTools([...mcpTools, ...pluginTools.schemas], pluginTools.handlers);
  if (mcpTools.length > 0 || pluginTools.loaded.length > 0) {
    console.log(
      color.dim(
        `已加载 ${mcpTools.length} 个 MCP 工具 · 插件 ${pluginTools.loaded.length} 个 · 技能 ${extra} 项`,
      ),
    );
  }
  for (const bad of pluginTools.failed) {
    console.log(color.yellow(`插件 ${bad.name} 加载失败：${bad.error}`));
  }

  let { session, store } = openSession(args);
  const sink = args.tui ? new TuiSink(llm.label, mode) : new ConsoleSink();
  // --yes 明确表示「我信任它」，覆盖 --safe
  const safe = args.safe && !args.yes;
  const agent = new Agent(llm, { maxSteps: args.maxSteps, mode, safe });

  if (args.prompt) {
    session.addUser(args.prompt);
    const answer = await agent.run(session, sink);
    sink.printState();
    return answer ? 0 : 1;
  }

  const archive = store ? color.dim(` · 存档 ${store.sessionId}`) : "";
  console.log(`${color.cyan("墟")}  模型 ${llm.label} · 模式 ${mode}${archive}`);
  console.log(
    color.dim(
      `（exit 退出${safe ? " · 安全模式已开" : ""} · /undo 撤销 · /find 搜历史 · /sessions 存档 · /clear 新会话）`,
    ),
  );

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  sink.attachPrompt(rl);
  try {
    for (;;) {
      let input = "";
      try {
        input = (await rl.question(`\n${color.green("你 ›")} `)).trim();
      } catch {
        console.log("\n再见。");
        return 0;
      }
      if (!input) continue;
      if (["exit", "quit", "/exit"].includes(input.toLowerCase())) {
        console.log("再见。");
        return 0;
      }
      if (input === "/undo") {
        console.log(color.dim(undoLast()));
        continue;
      }
      if (input.startsWith("/find ")) {
        const keyword = input.slice(6).trim();
        const hits = keyword ? searchSessions(keyword, { limit: 12 }) : [];
        if (hits.length === 0) {
          console.log(color.dim("没有命中。换个关键词，或先多聊几轮攒点历史。"));
        } else {
          hits.forEach((hit) => {
            const role = hit.role === "user" ? "你" : hit.role === "assistant" ? "助手" : hit.role;
            console.log(`${color.cyan(hit.sessionId)} ${color.dim(`[${role}]`)} ${hit.snippet}`);
          });
          console.log(color.dim(`共 ${hits.length} 条 · 用 --resume <会话号> 打开`));
        }
        continue;
      }
      if (input === "/sessions") {
        const items = SessionStore.list();
        if (items.length === 0) {
          console.log(color.dim("还没有存档。"));
        } else {
          items.slice(0, 10).forEach((item) => {
            const when = new Date(item.updated).toLocaleString("zh-CN", { hour12: false });
            console.log(`${color.cyan(item.id)} ${color.dim(when)} ${item.title} ${color.dim(`(${item.turns} 轮)`)}`);
          });
        }
        continue;
      }
      if (input === "/clear") {
        // 旧存档留着，新开一份
        if (args.persist) {
          const fresh = new SessionStore();
          session = new Session(systemPrompt());
          fresh.attach(session);
          store = fresh;
        } else {
          session.reset();
        }
        console.log(color.dim("已开新会话，旧的留在存档里。"));
        continue;
      }

      session.addUser(input);
      process.stdout.write(`${color.cyan("助手 ›")} `);
      await agent.run(session, sink);
      sink.printState();
    }
  } finally {
    rl.close();
  }
}

main()
  .then((code) => process.exit(code))
  .catch((err: Error) => {
    console.error(color.red(`未预期错误：${err.name}: ${err.message}`));
    process.exit(1);
  });
