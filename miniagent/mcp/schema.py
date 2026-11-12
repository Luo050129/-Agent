"""MCP 的 token 优化层：把 server 的 JSON Schema 压成能用的最小子集。

MCP server 给的 inputSchema 是完整的 JSON Schema：带 `$schema`、`title`、
`additionalProperties`、上千字的描述。原样发给模型有三个问题：

1. **贵**。一个 server 十五个工具轻松上千 token，每轮请求都发。
2. **有的协议不认**。Gemini 见到 `additionalProperties` 直接报错，我们的
   适配层还得再洗一遍。
3. **长描述反而干扰**。描述动辄一整段，模型抓不住要点。

所以这里做三件事：

    compact_schema()  抠掉没人用的关键字，描述按句子截断
    apply_budget()    按 token 预算排队，超出的降级成一行签名
    signature_line()  降级后的一行摘要，一个工具十来个 token

压缩后仍然保留模型真正需要的：参数名、类型、必填、枚举。
"""

from __future__ import annotations

import json

from ..tokens import clip, estimate_text

# 这些键对模型判断「怎么调」没有帮助，纯占 token
DROP_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "$defs",
        "definitions",
        "title",
        "default",
        "examples",
        "example",
        "deprecated",
        "readOnly",
        "writeOnly",
        "additionalProperties",
        "patternProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contentMediaType",
        "contentEncoding",
        "format",
        "pattern",
        "const",
        "not",
        "if",
        "then",
        "else",
        "allOf",
        "dependentRequired",
        "dependentSchemas",
        "minProperties",
        "maxProperties",
    }
)

TYPE_ALIASES = {"integer": "integer", "number": "number", "boolean": "boolean", "string": "string"}

# 描述按 token 预算卡，不是字符：英文 45 tok ≈ 180 字符，中文 45 tok ≈ 45 字
TOOL_DESC_TOKENS = 45
PARAM_DESC_TOKENS = 18
SIGNATURE_DESC_TOKENS = 12
ENUM_LIMIT = 12
MAX_DEPTH = 3


def _clip(text: str, max_tokens: int) -> str:
    """截到 token 预算内，尽量在句子边界收尾。实现在 tokens.clip。"""
    return clip(text, max_tokens)


def _merge_any_of(node: dict) -> dict:
    """`anyOf: [{type: string}, {type: null}]` 这类取第一个有类型的。"""
    for key in ("oneOf", "anyOf"):
        options = node.get(key)
        if isinstance(options, list) and options:
            candidates = [o for o in options if isinstance(o, dict) and o.get("type") != "null"]
            merged = dict(candidates[0] if candidates else options[0])
            merged.update({k: v for k, v in node.items() if k not in ("oneOf", "anyOf")})
            return merged
    return node


def _resolve_ref(node: dict, defs: dict, depth: int) -> dict:
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    name = ref.rsplit("/", 1)[-1]
    target = defs.get(name)
    if not isinstance(target, dict) or depth >= MAX_DEPTH:
        # 解析不了就把引用当成一个宽松的对象，至少不丢参数名
        return {"type": "object", "description": f"见 {name}"}
    return target


def _compact_prop(node: dict, defs: dict, depth: int = 0) -> dict:
    if not isinstance(node, dict):
        return {"type": "string"}
    node = _resolve_ref(_merge_any_of(node), defs, depth)

    out: dict = {}
    kind = node.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "string")
    if kind:
        out["type"] = TYPE_ALIASES.get(kind, kind)

    desc = node.get("description") or node.get("summary")
    if desc:
        out["description"] = _clip(desc, PARAM_DESC_TOKENS)

    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        out["enum"] = [str(v) for v in enum[:ENUM_LIMIT]]

    if out.get("type") == "array":
        items = node.get("items")
        if isinstance(items, dict):
            out["items"] = _compact_prop(items, defs, depth + 1)
        else:
            out["items"] = {"type": "string"}
    elif out.get("type") == "object" or "properties" in node:
        nested = node.get("properties")
        if isinstance(nested, dict) and nested and depth < MAX_DEPTH:
            out["properties"] = {
                str(name): _compact_prop(value, defs, depth + 1) for name, value in nested.items()
            }
            required = node.get("required")
            if isinstance(required, list) and required:
                out["required"] = [str(r) for r in required]
        else:
            out["type"] = "object"
            out["description"] = out.get("description") or "对象"

    if not out:
        out["type"] = "string"
    return out


def compact_schema(schema: dict | None) -> dict:
    """MCP 的 inputSchema → 扁平的 parameters。"""
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}

    defs: dict = {}
    for key in ("$defs", "definitions"):
        block = schema.get(key)
        if isinstance(block, dict):
            defs.update(block)

    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return {"type": "object", "properties": {}}

    out: dict = {
        "type": "object",
        "properties": {str(k): _compact_prop(v, defs) for k, v in props.items()},
    }
    required = schema.get("required")
    if isinstance(required, list) and required:
        out["required"] = [str(r) for r in required]
    return out


def to_openai_tool(
    name: str,
    description: str,
    input_schema: dict | None,
    *,
    desc_tokens: int = TOOL_DESC_TOKENS,
) -> dict:
    """MCP 工具 → 我们统一使用的 OpenAI 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _clip(description, desc_tokens),
            "parameters": compact_schema(input_schema),
        },
    }


def tool_tokens(tool: dict) -> int:
    """一个工具定义大约占多少 token。"""
    return estimate_text(json.dumps(tool, ensure_ascii=False))


def apply_budget(tools: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
    """按预算取工具，返回 (注入的, 降级的)。

    顺序即优先级（白名单里的先后）。一个都放不下时至少留一个 ——
    让模型看得见、调得动，比一个都没有强。
    """
    if budget <= 0:
        return [], list(tools)

    kept: list[dict] = []
    dropped: list[dict] = []
    used = 0
    for tool in tools:
        cost = tool_tokens(tool)
        if used + cost <= budget:
            kept.append(tool)
            used += cost
        else:
            dropped.append(tool)

    if not kept and tools:  # 单个工具就超预算：留最便宜的那个
        cheapest = min(tools, key=tool_tokens)
        kept.append(cheapest)
        dropped = [t for t in tools if t is not cheapest]
    return kept, dropped


def signature_line(tool: dict) -> str:
    """降级摘要：`工具名(参数1, 参数2) — 一句话`。"""
    fn = tool.get("function", {})
    params = fn.get("parameters", {}) or {}
    names = list((params.get("properties") or {}).keys())
    required = set(params.get("required") or [])
    shown = [f"{n}*" if n in required else n for n in names[:8]]
    if len(names) > 8:
        shown.append("…")
    desc = _clip(fn.get("description", ""), SIGNATURE_DESC_TOKENS)
    arg_text = ", ".join(shown) if shown else "无参数"
    return f"{fn.get('name', '?')}({arg_text})" + (f" — {desc}" if desc else "")


def compact_ratio(raw_tools: list[dict], compacted: list[dict]) -> float:
    """压缩率，给测试和界面显示用。"""
    raw = sum(estimate_text(json.dumps(t, ensure_ascii=False)) for t in raw_tools)
    new = sum(tool_tokens(t) for t in compacted)
    return 0.0 if raw <= 0 else 1 - new / raw
