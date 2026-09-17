package com.baishan.interview;

import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 上下文冒烟测试：用 H2（PostgreSQL 兼容模式）替代真实数据库，
 * 验证 Spring 上下文可完整加载、Spring AI 关键 Bean 全部装配成功。
 * 真实环境（docker compose up）下数据库相关能力由 PG + pgvector 提供。
 */
@SpringBootTest(properties = {
        "spring.datasource.url=jdbc:h2:mem:smoke;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.flyway.enabled=false",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "spring.ai.openai.api-key=sk-dummy",
        "spring.ai.openai.embedding.api-key=sk-dummy",
        "app.rag.embedding-dimensions=1536"
})
class ContextSmokeTest {

    @Autowired
    private ApplicationContext context;

    @Test
    void contextLoads() {
        assertNotNull(context);
    }

    @Test
    void aiBeansWired() {
        // Spring AI 2.0 只注册原型 ChatClient.Builder，ChatClient 由 Builder 构建
        assertTrue(context.getBean(ChatClient.Builder.class) instanceof ChatClient.Builder, "ChatClient.Builder 未装配");
        assertTrue(context.getBean(EmbeddingModel.class) instanceof EmbeddingModel, "EmbeddingModel 未装配");
        assertNotNull(context.getBean("knowledgeTools"), "知识库工具未装配");
    }

    @Test
    void servicesWired() {
        assertNotNull(context.getBean("resumeService"));
        assertNotNull(context.getBean("interviewService"));
        assertNotNull(context.getBean("knowledgeService"));
        assertNotNull(context.getBean("reportService"));
        assertNotNull(context.getBean("qaAgentService"));
    }
}
