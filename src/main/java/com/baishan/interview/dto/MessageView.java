package com.baishan.interview.dto;

import com.baishan.interview.domain.InterviewMessage;

import java.time.OffsetDateTime;

/** 面试消息视图。 */
public record MessageView(
        long id,
        String role,
        String kind,
        String content,
        EvaluationResult evaluation,
        Integer score,
        OffsetDateTime createdAt
) {

    public static MessageView from(InterviewMessage m) {
        return new MessageView(
                m.getId(),
                m.getRole(),
                m.getKind(),
                m.getContent(),
                null,
                m.getScore(),
                m.getCreatedAt()
        );
    }
}
