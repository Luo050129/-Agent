package com.baishan.interview.service;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * 朴素文本分块：按段落切分，合并至目标大小，带重叠。
 * 适合简历/知识库文档的 RAG 预处理。
 */
@Component
public class TextChunker {

    public List<String> chunk(String text, int chunkSize, int overlap) {
        List<String> result = new ArrayList<>();
        if (text == null || text.isBlank()) {
            return result;
        }
        // 按换行分段，段内再按句切分，避免切碎句子
        String[] paragraphs = text.split("\\n+");
        StringBuilder current = new StringBuilder();
        for (String para : paragraphs) {
            String p = para.trim();
            if (p.isEmpty()) {
                continue;
            }
            if (current.length() + p.length() + 1 > chunkSize && current.length() > 0) {
                result.add(current.toString().trim());
                // 保留尾部 overlap 字符用于衔接
                current = new StringBuilder(tail(current.toString(), overlap));
            }
            if (p.length() > chunkSize) {
                // 超长段落：按句子切分
                for (String sentence : splitLong(p, chunkSize, overlap)) {
                    result.add(sentence);
                }
                current = new StringBuilder(tail(p, overlap));
            } else {
                if (current.length() > 0) {
                    current.append('\n');
                }
                current.append(p);
            }
        }
        if (current.length() > 0) {
            result.add(current.toString().trim());
        }
        return result;
    }

    private String tail(String s, int n) {
        if (s.length() <= n) {
            return s;
        }
        int idx = s.length() - n;
        return s.substring(idx);
    }

    private List<String> splitLong(String p, int chunkSize, int overlap) {
        List<String> parts = new ArrayList<>();
        String[] sentences = p.split("(?<=[。！？!?；;])");
        StringBuilder cur = new StringBuilder();
        for (String s : sentences) {
            if (cur.length() + s.length() > chunkSize && cur.length() > 0) {
                parts.add(cur.toString().trim());
                cur = new StringBuilder(tail(cur.toString(), overlap));
            }
            cur.append(s);
        }
        if (cur.length() > 0) {
            parts.add(cur.toString().trim());
        }
        return parts;
    }
}
