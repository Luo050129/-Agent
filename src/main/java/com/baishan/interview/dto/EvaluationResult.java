package com.baishan.interview.dto;

import java.util.List;

/**
 * 单题回答评估结果（LLM 结构化输出契约）。
 */
public record EvaluationResult(
        int score,
        String level,
        String feedback,
        List<String> strengths,
        List<String> improvements,
        String nextFocus
) {
}
