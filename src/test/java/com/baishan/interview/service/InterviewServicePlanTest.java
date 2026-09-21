package com.baishan.interview.service;

import com.baishan.interview.config.AppProperties;
import com.baishan.interview.domain.InterviewMessage;
import com.baishan.interview.domain.InterviewSession;
import com.baishan.interview.domain.MessageKind;
import com.baishan.interview.dto.InterviewPlan;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * InterviewService 出题提示词单元测试。
 * 重点验证：题目计划（InterviewPlan）被注入为题目主题硬约束，
 * 且提示词包含"覆盖分散 / 同一项目不连问超 1 题"的约束。
 */
@ExtendWith(MockitoExtension.class)
class InterviewServicePlanTest {

    @Mock
    private com.baishan.interview.repository.InterviewSessionRepository sessionRepository;
    @Mock
    private com.baishan.interview.repository.InterviewMessageRepository messageRepository;
    @Mock
    private com.baishan.interview.service.ResumeService resumeService;
    @Mock
    private com.baishan.interview.service.KnowledgeService knowledgeService;
    @Mock
    private org.springframework.ai.chat.client.ChatClient.Builder chatClientBuilder;

    private final JsonService jsonService = new JsonService();
    private final AppProperties appProperties = new AppProperties();
    private InterviewService interviewService;

    @BeforeEach
    void setUp() {
        interviewService = new InterviewService(
                sessionRepository, messageRepository, resumeService, knowledgeService,
                chatClientBuilder, jsonService, appProperties);
    }

    @Test
    void askPrompt_includesPlannedTopicGroundAndCoverageRules() {
        InterviewPlan plan = new InterviewPlan(List.of(
                new InterviewPlan.InterviewPlanItem(
                        "项目：白山面试辅助 Agent", "整体架构与 Spring AI 工具调用",
                        "简历该项目描述 pgvector RAG 链路；JD 要求 Java + AI 应用", "PROJECT"),
                new InterviewPlan.InterviewPlanItem(
                        "技能：Redis 缓存", "缓存击穿与分布式锁",
                        "JD 要求高并发经验", "SKILL")
        ));
        InterviewSession session = new InterviewSession();
        session.setMode("MIXED");
        session.setContextJson(jsonService.toJson(Map.of("questionPlan", jsonService.toJson(plan))));

        InterviewMessage q1 = new InterviewMessage();
        q1.setKind(MessageKind.QUESTION.name());
        q1.setContent("请介绍一下你的白山项目？");
        when(messageRepository.findBySessionIdOrderByIdAsc(any())).thenReturn(List.of(q1));

        String prompt = interviewService.buildAskPrompt(session, 2);

        // 计划主题被注入为硬约束
        assertThat(prompt).contains("技能：Redis 缓存");
        assertThat(prompt).contains("JD 要求高并发经验");
        assertThat(prompt).contains("必须围绕此主题出题");
        // 覆盖分散约束
        assertThat(prompt).contains("不要在同一项目上连续追问超过 1 题");
        // 已问列表透传
        assertThat(prompt).contains("请介绍一下你的白山项目？");
    }

    @Test
    void askPrompt_withoutPlanFallsBackToCoverageHint() {
        InterviewSession session = new InterviewSession();
        session.setMode("TECHNICAL");
        session.setContextJson(jsonService.toJson(Map.of("resumeAnalysis", Map.of())));
        when(messageRepository.findBySessionIdOrderByIdAsc(any())).thenReturn(List.of());

        String prompt = interviewService.buildAskPrompt(session, 1);

        assertThat(prompt).contains("无题目计划");
        assertThat(prompt).contains("覆盖不同模块");
    }
}
