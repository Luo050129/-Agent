package com.baishan.interview.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

/**
 * knowledge_chunk 的向量存取与余弦相似度检索（pgvector）。
 * embedding 列不在 JPA 映射内，统一走原生 SQL。
 */
@Repository
public class VectorRepository {

    private final JdbcTemplate jdbc;

    public VectorRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 插入一个分块，embedding 以 pgvector 文本格式 [0.1,0.2,...] 传入。 */
    public void insertChunk(UUID documentId, int chunkIndex, String content, float[] embedding) {
        String vectorLiteral = toPgVector(embedding);
        jdbc.update("""
                INSERT INTO knowledge_chunk (document_id, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?::vector)
                """, documentId, chunkIndex, content, vectorLiteral);
    }

    /** 按余弦距离检索最相似的 Top-K 分块。 */
    public List<ChunkHit> similaritySearch(float[] queryEmbedding, int topK) {
        String vectorLiteral = toPgVector(queryEmbedding);
        return jdbc.query("""
                        SELECT c.id, c.document_id, c.chunk_index, c.content, d.file_name,
                               1 - (c.embedding <=> ?::vector) AS score
                        FROM knowledge_chunk c
                        JOIN knowledge_document d ON d.id = c.document_id
                        ORDER BY c.embedding <=> ?::vector
                        LIMIT ?
                        """,
                (rs, i) -> new ChunkHit(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("document_id")),
                        rs.getInt("chunk_index"),
                        rs.getString("content"),
                        rs.getString("file_name"),
                        rs.getDouble("score")),
                vectorLiteral, vectorLiteral, topK);
    }

    public void deleteByDocumentId(UUID documentId) {
        jdbc.update("DELETE FROM knowledge_chunk WHERE document_id = ?", documentId);
    }

    public int countByDocumentId(UUID documentId) {
        Integer n = jdbc.queryForObject(
                "SELECT count(*) FROM knowledge_chunk WHERE document_id = ?",
                Integer.class, documentId);
        return n == null ? 0 : n;
    }

    private String toPgVector(float[] embedding) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < embedding.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(embedding[i]);
        }
        return sb.append(']').toString();
    }

    /** 检索命中结果。 */
    public record ChunkHit(long id, UUID documentId, int chunkIndex, String content,
                           String fileName, double score) {
    }
}
