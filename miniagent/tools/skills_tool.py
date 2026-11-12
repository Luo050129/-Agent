"""工具层：技能加载。

技能清单在系统提示词里，正文靠这两个工具按需取，省 token。
"""

from __future__ import annotations

from .. import skills
from ._util import ERR, truncate


def use_skill(name: str) -> str:
    """按名字加载技能正文。"""
    target = skills.find(name)
    if target is None:
        available = "、".join(s.key for s in skills.discover()) or "（没有可用技能）"
        return f"{ERR}没有这个技能：{name}。可用技能：{available}"

    text = skills.body(target)
    return truncate(f"[技能 {target.key}｜{target.name}]\n{text}", 8000)


def list_skills() -> str:
    """列出所有技能（只有名字和一句描述）。"""
    items = skills.discover()
    if not items:
        return f"{ERR}当前没有安装任何技能。把 .md 放进 skills/ 目录就会出现"
    return "\n".join(f"- {s.key}：{s.description}" for s in items)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "use_skill",
            "description": "加载一个技能的完整正文，然后按它的方法做事。系统提示里列了可用技能名。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名（文件名或标题）"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出当前可用的技能及其一句话描述。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HANDLERS = {"use_skill": use_skill, "list_skills": list_skills}
DANGEROUS: set[str] = set()
