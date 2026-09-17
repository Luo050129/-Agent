package com.baishan.interview.exception;

/** AI 服务调用失败（模型不可达 / 超时 / 输出解析失败）。 */
public class AiCallException extends RuntimeException {

    public AiCallException(String message) {
        super(message);
    }

    public AiCallException(String message, Throwable cause) {
        super(message, cause);
    }
}
