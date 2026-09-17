package com.baishan.interview.dto;

import java.util.List;

/**
 * 整场面试总结（LLM 结构化输出契约）。
 */
public record InterviewSummary(
        int overallScore,
        String level,
        String overallEvaluation,
        List<String> strengths,
        List<String> weaknesses,
        List<String> developmentSuggestions,
        String hiringAdvice
) {
}
