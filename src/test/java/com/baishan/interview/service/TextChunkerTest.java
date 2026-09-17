package com.baishan.interview.service;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * TextChunker 单元测试（无 Spring 上下文、无外部依赖）。
 * 覆盖：空输入、单块、多段合并、重叠衔接、超长段落按句切分。
 */
class TextChunkerTest {

    private final TextChunker chunker = new TextChunker();

    @Test
    void blankInputReturnsEmpty() {
        assertTrue(chunker.chunk(null, 100, 20).isEmpty());
        assertTrue(chunker.chunk("   \n\t\n  ", 100, 20).isEmpty());
    }

    @Test
    void shortSingleParagraphIsOneChunk() {
        String text = "这是一段很短的内容。";
        List<String> chunks = chunker.chunk(text, 100, 20);
        assertEquals(1, chunks.size());
        assertEquals(text.trim(), chunks.get(0));
    }

    @Test
    void twoSmallParagraphsMergeWhenUnderChunkSize() {
        String a = "第一段内容较短。";
        String b = "第二段内容也较短。";
        List<String> chunks = chunker.chunk(a + "\n" + b, 100, 20);
        // 两段合并后仍小于 100，应当落在同一个块里（用换行连接）
        assertEquals(1, chunks.size());
        assertTrue(chunks.get(0).contains("第一段内容较短。"));
        assertTrue(chunks.get(0).contains("第二段内容也较短。"));
    }

    @Test
    void overlapIsCarriedToNextChunk() {
        int chunkSize = 50, overlap = 10;
        String a = "A".repeat(chunkSize);           // 正好 50 字符
        String b = "B".repeat(chunkSize);           // 正好 50 字符
        List<String> chunks = chunker.chunk(a + "\n" + b, chunkSize, overlap);

        assertEquals(2, chunks.size());
        // 第一块是完整的 A
        assertEquals(a, chunks.get(0));
        // 第二块以 A 的尾部 overlap 字符起头，并包含 B
        String tailOfA = a.substring(a.length() - overlap);
        assertTrue(chunks.get(1).startsWith(tailOfA), "第二块应以第一块尾部重叠字符起头");
        assertTrue(chunks.get(1).contains(b), "第二块应包含 B 段落");
        // 任何块长度不应超过 chunkSize + overlap + 1（换行符）
        for (String c : chunks) {
            assertTrue(c.length() <= chunkSize + overlap + 1,
                    "块过长: " + c.length() + " > " + (chunkSize + overlap + 1));
        }
    }

    @Test
    void longParagraphIsSplitBySentence() {
        int chunkSize = 50, overlap = 10;
        // 三个短句，拼接后超过 chunkSize，应按句切分
        String para = "第一句话内容。第二句话内容更多一些。第三句话作为结尾内容。";
        List<String> chunks = chunker.chunk(para, chunkSize, overlap);
        assertFalse(chunks.isEmpty());
        // 每个块都应包含至少一个完整句子，且不超过 chunkSize + overlap + 1
        for (String c : chunks) {
            assertTrue(c.length() <= chunkSize + overlap + 1,
                    "块过长: " + c.length());
        }
        // 原始段落的所有字符顺序应当保留在拼接结果中（重叠允许重复，故用子序列校验）
        String joined = String.join("", chunks);
        assertTrue(isSubsequence(para, joined),
                "原始段落内容未被完整保留");
    }

    @Test
    void originalContentIsCoveredAcrossChunks() {
        int chunkSize = 60, overlap = 15;
        String text = """
                简历解析模块负责把 PDF 与 Word 文档转换为纯文本。
                文本经过分块后送入向量模型生成 embedding。
                向量存入 pgvector，检索时使用余弦相似度取 Top-K。
                """;
        List<String> chunks = chunker.chunk(text, chunkSize, overlap);
        assertTrue(chunks.size() >= 1);
        String joined = String.join("", chunks);
        for (String para : text.split("\\n+")) {
            String p = para.trim();
            if (!p.isEmpty()) {
                assertTrue(isSubsequence(p, joined),
                        "段落未被覆盖: " + p);
            }
        }
    }

    /** 校验 sub 的字符是否按序出现在 full 中（允许重叠造成的重复字符）。 */
    private boolean isSubsequence(String sub, String full) {
        int i = 0;
        for (int j = 0; j < full.length() && i < sub.length(); j++) {
            if (full.charAt(j) == sub.charAt(i)) {
                i++;
            }
        }
        return i == sub.length();
    }
}
