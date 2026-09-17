package com.baishan.interview.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.Cursor;
import org.springframework.data.redis.core.ScanOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;
import java.util.List;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * CacheService 单元测试（Mock StringRedisTemplate）。
 * 重点验证：缓存 key 前缀、命中/未命中、以及 Redis 异常时的静默降级。
 */
@ExtendWith(MockitoExtension.class)
class CacheServiceTest {

    @Mock
    private StringRedisTemplate redis;

    @Mock
    private ValueOperations<String, String> valueOps;

    @InjectMocks
    private CacheService cacheService;

    @Test
    void putAddsPrefixAndTtl() {
        when(redis.opsForValue()).thenReturn(valueOps);
        cacheService.put("resume:1", "{\"score\":82}", Duration.ofHours(6));
        verify(valueOps).set(eq("baishan:resume:1"), eq("{\"score\":82}"), eq(Duration.ofHours(6)));
    }

    @Test
    void getReturnsStoredValue() {
        when(redis.opsForValue()).thenReturn(valueOps);
        when(valueOps.get("baishan:kb-search:x:4")).thenReturn("cached");
        assertEquals("cached", cacheService.get("kb-search:x:4"));
    }

    @Test
    void getReturnsNullOnRedisFailure() {
        when(redis.opsForValue()).thenThrow(new RuntimeException("Redis down"));
        // 降级：不应抛异常，返回 null 让上层回退到主流程
        assertNull(cacheService.get("any-key"));
    }

    @Test
    void evictDeletesWithPrefix() {
        cacheService.evict("resume:1");
        verify(redis).delete("baishan:resume:1");
    }

    @Test
    void evictByPrefixScansAndDeletesMatchingKeys() {
        Cursor<String> cursor = mock(Cursor.class);
        when(redis.scan(any(ScanOptions.class))).thenReturn(cursor);
        // 模拟 SCAN 游标只吐出一个命中键
        doAnswer(inv -> {
            Consumer<String> consumer = inv.getArgument(0);
            consumer.accept("baishan:kb-search:abc:4");
            return null;
        }).when(cursor).forEachRemaining(any(Consumer.class));

        cacheService.evictByPrefix("kb-search:");

        ArgumentCaptor<List<String>> captor = ArgumentCaptor.forClass(List.class);
        verify(redis).delete(captor.capture());
        assertEquals(List.of("baishan:kb-search:abc:4"), captor.getValue());
    }

    @Test
    void evictByPrefixDegradesWhenScanFails() {
        when(redis.scan(any(ScanOptions.class))).thenThrow(new RuntimeException("scan error"));
        // 降级：前缀清理失败不应向上抛异常
        assertDoesNotThrow(() -> cacheService.evictByPrefix("kb-search:"));
    }

    @Test
    void putDegradesWhenRedisFails() {
        when(redis.opsForValue()).thenThrow(new RuntimeException("Redis down"));
        // 写入失败应被吞掉，不影响主流程
        assertDoesNotThrow(() -> cacheService.put("k", "v", Duration.ofMinutes(5)));
        verify(redis, times(1)).opsForValue();
    }
}
