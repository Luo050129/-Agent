package com.baishan.interview.agent;

import com.baishan.interview.dto.ResumeAnalysis;
import com.baishan.interview.service.KnowledgeService;
import com.baishan.interview.service.ResumeService;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Component;

import java.util.UUID;

/**
 * 智能问答 Agent 可调用的工具集。
 */
@Component
public class KnowledgeTools {

    private final KnowledgeService knowledgeService;
    private final ResumeService resumeService;

    public KnowledgeTools(KnowledgeService knowledgeService, ResumeService resumeService) {
        this.knowledgeService = knowledgeService;
        this.resumeService = resumeService;
    }

    /** 检索知识库（企业介绍、岗位说明、参考资料等），返回按相似度排序的文本片段。 */
    /*  */
    @Tool(description = "检索知识库中与查询最相关的文档片段，返回文本内容与来源文件名。适用于回答企业背景、岗位职责、公司业务相关问题。")
    public String searchKnowledge(
            @ToolParam(description = "检索查询，用自然语言描述想了解的内容") String query,
            @ToolParam(description = "返回结果条数，1-6，默认 3") int topK) {
        int k = Math.max(1, Math.min(6, topK == 0 ? 3 : topK));
        return knowledgeService.formatForPrompt(query, k);
    }

    /** 获取简历的 LLM 结构化分析摘要（技能、优劣势、评分）。 */
    @Tool(description = "获取某份简历的结构化分析结果（候选人信息、技能、优势、不足、评分），用于结合简历内容回答个性化问题。")
    public String getResumeAnalysis(
            @ToolParam(description = "简历 ID（UUID 字符串）") String resumeId) {
        ResumeAnalysis analysis = resumeService.getAnalysis(UUID.fromString(resumeId));
        return analysis.toView().toString();
    }
}
