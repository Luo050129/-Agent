package com.baishan.interview.repository;

import com.baishan.interview.domain.KnowledgeDocument;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface KnowledgeDocumentRepository extends JpaRepository<KnowledgeDocument, UUID> {
}
