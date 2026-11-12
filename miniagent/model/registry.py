"""模型层：供应商目录。

35 个供应商 / 4 种原生协议，全部收敛成同一份数据结构：
    key        界面显示名（唯一标识）
    provider   供应商
    protocol   用哪种协议（openai / anthropic / gemini / responses）
    model      请求体里的模型 id
    base_url   接口地址
    key_env    依次尝试的环境变量
    quirks     方言差异（鉴权头、token 字段名、额外 header 等）

加供应商 = 往 CATALOG 里加一行；只有出现全新协议时才需要写适配器。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSpec:
    key: str
    provider: str
    protocol: str
    model: str
    base_url: str
    key_env: tuple[str, ...]
    quirks: dict = field(default_factory=dict, hash=False)
    context: int = 128000  # 上下文窗口，用于预算提示

    @property
    def label(self) -> str:
        return self.key


def _oai(key, provider, model, base_url, key_env, context=128000, **quirks):
    return ModelSpec(key, provider, "openai", model, base_url, (key_env,), dict(quirks), context)


def _anthropic(key, provider, model, base_url, key_env, context=200000):
    return ModelSpec(key, provider, "anthropic", model, base_url, (key_env,), {}, context)


def _gemini(key, provider, model, base_url, key_env, context=1000000):
    return ModelSpec(key, provider, "gemini", model, base_url, (key_env,), {}, context)


def _responses(key, provider, model, base_url, key_env, context=200000):
    return ModelSpec(key, provider, "responses", model, base_url, (key_env,), {}, context)


_BASE_OPENAI = "https://api.openai.com/v1"

CATALOG: tuple[ModelSpec, ...] = (
    # ---------------- OpenAI 兼容：国际 ----------------
    _oai("GPT-4o", "OpenAI", "gpt-4o", _BASE_OPENAI, "OPENAI_API_KEY"),
    _oai("GPT-4o mini", "OpenAI", "gpt-4o-mini", _BASE_OPENAI, "OPENAI_API_KEY"),
    _oai("o4-mini", "OpenAI", "o4-mini", _BASE_OPENAI, "OPENAI_API_KEY",
         max_tokens_field="max_completion_tokens"),
    _oai("Grok 4", "xAI", "grok-4", "https://api.x.ai/v1", "XAI_API_KEY"),
    _oai("Mistral Large", "Mistral", "mistral-large-latest", "https://api.mistral.ai/v1",
         "MISTRAL_API_KEY"),
    _oai("Llama 3.3 70B", "Groq", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1",
         "GROQ_API_KEY"),
    _oai("Llama 3.1 405B", "Together", "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
         "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    _oai("Fireworks Llama 70B", "Fireworks", "accounts/fireworks/models/llama-v3p1-70b-instruct",
         "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    _oai("DeepInfra Llama 70B", "DeepInfra", "meta-llama/Meta-Llama-3.1-70B-Instruct",
         "https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY"),
    _oai("OpenRouter Auto", "OpenRouter", "openrouter/auto", "https://openrouter.ai/api/v1",
         "OPENROUTER_API_KEY"),
    _oai("Perplexity Sonar", "Perplexity", "sonar-pro", "https://api.perplexity.ai",
         "PERPLEXITY_API_KEY", context=127000),

    # ---------------- OpenAI 兼容：国内 ----------------
    _oai("DeepSeek V4 Flash", "DeepSeek", "deepseek-chat", "https://api.deepseek.com/v1",
         "DEEPSEEK_API_KEY"),
    _oai("DeepSeek Reasoner", "DeepSeek", "deepseek-reasoner", "https://api.deepseek.com/v1",
         "DEEPSEEK_API_KEY"),
    _oai("Kimi K2", "Moonshot", "kimi-k2-0905-preview", "https://api.moonshot.cn/v1",
         "MOONSHOT_API_KEY"),
    _oai("GLM-4.6", "智谱 AI", "glm-4.6", "https://open.bigmodel.cn/api/paas/v4", "ZHIPU_API_KEY"),
    _oai("Qwen3 Max", "阿里云百炼", "qwen3-max", "https://dashscope.aliyuncs.com/compatible-mode/v1",
         "DASHSCOPE_API_KEY"),
    _oai("Qwen2.5 72B", "SiliconFlow", "Qwen/Qwen2.5-72B-Instruct",
         "https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY"),
    _oai("DeepSeek V3", "SiliconFlow", "deepseek-ai/DeepSeek-V3",
         "https://api.siliconflow.cn/v1", "SILICONFLOW_API_KEY"),
    _oai("豆包 Doubao", "火山引擎", "doubao-1-5-pro-32k-250115",
         "https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", context=32000),
    _oai("混元 Hunyuan", "腾讯云", "hunyuan-turbos-latest",
         "https://api.hunyuan.cloud.tencent.com/v1", "HUNYUAN_API_KEY", context=32000),
    _oai("MiniMax abab", "MiniMax", "abab6.5s-chat", "https://api.minimax.chat/v1",
         "MINIMAX_API_KEY", context=245000),
    _oai("Yi Large", "零一万物", "yi-large", "https://api.lingyiwanwu.com/v1", "YI_API_KEY",
         context=16000),
    _oai("Step-2", "阶跃星辰", "step-2-16k", "https://api.stepfun.com/v1", "STEPFUN_API_KEY",
         context=16000),
    _oai("Baichuan4", "百川智能", "Baichuan4", "https://api.baichuan-ai.com/v1", "BAICHUAN_API_KEY",
         context=32000),
    _oai("Spark Max", "科大讯飞", "generalv3.5", "https://spark-api-open.xf-yun.com/v1",
         "SPARK_API_KEY", context=8000),

    # ---------------- OpenAI 兼容：本地 ----------------
    _oai("Ollama 本地", "本地", "qwen2.5:7b", "http://localhost:11434/v1", "OLLAMA_API_KEY",
         context=32000),
    _oai("vLLM 本地", "本地", "local-model", "http://localhost:8000/v1", "VLLM_API_KEY",
         context=32000),
    _oai("LM Studio 本地", "本地", "local-model", "http://localhost:1234/v1", "LMSTUDIO_API_KEY",
         context=32000),

    # ---------------- OpenAI 兼容：更多推理 / 聚合服务 ----------------
    _oai("Cerebras Llama 70B", "Cerebras", "llama3.3-70b", "https://api.cerebras.ai/v1",
         "CEREBRAS_API_KEY"),
    _oai("Hyperbolic Llama 405B", "Hyperbolic", "meta-llama/Meta-Llama-3.1-405B-Instruct",
         "https://api.hyperbolic.xyz/v1", "HYPERBOLIC_API_KEY"),
    _oai("Novita Llama 70B", "Novita", "meta-llama/llama-3.3-70b-instruct",
         "https://api.novita.ai/v3/openai", "NOVITA_API_KEY"),
    _oai("SambaNova Llama 405B", "SambaNova", "Meta-Llama-3.1-405B-Instruct",
         "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY"),
    _oai("Nebius Llama 70B", "Nebius", "meta-llama/Meta-Llama-3.1-70B-Instruct",
         "https://api.studio.nebius.ai/v1", "NEBIUS_API_KEY"),
    _oai("ModelScope Qwen", "魔搭 ModelScope", "Qwen/Qwen2.5-72B-Instruct",
         "https://api-inference.modelscope.cn/v1", "MODELSCOPE_API_KEY"),
    _oai("千帆 ERNIE", "百度智能云", "ernie-4.5-turbo-128k", "https://qianfan.baidubce.com/v2",
         "QIANFAN_API_KEY", context=128000),
    _oai("Cohere Command", "Cohere", "command-r-plus",
         "https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY"),
    _oai("Upstage Solar", "Upstage", "solar-pro", "https://api.upstage.ai/v1/solar",
         "UPSTAGE_API_KEY"),
    _oai("AI21 Jamba", "AI21", "jamba-large", "https://api.ai21.com/studio/v1", "AI21_API_KEY"),
    _oai("天工 SkyWork", "昆仑万维", "skywork-pro", "https://api.tiangong.cn/v1",
         "TIANGONG_API_KEY", context=32000),
    _oai("商汤日日新", "商汤", "SenseChat-5", "https://api.sensenova.cn/compatible-mode/v1",
         "SENSENOVA_API_KEY", context=32000),

    # ---------------- OpenAI 兼容但鉴权不同 ----------------
    ModelSpec("Azure OpenAI", "Azure", "openai", "gpt-4o",
              "https://YOUR-RESOURCE.openai.azure.com/openai/v1", ("AZURE_OPENAI_API_KEY",),
              {"auth": "api-key"}),

    # ---------------- Anthropic 原生 ----------------
    _anthropic("Claude Sonnet 4.5", "Anthropic", "claude-sonnet-4-5",
               "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    _anthropic("Claude Opus 4.1", "Anthropic", "claude-opus-4-1",
               "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    _anthropic("Claude Haiku 4.5", "Anthropic", "claude-haiku-4-5",
               "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),

    # ---------------- Gemini 原生 ----------------
    _gemini("Gemini 2.5 Pro", "Google", "gemini-2.5-pro",
            "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
    _gemini("Gemini 2.5 Flash", "Google", "gemini-2.5-flash",
            "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),

    # ---------------- OpenAI Responses 原生 ----------------
    _responses("GPT-5", "OpenAI", "gpt-5", _BASE_OPENAI, "OPENAI_API_KEY"),
    _responses("o3", "OpenAI", "o3", _BASE_OPENAI, "OPENAI_API_KEY"),
)

_BY_KEY = {spec.key: spec for spec in CATALOG}

# 菜单默认只显示这些 —— 40 多条平铺没人看得下去。
# 每个供应商留 1~2 个主力型号，其余的进「更多模型」二级菜单。
MAINSTREAM: frozenset[str] = frozenset(
    {
        "DeepSeek V4 Flash",
        "DeepSeek Reasoner",
        "GPT-5",
        "GPT-4o",
        "Claude Sonnet 4.5",
        "Claude Haiku 4.5",
        "Gemini 2.5 Flash",
        "Gemini 2.5 Pro",
        "Grok 4",
        "Kimi K2",
        "Qwen3 Max",
        "GLM-4.6",
        "Llama 3.3 70B",
    }
)


def all_specs() -> tuple[ModelSpec, ...]:
    """内置 + 用户自定义。自定义的排前面，菜单里一眼能看到。"""
    try:
        from .custom import load as load_custom

        extra = tuple(load_custom())
    except Exception:  # noqa: BLE001 自定义文件坏了不能拖垮启动
        extra = ()
    if not extra:
        return CATALOG
    return extra + CATALOG


def mainstream_specs() -> list[ModelSpec]:
    """菜单首屏要显示的那批。找不到自定义的也算主流。"""
    from .custom import load as load_custom

    try:
        custom = load_custom()
    except Exception:  # noqa: BLE001
        custom = []
    picked = [spec for spec in CATALOG if spec.key in MAINSTREAM]
    return list(custom) + picked


def find(key: str | None) -> ModelSpec | None:
    """按显示名精确匹配；也接受底层 model id。自定义模型优先。"""
    if not key:
        return None
    for spec in all_specs():
        if spec.key == key:
            return spec
    for spec in all_specs():
        if spec.model == key:
            return spec
    return None


def default_spec() -> ModelSpec:
    env_model = os.getenv("AGENT_MODEL")
    return find(env_model) or all_specs()[0]


def providers() -> list[str]:
    seen: list[str] = []
    for spec in all_specs():
        if spec.provider not in seen:
            seen.append(spec.provider)
    return seen


def protocols() -> list[str]:
    seen: list[str] = []
    for spec in all_specs():
        if spec.protocol not in seen:
            seen.append(spec.protocol)
    return seen


def by_protocol(protocol: str) -> list[ModelSpec]:
    return [spec for spec in all_specs() if spec.protocol == protocol]


def by_provider(provider: str) -> list[ModelSpec]:
    return [spec for spec in all_specs() if spec.provider == provider]
