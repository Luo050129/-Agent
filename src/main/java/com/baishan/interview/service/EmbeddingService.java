package com.baishan.interview.service;

import com.baishan.interview.config.AppProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * Embedding 向量化封装。
 * 模型端点 / 维度由 application.yml 的 spring.ai.openai.embedding.* 决定。
 */
@Service
public class EmbeddingService {

    private static final Logger log = LoggerFactory.getLogger(EmbeddingService.class);

    private final EmbeddingModel embeddingModel;
    private final AppProperties appProperties;

    public EmbeddingService(EmbeddingModel embeddingModel, AppProperties appProperties) {
        this.embeddingModel = embeddingModel;
        this.appProperties = appProperties;
    }

    /** 文本向量化。 */
    public float[] embed(String text) {
        float[] vector = embeddingModel.embed(text);
        checkDimension(vector);
        return vector;
    }

    /** 批量文本向量化（单次 API 请求，降低调用成本）。 */
    public List<float[]> embedBatch(List<String> texts) {
        List<float[]> vectors = embeddingModel.embed(texts);
        if (!vectors.isEmpty()) {
            checkDimension(vectors.get(0));
        }
        return vectors;
    }

    private void checkDimension(float[] vector) {
        int expected = appProperties.getRag().getEmbeddingDimensions();
        if (vector != null && vector.length != expected) {
            log.warn("embedding 维度 {} 与配置 {} 不一致，请检查模型与 V1__init.sql 的 vector(N)",
                    vector.length, expected);
        }
    }
}
