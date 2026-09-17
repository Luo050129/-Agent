package com.baishan.interview.dto;

import com.baishan.interview.domain.Resume;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 简历视图。 */
public record ResumeView(
        UUID id,
        String fileName,
        String mimeType,
        Long fileSize,
        boolean analyzed,
        Integer overallScore,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {

    public static ResumeView from(Resume r) {
        return new ResumeView(
                r.getId(),
                r.getFileName(),
                r.getMimeType(),
                r.getFileSize(),
                r.getAnalysisJson() != null,
                r.getOverallScore(),
                r.getCreatedAt(),
                r.getUpdatedAt()
        );
    }
}
