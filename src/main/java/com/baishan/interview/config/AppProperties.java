package com.baishan.interview.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 业务配置（app.*），映射 application.yml 中的 app.interview / app.rag / app.upload。
 */
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private final Interview interview = new Interview();
    private final Rag rag = new Rag();
    private final Upload upload = new Upload();

    public Interview getInterview() {
        return interview;
    }

    public Rag getRag() {
        return rag;
    }

    public Upload getUpload() {
        return upload;
    }

    public static class Interview {
        private int questionCount = 5;
        private int maxAnswerLength = 5000;

        public int getQuestionCount() {
            return questionCount;
        }

        public void setQuestionCount(int questionCount) {
            this.questionCount = questionCount;
        }

        public int getMaxAnswerLength() {
            return maxAnswerLength;
        }

        public void setMaxAnswerLength(int maxAnswerLength) {
            this.maxAnswerLength = maxAnswerLength;
        }
    }

    public static class Rag {
        /** 是否启用知识库 RAG（本地演示模式关闭，跳过 pgvector 依赖）。 */
        private boolean enabled = true;
        private int chunkSize = 500;
        private int chunkOverlap = 80;
        private int topK = 4;
        private int embeddingDimensions = 1024;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public int getChunkSize() {
            return chunkSize;
        }

        public void setChunkSize(int chunkSize) {
            this.chunkSize = chunkSize;
        }

        public int getChunkOverlap() {
            return chunkOverlap;
        }

        public void setChunkOverlap(int chunkOverlap) {
            this.chunkOverlap = chunkOverlap;
        }

        public int getTopK() {
            return topK;
        }

        public void setTopK(int topK) {
            this.topK = topK;
        }

        public int getEmbeddingDimensions() {
            return embeddingDimensions;
        }

        public void setEmbeddingDimensions(int embeddingDimensions) {
            this.embeddingDimensions = embeddingDimensions;
        }
    }

    public static class Upload {
        private long maxFileSize = 10 * 1024 * 1024;

        public long getMaxFileSize() {
            return maxFileSize;
        }

        public void setMaxFileSize(long maxFileSize) {
            this.maxFileSize = maxFileSize;
        }
    }
}
