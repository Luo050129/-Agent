package com.baishan.interview.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;

/** 基础设施配置。 */
@Configuration
@EnableConfigurationProperties(AppProperties.class)
public class InfraConfig {

    /** 字符串键值 RedisTemplate，用于业务缓存（分析结果 / 检索结果）。 */
    @Bean
    public StringRedisTemplate stringRedisTemplate(RedisConnectionFactory factory) {
        return new StringRedisTemplate(factory);
    }
}
