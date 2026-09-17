package com.baishan.interview.dto;

import java.util.List;
import java.util.Map;

/**
 * 简历分析结果（LLM 结构化输出契约）。
 */
public record ResumeAnalysis(
        String candidateName,
        String targetPosition,
        String summary,
        List<String> skills,
        List<String> strengths,
        List<String> weaknesses,
        List<String> suggestions,
        int overallScore,
        List<DimensionScore> dimensions
) {

    /** 单项能力维度评分。 */
    public record DimensionScore(String name, int score, String comment) {
    }

    /** 兼容输出缺失字段的视图（供前端展示）。 */
    public Map<String, Object> toView() {
        return Map.of(
                "candidateName", candidateName == null ? "" : candidateName,
                "targetPosition", targetPosition == null ? "" : targetPosition,
                "summary", summary == null ? "" : summary,
                "skills", skills == null ? List.of() : skills,
                "strengths", strengths == null ? List.of() : strengths,
                "weaknesses", weaknesses == null ? List.of() : weaknesses,
                "suggestions", suggestions == null ? List.of() : suggestions,
                "overallScore", overallScore,
                "dimensions", dimensions == null ? List.of() : dimensions
        );
    }
}
