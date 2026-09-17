package com.baishan.interview.dto;

import jakarta.validation.constraints.NotNull;

/** 创建面试会话请求。 */
public record CreateSessionRequest(
        @NotNull(message = "resumeId 不能为空") java.util.UUID resumeId,
        String mode,
        String jdText
) {
}
