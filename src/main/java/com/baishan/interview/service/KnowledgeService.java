package com.baishan.interview.service;

import com.baishan.interview.config.AppProperties;
import com.baishan.interview.domain.KnowledgeDocument;
import com.baishan.interview.dto.KnowledgeDocView;
import com.baishan.interview.dto.KnowledgeHit;
import com.baishan.interview.exception.NotFoundException;
import com.baishan.interview.repository.KnowledgeDocumentRepository;
import com.baishan.interview.repository.VectorRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 知识库服务：文档上传 → Tika 解析 → 分块 → 向量化 → pgvector 存储；
 * 检索走余弦相似度 Top-K，结果短时缓存于 Redis。
 */
@Service
public class KnowledgeService {

    private static final Logger log = LoggerFactory.getLogger(KnowledgeService.class);

    private final DocumentParsingService parsingService;
    private final TextChunker chunker;
    private final EmbeddingService embeddingService;
    private final VectorRepository vectorRepository;
    private final KnowledgeDocumentRepository documentRepository;
    private final CacheService cacheService;
    private final JsonService jsonService;
    private final AppProperties appProperties;

    public KnowledgeService(DocumentParsingService parsingService,
                            TextChunker chunker,
                            EmbeddingService embeddingService,
                            VectorRepository vectorRepository,
                            KnowledgeDocumentRepository documentRepository,
                            CacheService cacheService,
                            JsonService jsonService,
                            AppProperties appProperties) {
        this.parsingService = parsingService;
        this.chunker = chunker;
        this.embeddingService = embeddingService;
        this.vectorRepository = vectorRepository;
        this.documentRepository = documentRepository;
        this.cacheService = cacheService;
        this.jsonService = jsonService;
        this.appProperties = appProperties;
    }

    @Transactional
    public KnowledgeDocView upload(MultipartFile file, String docType) {
        if (!appProperties.getRag().isEnabled()) {
            throw new IllegalArgumentException("知识库功能未启用（当前为本地演示模式，不依赖 pgvector）。完整模式请用 docker compose 启动 PostgreSQL 后以默认 profile 运行。");
        }
        DocumentParsingService.ParsedText parsed = parsingService.parse(file, appProperties.getUpload().getMaxFileSize());
        UUID docId = UUID.randomUUID();
        KnowledgeDocument doc = new KnowledgeDocument(
                docId, file.getOriginalFilename(), parsed.mimeType(), file.getSize(), docType);
        doc.setCreatedAt(OffsetDateTime.now());
        documentRepository.save(doc);

        List<String> chunks = chunker.chunk(parsed.text(),
                appProperties.getRag().getChunkSize(), appProperties.getRag().getChunkOverlap());
        if (chunks.isEmpty()) {
            throw new IllegalArgumentException("文档内容过短，无法分块");
        }

        List<float[]> vectors = embeddingService.embedBatch(chunks);
        for (int i = 0; i < chunks.size(); i++) {
            vectorRepository.insertChunk(docId, i, chunks.get(i), vectors.get(i));
        }
        doc.setChunkCount(chunks.size());
        documentRepository.save(doc);

        log.info("知识库文档入库 {}：{} 分块", file.getOriginalFilename(), chunks.size());
        cacheService.evictByPrefix("kb-search:");
        return KnowledgeDocView.from(doc);
    }

    @Transactional
    public void delete(UUID documentId) {
        if (!documentRepository.existsById(documentId)) {
            throw new NotFoundException("文档不存在: " + documentId);
        }
        vectorRepository.deleteByDocumentId(documentId);
        documentRepository.deleteById(documentId);
        cacheService.evictByPrefix("kb-search:");
    }

    public List<KnowledgeDocView> list() {
        return documentRepository.findAll().stream().map(KnowledgeDocView::from).toList();
    }

    /** 向量相似度检索，命中结果短时缓存。 */
    public List<KnowledgeHit> search(String query, int topK) {
        if (!appProperties.getRag().isEnabled()) {
            return List.of();
        }
        String cacheKey = "kb-search:" + query.trim().hashCode() + ":" + topK;
        String cached = cacheService.get(cacheKey);
        if (cached != null) {
            return jsonService.fromJson(cached, JsonService.KnowledgeHitList.class) == null
                    ? List.of()
                    : jsonService.fromJson(cached, JsonService.KnowledgeHitList.class).hits();
        }

        float[] queryVector = embeddingService.embed(query);
        List<KnowledgeHit> hits = vectorRepository
                .similaritySearch(queryVector, topK)
                .stream()
                .map(KnowledgeHit::from)
                .toList();

        cacheService.put(cacheKey, jsonService.toJson(new JsonService.KnowledgeHitList(hits)), Duration.ofMinutes(5));
        return hits;
    }

    /** 将 Top-K 检索命中拼接为可注入提示词的文本。 */
    public String formatForPrompt(String query, int topK) {
        if (!appProperties.getRag().isEnabled()) {
            return "(知识库未启用：当前为本地演示模式，无企业资料可检索)";
        }
        List<KnowledgeHit> hits = search(query, topK);
        if (hits.isEmpty()) {
            return "(知识库无相关内容)";
        }
        StringBuilder sb = new StringBuilder();
        for (KnowledgeHit h : hits) {
            sb.append("【来源：").append(h.fileName()).append("】\n")
                    .append(h.content()).append("\n\n");
        }
        return sb.toString().trim();
    }
}
