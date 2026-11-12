"""模型目录测试：切换模型靠的是数据，不是改代码。

不需要网络，`python tests/test_models.py` 直接跑。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.model import LLM, all_specs, default_spec, find, providers  # noqa: E402


def main() -> int:
    print("[models] 开始")
    checks = []

    checks.append(("目录里不止一个供应商", len(providers()) >= 3))
    checks.append(("目录里不止一个模型", len(all_specs()) >= 5))

    ds = find("DeepSeek V4 Flash")
    checks.append(("能按显示名找到模型", ds is not None and ds.model == "deepseek-chat"))
    checks.append(("能按底层 model 名找到", find("deepseek-chat") is not None))
    checks.append(("找不到时返回 None", find("不存在的模型") is None))

    spec = default_spec()
    checks.append(("默认模型在目录里", spec in all_specs()))

    # 用显式 base_url 构造，验证解析逻辑但不发请求
    llm = LLM(model="DeepSeek V4 Flash", base_url="http://127.0.0.1:1/v1", api_key_value="k")
    checks.append(("LLM 走目录里的模型 id", llm.model == "deepseek-chat"))
    checks.append(("LLM 记住显示名", llm.label == "DeepSeek V4 Flash"))
    checks.append(("请求地址拼接正确", llm.url.endswith("/chat/completions")))

    other = LLM(model="GPT-4o", base_url="http://127.0.0.1:1/v1", api_key_value="k")
    checks.append(("切到其他供应商的模型", other.model == "gpt-4o" and other.spec.provider == "OpenAI"))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[models] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
