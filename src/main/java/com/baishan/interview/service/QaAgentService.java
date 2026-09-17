package com.baishan.interview.service;

import com.baishan.interview.agent.KnowledgeTools;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.tool.method.MethodToolCallbackProvider;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;

/**
 * 智能问答 Agent（ReAct 风格）：
 * 具备知识库检索 / 简历分析工具，可自主决定是否调用工具后组织回答，流式输出。
 */
@Service
public class QaAgentService {

    private static final String SYSTEM_PROMPT = """
            你是「白山求职助手」，一款服务于求职者的 AI 助手，擅长回答求职、简历、面试相关的问题。

            【可用工具】
            - searchKnowledge：检索企业知识库（企业介绍、岗位说明、参考资料等），回答企业背景 / 岗位职责 / 公司业务问题时必须先调用它。
            - getResumeAnalysis：获取指定简历的结构化分析结果，回答与某份简历相关的问题时可调用它。

            【回答要求】
            1. 涉及知识库或简历内容时，先调用工具获取真实信息，严禁编造工具未返回的内容。
            2. 使用简体中文回答，条理清晰，适当使用列表。
            3. 引用知识库内容时，在末尾注明信息来源文件名。
            4. 与求职无关的问题，礼貌说明能力范围并引导回求职话题。
            """;

    private final ChatClient chatClient;
    private final KnowledgeTools knowledgeTools;

    public QaAgentService(ChatClient.Builder chatClientBuilder,
                          KnowledgeTools knowledgeTools) {
        this.chatClient = chatClientBuilder.build();
        this.knowledgeTools = knowledgeTools;
    }

    /** 流式问答。resumeId 可空：提供时向模型提示简历上下文。 */
    public Flux<String> stream(String message, String resumeId) {
        String user = resumeId == null || resumeId.isBlank()
                ? message
                : "（当前对话关联的简历 ID：" + resumeId + "，如需了解简历内容请调用 getResumeAnalysis 工具）\n\n" + message;
        // 每次调用构建工具提供者：避免容器级 ToolCallbackProvider Bean 与模型装配形成循环依赖
        MethodToolCallbackProvider provider = MethodToolCallbackProvider.builder()
                .toolObjects(knowledgeTools)
                .build();
        return chatClient.prompt()
                .system(SYSTEM_PROMPT)
                .user(user)
                .tools(provider)
                .stream()
                .content();
    }
}
