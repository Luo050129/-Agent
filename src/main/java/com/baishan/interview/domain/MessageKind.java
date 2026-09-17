package com.baishan.interview.domain;

/** 面试消息类型。 */
public enum MessageKind {
    /** 面试官提问 */
    QUESTION,
    /** 候选人回答 */
    ANSWER,
    /** 评估结果 */
    EVALUATION,
    /** 系统提示（开场白等） */
    SYSTEM
}
