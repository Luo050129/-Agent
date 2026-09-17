package com.baishan.interview.dto;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/** 面试会话详情视图。 */
public record SessionView(
        UUID id,
        UUID resumeId,
        String mode,
        String status,
        int questionCount,
        OffsetDateTime createdAt,
        OffsetDateTime endedAt,
        List<MessageView> messages,
        InterviewSummary summary
) {
}
