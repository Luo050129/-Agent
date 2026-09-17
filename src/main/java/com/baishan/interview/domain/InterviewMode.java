package com.baishan.interview.domain;

/** 面试模式。 */
public enum InterviewMode {
    /** 技术面：围绕专业技能、项目细节、技术深度 */
    TECHNICAL,
    /** 行为面：围绕经历、动机、软技能（HR 面） */
    BEHAVIORAL,
    /** 综合面：技术与行为混合 */
    MIXED;

    public static boolean isValid(String mode) {
        for (InterviewMode m : values()) {
            if (m.name().equalsIgnoreCase(mode)) {
                return true;
            }
        }
        return false;
    }

    public static InterviewMode of(String mode) {
        return InterviewMode.valueOf(mode.toUpperCase());
    }
}
