/**
 * 命令行入口。对应 Python 版的 agent.ts。
 *
 * 用法：
 *   node --experimental-strip-types src/cli/main.ts "统计这个项目有多少文件"
 *   node --experimental-strip-types src/cli/main.ts -p "..." --model deepseek-chat
 *   node --experimental-strip-types src/cli/main.ts --selftest
 */

import { createInterface } from "node:readline/promises";
import { ConfigError, loadEnv } from "../config.ts";
import { Agent, type Streamable } from "../core/loop.ts";
import { Session } from "../core/session.ts";
import { systemPrompt } from "../core/prompt.ts";
import { LLM } from "../model/client.ts";
import type { Message, ToolCall } from "../wire.ts";
import { ConsoleSink, color } from "./sink.ts";

interface Args {
  prompt?: string;
  model?: string;
  baseUrl?: string;
  apiKey?: string;
  maxSteps: number;
  selftest: boolean;
  help: boolean;
}

function parseArgs(argv: string[]): Args {
  const args: Args = { maxSteps: 20, selftest: false, help: false };
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
      case "--selftest":
        args.selftest = true;
        break;
      case "-h":
      case "--help":
        args.help = true;
        break;
      default:
        // 不是选项就当一次性提问
        if (!flag.startsWith("-") && args.prompt === undefined) args.prompt = flag;
        break;
    }
  }
  return args;
}

/** 自检用假模型：第一轮要工具，第二轮给答复，不碰网络 */
class ScriptedLLM implements Streamable {
  readonly label = "fake-scripted";
  readonly model = "fake-scripted";
  turns = 0;

  async stream(
    messages: Message[],
    _tools: Array<Record<string, unknown>> | null,
    onText?: (piece: string) => void,
  ): Promise<{ text: string; calls: ToolCall[] }> {
    this.turns += 1;
    if (!messages.some((m) => m.role === "tool")) {
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
    const text = "自检通过：模型→工具→回灌→答复 链路正常。";
    onText?.(text);
    return { text, calls: [] };
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

async function main(): Promise<number> {
  loadEnv();
  const args = parseArgs(process.argv.slice(2));

  if (args.help) {
    console.log(
      [
        "用法：node --experimental-strip-types src/cli/main.ts [选项] [问题]",
        "",
        "  -p, --prompt <文本>   一次性执行",
        "  -m, --model <名字>    模型名",
        "      --base-url <地址> OpenAI 兼容接口地址",
        "      --api-key <key>   直接传 Key（默认读环境变量或 .env）",
        "      --max-steps <n>   单轮最大步数，默认 20",
        "      --selftest        无 Key 自检整条链路",
      ].join("\n"),
    );
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

  const session = new Session(systemPrompt());
  const sink = new ConsoleSink();
  const agent = new Agent(llm, { maxSteps: args.maxSteps });

  if (args.prompt) {
    session.addUser(args.prompt);
    const answer = await agent.run(session, sink);
    sink.printState();
    return answer ? 0 : 1;
  }

  console.log(
    `${color.cyan("墟")}  模型 ${llm.label}  （exit 退出，/clear 清空上下文）`,
  );
  const rl = createInterface({ input: process.stdin, output: process.stdout });
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
      if (input === "/clear") {
        session.reset();
        console.log(color.dim("上下文已清空。"));
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
