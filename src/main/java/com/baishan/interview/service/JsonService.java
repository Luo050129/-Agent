package com.baishan.interview.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

/**
 * Jackson 3（tools.jackson）JSON 工具封装。
 * Spring Boot 4 / Spring AI 2 均基于 Jackson 3。
 */
@Service
public class JsonService {

    private static final Logger log = LoggerFactory.getLogger(JsonService.class);

    private final ObjectMapper mapper = JsonMapper.builder().build();

    public <T> T fromJson(String json, Class<T> type) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return mapper.readValue(json, type);
        } catch (Exception e) {
            log.warn("JSON 反序列化失败: {}", e.getMessage());
            return null;
        }
    }

    public String toJson(Object value) {
        try {
            return mapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("JSON 序列化失败", e);
        }
    }

    /** 知识库检索结果列表包装（缓存反序列化用）。 */
    public record KnowledgeHitList(java.util.List<com.baishan.interview.dto.KnowledgeHit> hits) {
    }
}
