/** 内置工具定义汇总。单独成文件，避免 index ↔ 各工具模块循环依赖。 */

import * as filesystem from "./filesystem.ts";
import * as runtime from "./runtime.ts";

const MODULES = [runtime, filesystem];

export const TOOL_SCHEMAS: Array<Record<string, unknown>> = MODULES.flatMap((m) => m.schemas);
