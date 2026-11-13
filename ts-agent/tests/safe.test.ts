/** 安全模式：危险工具确认、拒绝后不执行、无确认实现视为拒绝、关掉就直通。 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import { Agent, type Sink, type Streamable, type TaskState } from "../src/core/loop.ts";
import { Session } from "../src/core/session.ts";
import { BASE_DIR } from "../src/config.ts";
import type { Message, ToolCall } from "../src/wire.ts";

const PROBE = join(BASE_DIR, ".agent", "safe_probe.txt");

function call(name: string, args: Record<string, unknown>, id = "c1"): ToolCall {
  return { id, type: "function", function: { name, arguments: JSON.stringify(args) } };
}

/** 先要一次写文件，第二轮收尾 */
class WriteLLM implements Streamable {
  readonly label = "write";
  readonly model = "write";
  turns = 0;
  async stream(): Promise<{ text: string; calls: ToolCall[] }> {
    this.turns += 1;
    if (this.turns === 1) {
      return { text: "", calls: [call("write_file", { path: PROBE, content: "写进来了" })] };
    }
    return { text: "完事", calls: [] };
  }
}

class Sink2 implements Sink {
  asked: Array<{ tool: string; args: string }> = [];
  results: string[] = [];
  private readonly answer: boolean;
  readonly hasConfirm: boolean;

  constructor(answer: boolean, hasConfirm = true) {
    this.answer = answer;
    this.hasConfirm = hasConfirm;
  }

  text(): void {}
  toolCall(): void {}
  toolResult(result: string): void {
    this.results.push(result);
  }
  state(_s: TaskState): void {}
  error(): void {}
  confirm?: (tool: string, args: string) => Promise<boolean>;
}

test("安全模式：用户同意后才真的写", async () => {
  rmSync(PROBE, { force: true });
  const sink = new Sink2(true);
  sink.confirm = async (tool, args) => {
    sink.asked.push({ tool, args });
    return sink.answer;
  };

  const session = new Session();
  session.addUser("写个探针文件");
  await new Agent(new WriteLLM(), { maxSteps: 4, safe: true, verify: false }).run(session, sink);

  assert.equal(sink.asked.length >= 1, true, "危险工具必须先问");
  assert.equal(sink.asked[0]?.tool, "write_file");
  assert.equal(existsSync(PROBE), true, "同意后要真的写");
  rmSync(PROBE, { force: true });
});

test("安全模式：拒绝后不执行，且把拒绝回灌给模型", async () => {
  rmSync(PROBE, { force: true });
  const sink = new Sink2(false);
  sink.confirm = async (tool, args) => {
    sink.asked.push({ tool, args });
    return false;
  };

  const session = new Session();
  session.addUser("写个探针文件");
  const answer = await new Agent(new WriteLLM(), { maxSteps: 4, safe: true, verify: false }).run(
    session,
    sink,
  );

  assert.equal(existsSync(PROBE), false, "拒绝后绝不能落盘");
  const toolMsg = session.messages.find((m) => m.role === "tool");
  assert.ok(String(toolMsg?.content).includes("[ERR]"), String(toolMsg?.content));
  assert.ok(String(toolMsg?.content).includes("拒绝"), String(toolMsg?.content));
  assert.ok(answer.includes("完事"), "模型要能继续收尾");
});

test("安全模式：Sink 没实现 confirm 时按拒绝处理", async () => {
  rmSync(PROBE, { force: true });
  const sink = new Sink2(true, false); // 不挂 confirm

  const session = new Session();
  session.addUser("写个探针文件");
  await new Agent(new WriteLLM(), { maxSteps: 4, safe: true, verify: false }).run(session, sink);

  assert.equal(existsSync(PROBE), false, "没实现确认 = 拒绝");
});

test("安全模式关掉时危险工具直通", async () => {
  rmSync(PROBE, { force: true });
  const sink = new Sink2(false);
  sink.confirm = async (tool, args) => {
    sink.asked.push({ tool, args });
    return false;
  };

  const session = new Session();
  session.addUser("写个探针文件");
  await new Agent(new WriteLLM(), { maxSteps: 4, safe: false, verify: false }).run(session, sink);

  assert.equal(sink.asked.length, 0, "非安全模式不该打扰用户");
  assert.equal(existsSync(PROBE), true, "应该直接写");
  rmSync(PROBE, { force: true });
});

test("读类工具在安全模式下不问", async () => {
  const asked: string[] = [];
  class ReadLLM implements Streamable {
    readonly label = "read";
    readonly model = "read";
    turns = 0;
    async stream(_m: Message[]): Promise<{ text: string; calls: ToolCall[] }> {
      this.turns += 1;
      if (this.turns === 1) return { text: "", calls: [call("list_dir", { path: "." })] };
      return { text: "看完了", calls: [] };
    }
  }
  const sink = new Sink2(true);
  sink.confirm = async (tool) => {
    asked.push(tool);
    return true;
  };

  const session = new Session();
  session.addUser("看看目录");
  await new Agent(new ReadLLM(), { maxSteps: 4, safe: true, verify: false }).run(session, sink);
  assert.deepEqual(asked, [], "只读操作不该弹确认");
});
