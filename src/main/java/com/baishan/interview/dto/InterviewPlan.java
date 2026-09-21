package com.baishan.interview.dto;

import java.util.List;

/**
 * 面试题目计划：创建会话时基于「简历分析 + 岗位 JD + 面试模式 + 题数」生成，
 * 作为整场面试的题目骨架。
 *
 * 作用：
 * 1. 保证题目覆盖简历各模块（每个项目至少一次）与岗位核心能力，避免单点深挖；
 * 2. 每题携带 ground 锚点，强制紧扣具体简历经历 / JD 要求，杜绝泛泛而问。
 */
public record InterviewPlan(List<InterviewPlanItem> items) {

    /**
     * 单个题目的出题意图。
     *
     * @param topic  题目主题，例如「项目：白山面试辅助 Agent」「技能：Redis 缓存设计」
     * @param angle  出题角度，例如「整体架构与 Spring AI 工具调用设计」
     * @param ground 简历 / 岗位锚点：本题必须紧扣的具体经历或 JD 能力，禁止泛泛而谈
     * @param type   题目类型：PROJECT（项目经历）/ SKILL（技术能力）/ BEHAVIORAL（行为软技能）/ JD_GAP（JD 要求但简历偏弱）
     */
    public record InterviewPlanItem(String topic, String angle, String ground, String type) {
    }
}
