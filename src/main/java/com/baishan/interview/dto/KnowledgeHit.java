package com.baishan.interview.dto;

import com.baishan.interview.repository.VectorRepository;

/** 知识库检索命中。 */
public record KnowledgeHit(
        long chunkId,
        java.util.UUID documentId,
        String fileName,
        String content,
        double score
) {

    public static KnowledgeHit from(VectorRepository.ChunkHit h) {
        return new KnowledgeHit(h.id(), h.documentId(), h.fileName(), h.content(), h.score());
    }
}
