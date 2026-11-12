# 供应商与协议总表

由 `miniagent/model/registry.py` 自动生成：**48 个内置模型 / 37 家供应商 / 4 种协议**，其中 **13 个进菜单首屏**（`MAINSTREAM`）。

加供应商不用改代码：往 `CATALOG` 加一行。用户自己的模型写 `.agent/models.json`，见 `model/custom.py`。

| 协议 | 实现 | 条目数 |
| --- | --- | --- |
| `openai` | OpenAI Chat Completions | 41 |
| `anthropic` | Anthropic Messages | 3 |
| `gemini` | Google Gemini | 2 |
| `responses` | OpenAI Responses | 2 |

## 菜单首屏（主流）

| 显示名 | 供应商 | 协议 | 模型 id |
| --- | --- | --- | --- |
| GPT-4o | OpenAI | `openai` | `gpt-4o` |
| Grok 4 | xAI | `openai` | `grok-4` |
| Llama 3.3 70B | Groq | `openai` | `llama-3.3-70b-versatile` |
| DeepSeek V4 Flash | DeepSeek | `openai` | `deepseek-chat` |
| DeepSeek Reasoner | DeepSeek | `openai` | `deepseek-reasoner` |
| Kimi K2 | Moonshot | `openai` | `kimi-k2-0905-preview` |
| GLM-4.6 | 智谱 AI | `openai` | `glm-4.6` |
| Qwen3 Max | 阿里云百炼 | `openai` | `qwen3-max` |
| Claude Sonnet 4.5 | Anthropic | `anthropic` | `claude-sonnet-4-5` |
| Claude Haiku 4.5 | Anthropic | `anthropic` | `claude-haiku-4-5` |
| Gemini 2.5 Pro | Google | `gemini` | `gemini-2.5-pro` |
| Gemini 2.5 Flash | Google | `gemini` | `gemini-2.5-flash` |
| GPT-5 | OpenAI | `responses` | `gpt-5` |

## 全部明细

| 显示名 | 供应商 | 协议 | 模型 id | 接口地址 | 环境变量 |
| --- | --- | --- | --- | --- | --- |
| GPT-4o ★ | OpenAI | `openai` | `gpt-4o` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| GPT-4o mini | OpenAI | `openai` | `gpt-4o-mini` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| o4-mini | OpenAI | `openai` | `o4-mini` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| Grok 4 ★ | xAI | `openai` | `grok-4` | `https://api.x.ai/v1` | `XAI_API_KEY` |
| Mistral Large | Mistral | `openai` | `mistral-large-latest` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| Llama 3.3 70B ★ | Groq | `openai` | `llama-3.3-70b-versatile` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| Llama 3.1 405B | Together | `openai` | `meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| Fireworks Llama 70B | Fireworks | `openai` | `accounts/fireworks/models/llama-v3p1-70b-instruct` | `https://api.fireworks.ai/inference/v1` | `FIREWORKS_API_KEY` |
| DeepInfra Llama 70B | DeepInfra | `openai` | `meta-llama/Meta-Llama-3.1-70B-Instruct` | `https://api.deepinfra.com/v1/openai` | `DEEPINFRA_API_KEY` |
| OpenRouter Auto | OpenRouter | `openai` | `openrouter/auto` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| Perplexity Sonar | Perplexity | `openai` | `sonar-pro` | `https://api.perplexity.ai` | `PERPLEXITY_API_KEY` |
| DeepSeek V4 Flash ★ | DeepSeek | `openai` | `deepseek-chat` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| DeepSeek Reasoner ★ | DeepSeek | `openai` | `deepseek-reasoner` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| Kimi K2 ★ | Moonshot | `openai` | `kimi-k2-0905-preview` | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| GLM-4.6 ★ | 智谱 AI | `openai` | `glm-4.6` | `https://open.bigmodel.cn/api/paas/v4` | `ZHIPU_API_KEY` |
| Qwen3 Max ★ | 阿里云百炼 | `openai` | `qwen3-max` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` |
| Qwen2.5 72B | SiliconFlow | `openai` | `Qwen/Qwen2.5-72B-Instruct` | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` |
| DeepSeek V3 | SiliconFlow | `openai` | `deepseek-ai/DeepSeek-V3` | `https://api.siliconflow.cn/v1` | `SILICONFLOW_API_KEY` |
| 豆包 Doubao | 火山引擎 | `openai` | `doubao-1-5-pro-32k-250115` | `https://ark.cn-beijing.volces.com/api/v3` | `ARK_API_KEY` |
| 混元 Hunyuan | 腾讯云 | `openai` | `hunyuan-turbos-latest` | `https://api.hunyuan.cloud.tencent.com/v1` | `HUNYUAN_API_KEY` |
| MiniMax abab | MiniMax | `openai` | `abab6.5s-chat` | `https://api.minimax.chat/v1` | `MINIMAX_API_KEY` |
| Yi Large | 零一万物 | `openai` | `yi-large` | `https://api.lingyiwanwu.com/v1` | `YI_API_KEY` |
| Step-2 | 阶跃星辰 | `openai` | `step-2-16k` | `https://api.stepfun.com/v1` | `STEPFUN_API_KEY` |
| Baichuan4 | 百川智能 | `openai` | `Baichuan4` | `https://api.baichuan-ai.com/v1` | `BAICHUAN_API_KEY` |
| Spark Max | 科大讯飞 | `openai` | `generalv3.5` | `https://spark-api-open.xf-yun.com/v1` | `SPARK_API_KEY` |
| Ollama 本地 | 本地 | `openai` | `qwen2.5:7b` | `http://localhost:11434/v1` | `OLLAMA_API_KEY` |
| vLLM 本地 | 本地 | `openai` | `local-model` | `http://localhost:8000/v1` | `VLLM_API_KEY` |
| LM Studio 本地 | 本地 | `openai` | `local-model` | `http://localhost:1234/v1` | `LMSTUDIO_API_KEY` |
| Cerebras Llama 70B | Cerebras | `openai` | `llama3.3-70b` | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` |
| Hyperbolic Llama 405B | Hyperbolic | `openai` | `meta-llama/Meta-Llama-3.1-405B-Instruct` | `https://api.hyperbolic.xyz/v1` | `HYPERBOLIC_API_KEY` |
| Novita Llama 70B | Novita | `openai` | `meta-llama/llama-3.3-70b-instruct` | `https://api.novita.ai/v3/openai` | `NOVITA_API_KEY` |
| SambaNova Llama 405B | SambaNova | `openai` | `Meta-Llama-3.1-405B-Instruct` | `https://api.sambanova.ai/v1` | `SAMBANOVA_API_KEY` |
| Nebius Llama 70B | Nebius | `openai` | `meta-llama/Meta-Llama-3.1-70B-Instruct` | `https://api.studio.nebius.ai/v1` | `NEBIUS_API_KEY` |
| ModelScope Qwen | 魔搭 ModelScope | `openai` | `Qwen/Qwen2.5-72B-Instruct` | `https://api-inference.modelscope.cn/v1` | `MODELSCOPE_API_KEY` |
| 千帆 ERNIE | 百度智能云 | `openai` | `ernie-4.5-turbo-128k` | `https://qianfan.baidubce.com/v2` | `QIANFAN_API_KEY` |
| Cohere Command | Cohere | `openai` | `command-r-plus` | `https://api.cohere.ai/compatibility/v1` | `COHERE_API_KEY` |
| Upstage Solar | Upstage | `openai` | `solar-pro` | `https://api.upstage.ai/v1/solar` | `UPSTAGE_API_KEY` |
| AI21 Jamba | AI21 | `openai` | `jamba-large` | `https://api.ai21.com/studio/v1` | `AI21_API_KEY` |
| 天工 SkyWork | 昆仑万维 | `openai` | `skywork-pro` | `https://api.tiangong.cn/v1` | `TIANGONG_API_KEY` |
| 商汤日日新 | 商汤 | `openai` | `SenseChat-5` | `https://api.sensenova.cn/compatible-mode/v1` | `SENSENOVA_API_KEY` |
| Azure OpenAI | Azure | `openai` | `gpt-4o` | `https://YOUR-RESOURCE.openai.azure.com/openai/v1` | `AZURE_OPENAI_API_KEY` |
| Claude Sonnet 4.5 ★ | Anthropic | `anthropic` | `claude-sonnet-4-5` | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| Claude Opus 4.1 | Anthropic | `anthropic` | `claude-opus-4-1` | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| Claude Haiku 4.5 ★ | Anthropic | `anthropic` | `claude-haiku-4-5` | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| Gemini 2.5 Pro ★ | Google | `gemini` | `gemini-2.5-pro` | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` |
| Gemini 2.5 Flash ★ | Google | `gemini` | `gemini-2.5-flash` | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` |
| GPT-5 ★ | OpenAI | `responses` | `gpt-5` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| o3 | OpenAI | `responses` | `o3` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |

## 方言差异（quirks）

| quirk | 作用 | 谁在用 |
| --- | --- | --- |
| `max_tokens_field` | 换掉 token 上限字段名 | OpenAI o 系列用 `max_completion_tokens` |
| `auth: api-key` | 用 `api-key` 头代替 `Authorization: Bearer` | Azure OpenAI |
| `headers` | 追加自定义请求头 | 需要额外头的网关 |
| `stream_usage` | 是否请求流式用量统计 | 默认开；不支持的网关可关 |

> ★ = 进菜单首屏。本地模型（Ollama / vLLM / LM Studio）不校验 Key，缺省时会填占位。
