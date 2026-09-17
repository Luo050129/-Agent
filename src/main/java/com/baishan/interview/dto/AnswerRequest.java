package com.baishan.interview.dto;

import jakarta.validation.constraints.NotBlank;

/** 提交回答请求。 */
public record AnswerRequest(
        @NotBlank(message = "回答内容不能为空") String content
) {
}
