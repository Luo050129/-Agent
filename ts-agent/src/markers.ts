/**
 * 全包统一的工具结果标记。对应 Python 版的 miniagent/markers.py。
 *
 * 约定（三层判定的第 1 层，由 core/guard.classify 采信）：
 *   工具主动失败   → 返回值前面加 ERR
 *   成功但含歧义词 → tagResult 补 OK 豁免
 *   干净的成功     → 不加前缀
 *
 * 这里零依赖：tools、core、mcp 三层都要用，谁都能 import 它。
 */

/** 带尾随空格的消息前缀：工具拼结果时用 */
export const ERR = "[ERR] ";
export const OK = "[OK] ";

/** 裸标记：guard 做 startsWith 判定用，不带空格更宽容 */
export const ERR_TAG = "[ERR]";
export const OK_TAG = "[OK]";
