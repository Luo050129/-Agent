package com.baishan.interview.service;

import com.baishan.interview.config.AppProperties;
import com.baishan.interview.dto.KnowledgeHit;
import com.baishan.interview.repository.KnowledgeDocumentRepository;
import com.baishan.interview.repository.VectorRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.anyInt;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * KnowledgeService 检索逻辑单元测试（Mock 掉 Embedding / VectorRepository / Cache / Json）。
 * 验证：RAG 开启/关闭分支、缓存命中跳过向量化、Top-K 截断、提示词拼接格式。
 * 不依赖 pgvector / Redis / 真实模型。
 */
@ExtendWith(MockitoExtension.class)
class KnowledgeServiceRetrievalTest {

    @Mock
    private DocumentParsingService parsingService;
    @Mock
    private TextChunker chunker;
    @Mock
    private EmbeddingService embeddingService;
    @Mock
    private VectorRepository vectorRepository;
    @Mock
    private KnowledgeDocumentRepository documentRepository;
    @Mock
    private CacheService cacheService;
    @Mock
    private JsonService jsonService;

    private final AppProperties appProperties = new AppProperties();
    private KnowledgeService knowledgeService;

    @BeforeEach
    void setUp() {
        knowledgeService = new KnowledgeService(
                parsingService, chunker, embeddingService, vectorRepository,
                documentRepository, cacheService, jsonService, appProperties);
    }

    @Test
    void searchReturnsHitsWhenRagEnabled() {
        appProperties.getRag().setEnabled(true);
        float[] qv = {0.1f, 0.2f};
        VectorRepository.ChunkHit hit = new VectorRepository.ChunkHit(
                1L, UUID.randomUUID(), 0, "Spring Bean 的初始化回调包括 InitMethod 与 InitializingBean。",
                "spring.md", 0.95);

        when(cacheService.get(anyString())).thenReturn(null);
        when(embeddingService.embed("Spring Bean 初始化回调有哪些？")).thenReturn(qv);
        when(vectorRepository.similaritySearch(qv, 4)).thenReturn(List.of(hit));

        List<KnowledgeHit> hits = knowledgeService.search("Spring Bean 初始化回调有哪些？", 4);

        assertEquals(1, hits.size());
        assertEquals("spring.md", hits.get(0).fileName());
        assertTrue(hits.get(0).content().contains("InitializingBean"));
    }

    @Test
    void searchSkipsEmbeddingWhenCacheHit() {
        appProperties.getRag().setEnabled(true);
        KnowledgeHit cached = new KnowledgeHit(9L, UUID.randomUUID(), "cached.md", "缓存命中的内容", 0.88);
        when(cacheService.get(anyString())).thenReturn("cached-json");
        when(jsonService.fromJson(anyString(), eq(JsonService.KnowledgeHitList.class)))
                .thenReturn(new JsonService.KnowledgeHitList(List.of(cached)));

        List<KnowledgeHit> hits = knowledgeService.search("任意查询", 4);

        assertEquals("缓存命中的内容", hits.get(0).content());
        // 命中缓存就不应再调用 embedding 与向量检索
        verify(embeddingService, never()).embed(any());
        verify(vectorRepository, never()).similaritySearch(any(), anyInt());
    }

    @Test
    void searchReturnsEmptyWhenRagDisabled() {
        appProperties.getRag().setEnabled(false);
        assertTrue(knowledgeService.search("任何问题", 4).isEmpty());
    }

    @Test
    void formatForPromptReturnsDisabledMessageWhenRagOff() {
        appProperties.getRag().setEnabled(false);
        String prompt = knowledgeService.formatForPrompt("问题", 4);
        assertTrue(prompt.contains("未启用"));
    }

    @Test
    void formatForPromptReturnsNoContentWhenEmpty() {
        appProperties.getRag().setEnabled(true);
        when(cacheService.get(anyString())).thenReturn(null);
        when(embeddingService.embed(any())).thenReturn(new float[]{0.1f});
        when(vectorRepository.similaritySearch(any(), anyInt())).thenReturn(List.of());

        String prompt = knowledgeService.formatForPrompt("问题", 4);
        assertTrue(prompt.contains("无相关内容"));
    }

    @Test
    void formatForPromptIncludesSourceAndContent() {
        appProperties.getRag().setEnabled(true);
        float[] qv = {0.1f};
        VectorRepository.ChunkHit hit = new VectorRepository.ChunkHit(
                1L, UUID.randomUUID(), 0, "布隆过滤器可用于拦截缓存穿透。", "redis.md", 0.9);
        when(cacheService.get(anyString())).thenReturn(null);
        when(embeddingService.embed(any())).thenReturn(qv);
        when(vectorRepository.similaritySearch(qv, 4)).thenReturn(List.of(hit));

        String prompt = knowledgeService.formatForPrompt("缓存穿透怎么办", 4);
        assertTrue(prompt.contains("【来源：redis.md】"));
        assertTrue(prompt.contains("布隆过滤器"));
    }
}
