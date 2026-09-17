# 面试辅助 Agent

基于大语言模型的**简历分析与模拟面试系统**：为求职者提供简历评估建议、个性化模拟面试、知识库增强问答与 PDF 报告导出。

> ⚠️ **安全提示（部署前必读）**
>
> 本项目默认 **8080 端口对外裸奔，无任何鉴权与限流**。任何人都能直接调用接口并消耗你配置的 LLM API 额度（产生费用）。
> - **仅限本地 / 受信任的内网使用**，切勿直接暴露到公网。
> - `local` profile 下知识库 RAG 功能关闭（仅用 H2 内存库演示简历分析 / 模拟面试），完整 RAG 需 `docker compose up -d` 启动 PostgreSQL+pgvector。
> - 模型密钥通过环境变量注入（见下文「配置模型密钥」），**请勿将密钥提交进仓库**；本仓库 `.gitignore` 已忽略 `data/`、`bin/`、`.env` 等本地产物。

## 测试

```bash
# 运行全部测试（上下文冒烟 + 核心单测 + 检索单测）
./gradlew test
```

| 测试类 | 类型 | 覆盖 |
|---|---|---|
| `ContextSmokeTest` | 集成 | Spring 上下文 / AI Bean 装配（H2 替代真实库） |
| `TextChunkerTest` | 单元 | 文本分块：空输入 / 合并 / 重叠 / 超长段落按句切 |
| `CacheServiceTest` | 单元（Mock Redis） | 缓存前缀、命中、Redis 异常静默降级、SCAN 前缀清理 |
| `KnowledgeServiceRetrievalTest` | 单元（Mock 依赖） | RAG 开/关分支、缓存命中跳过向量化、Top-K、提示词拼接 |
| `RagRetrievalEvalTest` | 集成（黄金集评估） | RAG 检索质量：合成语料 → 分块 → 向量化 → 余弦排序 → recall@K |

> `RagRetrievalEvalTest` 用于量化知识库检索质量（黄金集 recall@K）。它通过环境变量 `EMBEDDING_API_KEY` 控制：
> **仅当该变量存在时才会执行**（需要真实 embedding 调用），否则自动跳过，因此无密钥的 CI 环境不会失败。评估语料为**完全合成的通用面试题库，不含任何个人简历或隐私信息**。

## 技术栈

| 层 | 选型 | 用途 |
|---|---|---|
| 语言/构建 | Java 21 · Gradle 8.14 | 工具链 |
| 框架 | Spring Boot 4.0.8 | Web / JPA / 配置 |
| AI | Spring AI 2.0.1（OpenAI 兼容） | ChatClient 流式对话 · 结构化输出 · Tool Calling |
| 向量库 | PostgreSQL 16 + pgvector（HNSW 索引） | 知识库语义检索 |
| 缓存 | Redis 7 | 分析结果 / 检索结果缓存 |
| 文档解析 | Apache Tika 4 | PDF / Word / TXT 文本抽取 |
| PDF 报告 | iText 8 + font-asian（中文字体） | 面试评估报告导出 |
| 容器 | Docker Compose | 一键拉起 PG + Redis |

## 功能总览

| 模块 | 能力 | 实现要点 |
|---|---|---|
| 简历分析 | 上传 → Tika 解析 → LLM 结构化评估（评分/技能/优劣势/建议/维度得分） | `BeanOutputConverter` 强类型输出，结果落库 + Redis 缓存 |
| 模拟面试 | 技术面 / 行为面 / 综合面，SSE流式逐题提问（打字机效果），逐题评分与点评，面试总结 | 面试官提示词动态组装（简历+JD+知识库上下文），出题 SSE 流式，评估走结构化输出 |
| 知识库 RAG | 文档上传 → 分块(800,50) → Embedding → pgvector 余弦检索 Top-K | 自定义 `VectorRepository`（HNSW 索引），结果短时缓存 |
| 智能问答 | ReAct 风格 Agent，可自主调用「知识库检索 / 简历分析」工具后作答 | `@Tool` 注解 + `MethodToolCallbackProvider`，流式输出 |
| 报告导出 | 面试评估 PDF（简历分析 + 问答记录 + 逐题评估 + 综合总结） | iText 8 + STSong-Light 中文字体 |

## 架构

```
浏览器(静态前端) ── REST/SSE ──▶ Spring Boot 4.0 (Servlet + Reactor)
                                    │
        ┌───────────────┬───────────┼──────────────────┬──────────────┐
        ▼               ▼           ▼                  ▼              ▼
  ResumeController  Interview  KnowledgeController  QaController  ReportController          //定义业务
   (简历分析)     Service(面试Agent)  (RAG pgvector)  (工具调用Agent)  (iText PDF)
        │               │           │
        ▼               ▼           ▼
   ChatClient ◀─── ChatClient ◀── EmbeddingModel
   (DeepSeek等,    (结构化输出)      (OpenAI兼容端点)
    OpenAI兼容)
        │               │           │
        ▼               ▼           ▼
   Redis缓存      PostgreSQL(pgvector HNSW)      Redis缓存           
```

- **LLM 双通道**：Chat 走 DeepSeek（`api.deepseek.com`，OpenAI 兼容协议）；Embedding 走任意 OpenAI 兼容向量端点（默认 SiliconFlow `BAAI/bge-m3`，1024 维），两者端点/密钥可独立配置。
- **面试 Agent 循环**：`ask（流式出题）→ answer（结构化评估）→ finish（总结）`，状态全部落库（`interview_session` / `interview_message`），支持中途断线后通过 GET 恢复会话记录。

## 快速开始

### 0. 本地演示模式

> 核心闭环（简历上传 → AI 分析 → 模拟面试 → 总结 → PDF 报告）**不依赖 PostgreSQL/pgvector**。
> 仅知识库 RAG 需要向量库，本地模式自动优雅降级。

```bash
# Windows：双击 start-local.cmd；或命令行执行
SPRING_PROFILES_ACTIVE=local ./gradlew bootRun --args="--server.port=8080"
# → http://localhost:8080
```

本地模式使用 H2（PostgreSQL 兼容模式，数据落在 `data/` 目录）+ 本机 Redis，Flyway 关闭。切换到完整模式只需去掉 `local` profile 并先启动 Docker 基础设施。

### 1. 完整模式：启动基础设施（Docker）

```bash
docker compose up -d
# PostgreSQL(pgvector): localhost:5432  interview_agent/postgres
# Redis:               localhost:6379
```

### 2. 配置模型密钥（环境变量）

> 💡 本项目已在本机通过 `setx` 写入系统用户环境变量（`AI_API_KEY` / `AI_CHAT_MODEL` / `AI_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL`），**新开终端即可直接启动**。以下为换环境 / 换钥匙时的说明：

```bash
# Chat（必填）——DeepSeek 或任意 OpenAI 兼容服务
export AI_API_KEY=sk-xxxx
# export AI_BASE_URL=https://api.deepseek.com      # 默认
# export AI_CHAT_MODEL=deepseek-v4-flash           # 默认（deepseek-chat 已弃用）

# Embedding（必填，RAG/知识库需要）——OpenAI 兼容向量端点
export EMBEDDING_API_KEY=sk-xxxx
# export EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1   # 默认
# export EMBEDDING_MODEL=BAAI/bge-m3                        # 默认（1024 维）
```

Windows 用户也可用 `setx AI_API_KEY "sk-xxxx"` 写入用户级环境变量（新开终端生效）。

> ⚠️ 切换 embedding 模型时若维度 ≠ 1024，需同步修改 `src/main/resources/db/migration/V1__init.sql` 中 `vector(1024)` 与 `application.yml` 的 `app.rag.embedding-dimensions`。

### 3. 启动应用

```bash
./gradlew bootRun          # 或 ./gradlew bootJar && java -jar build/libs/baishan-interview-agent.jar
```

访问 **http://localhost:8080** 打开控制台。

### 4. 体验流程

1. **简历分析**：上传简历（PDF/Word/TXT）→ 点击「AI 分析」
2. **模拟面试**：选择已分析简历 → 填 JD → 开始面试 → 逐题作答 → 结束生成总结 → 下载 PDF 报告
3. **知识库**：上传企业介绍/岗位说明 → 语义检索验证
4. **智能问答**：提问（如"根据我的简历我适合什么岗位？"），Agent 自动调工具检索

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/resumes/upload` | 上传简历（multipart `file`） |
| GET | `/api/resumes` | 简历列表 |
| GET | `/api/resumes/{id}` | 简历详情 |
| POST | `/api/resumes/{id}/analyze` | 触发 LLM 分析（幂等，已分析返回缓存） |
| GET | `/api/resumes/{id}/analysis` | 获取分析结果 |
| DELETE | `/api/resumes/{id}` | 删除简历 |
| POST | `/api/knowledge/documents` | 知识库文档入库（multipart `file` + `docType`） |
| GET | `/api/knowledge/documents` | 文档列表 |
| DELETE | `/api/knowledge/documents/{id}` | 删除文档及分块 |
| GET | `/api/knowledge/search?query=&topK=` | 向量语义检索 |
| POST | `/api/interviews` | 创建面试会话 `{resumeId, mode, jdText}` |
| GET | `/api/interviews/{id}` | 会话详情（含全部消息与评估） |
| POST | `/api/interviews/{id}/ask` | **SSE 流式**提出下一题 |
| POST | `/api/interviews/{id}/answer` | 提交回答，返回结构化评估 |
| POST | `/api/interviews/{id}/finish` | 结束并生成总结 |
| GET | `/api/reports/interview/{sessionId}/pdf` | 下载评估报告 PDF |
| POST | `/api/qa/stream` | **SSE 流式**智能问答（可带 `resumeId`） |

`mode` 取值：`TECHNICAL` / `BEHAVIORAL` / `MIXED`（默认）。

## 关键配置（application.yml）

```yaml
spring:
  ai:
    openai:
      api-key: ${AI_API_KEY:}                       # Chat 密钥
      base-url: ${AI_BASE_URL:https://api.deepseek.com}
      chat:
        model: ${AI_CHAT_MODEL:deepseek-v4-flash}
        temperature: 0.7
        max-tokens: 4096
        extra-body:                        # DeepSeek：关闭思考模式，加快流式首字
          thinking:
            type: disabled
      embedding:
        api-key: ${EMBEDDING_API_KEY:}              # Embedding 密钥（独立）
        base-url: ${EMBEDDING_BASE_URL:https://api.siliconflow.cn/v1}
        model: ${EMBEDDING_MODEL:BAAI/bge-m3}
app:
  interview:
    question-count: 5        # 每场面试题数
    max-answer-length: 5000
  rag:
    chunk-size: 500          # 分块大小（字符）
    chunk-overlap: 80
    top-k: 4                 # 检索条数
    embedding-dimensions: 1024
```

## 工程结构

```
src/main/java/com/baishan/interview/
├── agent/            # @Tool 工具（知识库检索 / 简历分析）
├── config/           # 配置（AppProperties / InfraConfig / WebConfig）
├── controller/       # REST + SSE 控制器
├── domain/           # JPA 实体与枚举
├── dto/              # 请求 / 响应 / LLM 结构化输出契约
├── exception/        # 统一异常处理
├── repository/       # JPA 仓库 + pgvector 原生查询（VectorRepository）
└── service/          # 解析 / RAG / 简历分析 / 面试 Agent / 问答 / 报告
src/main/resources/
├── db/migration/     # Flyway（V1__init.sql：vector 扩展 + 建表 + HNSW 索引）
└── static/           # 前端单页（原生 JS，零构建）
src/test/             # 上下文冒烟测试（H2 替代数据库，验证 AI Bean 装配）
```

## 说明与后续方向

- 简历分析 / 面试评估的提示词采用「上下文层 / 任务层 / 约束层」结构，集中在各 Service 常量中，便于调优。
- 未配置模型密钥时应用仍可启动（启动/建库不受影响），调用 AI 接口才报错。
- 工具调用（Tool Calling）在问答调用时即时构建 `MethodToolCallbackProvider`，避免容器级 Provider Bean 与模型装配形成循环依赖（Spring AI 2.0 的 `toolCallbackResolver` 会急切收集 Provider Bean）。
- 运行 `./gradlew test` 可执行上下文冒烟测试（无需数据库）。
- 可扩展方向：OCR 简历（扫描件）、面试语音对话（STT/TTS）、多轮追问的深度评估、基于历史面试的候选人画像、报告 PDF 模板定制。
