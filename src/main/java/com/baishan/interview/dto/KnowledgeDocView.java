package com.baishan.interview.dto;

import com.baishan.interview.domain.KnowledgeDocument;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 知识库文档视图。 */
public record KnowledgeDocView(
        UUID id,
        String fileName,
        String docType,
        Long fileSize,
        int chunkCount,
        OffsetDateTime createdAt
) {

    public static KnowledgeDocView from(KnowledgeDocument d) {
        return new KnowledgeDocView(
                d.getId(),
                d.getFileName(),
                d.getDocType(),
                d.getFileSize(),
                d.getChunkCount(),
                d.getCreatedAt()
        );
    }
}
