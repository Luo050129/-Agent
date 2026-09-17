package com.baishan.interview.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * 轻量 JSON 缓存（Redis）。
 * 用于缓存简历分析结果与知识库检索结果，避免重复调用 LLM / embedding。
 * Redis 不可用时静默降级（缓存是优化而非正确性依赖），不影响业务。
 */
@Service
public class CacheService {

    private static final Logger log = LoggerFactory.getLogger(CacheService.class);

    private static final String PREFIX = "baishan:";

    private final StringRedisTemplate redis;

    public CacheService(StringRedisTemplate redis) {
        this.redis = redis;
    }

    public void put(String key, String json, Duration ttl) {
        try {
            redis.opsForValue().set(PREFIX + key, json, ttl);
        } catch (Exception e) {
            log.warn("Redis 写入失败（降级）: {}", e.getMessage());
        }
    }

    public String get(String key) {
        try {
            return redis.opsForValue().get(PREFIX + key);
        } catch (Exception e) {
            log.warn("Redis 读取失败（降级）: {}", e.getMessage());
            return null;
        }
    }

    public void evict(String key) {
        try {
            redis.delete(PREFIX + key);
        } catch (Exception e) {
            log.warn("Redis 删除失败（降级）: {}", e.getMessage());
        }
    }

    /** 按前缀删除（SCAN 匹配），用于知识库变更后清空相关检索缓存。 */
    public void evictByPrefix(String keyPrefix) {
        try {
            String pattern = PREFIX + keyPrefix + "*";
            List<String> keys = new ArrayList<>();
            try (var cursor = redis.scan(ScanOptions.scanOptions().match(pattern).count(200).build())) {
                cursor.forEachRemaining(keys::add);
            }
            if (!keys.isEmpty()) {
                redis.delete(keys);
            }
        } catch (Exception e) {
            log.warn("Redis 前缀清理失败（降级）: {}", e.getMessage());
        }
    }
}
