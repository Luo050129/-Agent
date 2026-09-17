package com.baishan.interview.evaluation;

import com.baishan.interview.service.EmbeddingService;
import com.baishan.interview.service.TextChunker;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * RAG 检索质量评估（黄金集 recall@K）。
 *
 * 目的：用一组「查询 -> 期望命中文档」的黄金集，量化知识库检索是否能把正确的分块
 * 排进 Top-K。它直接验证「分块 + bge-m3 向量化 + 余弦相似度排序」这条检索主链路的质量，
 * 而不依赖 pgvector / Docker —— 余弦相似度在测试内用 Java 计算，因此只需 embedding API 即可运行。
 *
 * 注意（防个人信息泄露）：
 *   - 语料为【完全合成】的通用面试题库，不含任何真实简历 / 姓名 / 联系方式。
 *   - 真实简历在 data/ 下，已被 .gitignore 排除，绝不会进入本测试或仓库。
 *
 * 运行条件：仅当环境变量 EMBEDDING_API_KEY 存在时执行；否则整类跳过（不会让 CI 失败）。
 * 评估阈值 recall@4 >= 0.8 为保守下限（黄金集精心构造，理想应为 1.0），便于发现检索退化。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE, properties = {
        "spring.datasource.url=jdbc:h2:mem:rageval;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "spring.flyway.enabled=false",
        "spring.jpa.hibernate.ddl-auto=create-drop",
        "spring.ai.openai.api-key=sk-dummy"
})
@EnabledIfEnvironmentVariable(named = "EMBEDDING_API_KEY", matches = ".+")
class RagRetrievalEvalTest {

    private static final Logger log = LoggerFactory.getLogger(RagRetrievalEvalTest.class);

    @Autowired
    private EmbeddingService embeddingService;
    @Autowired
    private TextChunker chunker;

    private static final int CHUNK_SIZE = 200;
    private static final int CHUNK_OVERLAP = 40;
    private static final int TOP_K = 4;

    /** 合成语料：每篇一个通用技术主题，无任何个人信息。 */
    private static final String[] CORPUS = {
            "Java HashMap 底层基于数组加链表或红黑树实现。put 时先计算 key 的 hash，"
                    + "再定位到桶，发生哈希冲突时以链表挂载，链表长度超过阈值（默认 8）且数组容量达标后转为红黑树。"
                    + "默认负载因子 0.75，元素数量超过容量乘负载因子时触发扩容，容量翻倍并重新散列。"
                    + "HashMap 不是线程安全的，多线程并发扩容可能导致死循环或数据丢失，并发场景应使用 ConcurrentHashMap。",
            "Redis 缓存穿透指查询不存在的数据，请求绕过缓存直击数据库。解决方案之一是布隆过滤器，"
                    + "在访问缓存前拦截必然不存在的 key；其二是缓存空值并设置较短过期时间。"
                    + "缓存击穿指某个热点 key 失效瞬间大量请求打到数据库，可用互斥锁或逻辑过期重建。"
                    + "缓存雪崩指大量 key 同时失效，应通过错峰过期时间、随机 TTL 与集群高可用来规避。",
            "Spring Bean 的生命周期包含实例化、属性填充、感知接口回调、初始化与销毁。"
                    + "初始化阶段会依次执行 Aware 接口、BeanPostProcessor 的前置处理、InitializingBean 的 afterPropertiesSet、"
                    + "自定义的 init-method，再经过后置处理。容器关闭时调用 DisposableBean 的 destroy 与自定义 destroy-method。",
            "MySQL 索引底层多为 B+ 树，叶子节点有序且双向链接，适合范围查询。"
                    + "联合索引遵循最左前缀原则，跳过最左列会导致索引失效。索引下推能把过滤条件下推到存储引擎层减少回表。"
                    + "慢查询可通过慢查询日志、EXPLAIN 分析执行计划来定位，常见优化手段包括建立合适索引、"
                    + "避免 SELECT *、减少隐式类型转换与函数包裹列。",
            "TCP 建立连接需要三次握手：客户端发送 SYN，服务端回 SYN-ACK，客户端再发 ACK 确认。"
                    + "三次而非两次是为了双方都能确认彼此的收发能力。释放连接需要四次挥手，因为 TCP 全双工，"
                    + "服务端收到 FIN 后先回 ACK，等待自身数据发送完毕再发 FIN，客户端回 ACK 后进入 TIME-WAIT。",
            "HTTPS 在 HTTP 之下加入 TLS 层保证安全。握手阶段通过非对称加密协商出对称密钥，"
                    + "之后用对称加密传输应用数据以提升性能。服务器证书由 CA 签发，客户端用它验证服务端身份并交换密钥。"
                    + "这样既保证了机密性，也通过证书链防止中间人篡改。"
    };

    /** 黄金查询：query 应命中 CORPUS 中对应下标的文档。 */
    private record Golden(String query, int expectedDoc) {}
    private static final Golden[] GOLDEN = {
            new Golden("HashMap 为什么线程不安全？扩容时会发生什么？", 0),
            new Golden("缓存穿透怎么解决？布隆过滤器有什么用？", 1),
            new Golden("Spring 的 Bean 初始化回调有哪些？", 2),
            new Golden("慢查询怎么排查？联合索引为什么失效？", 3),
            new Golden("TCP 为什么是三次握手而不是两次？", 4),
            new Golden("HTTPS 怎么保证传输安全？证书有什么用？", 5)
    };

    @Test
    void recallAtKShouldBeHigh() {
        // 1) 分块并保留 块 -> 文档 的映射
        List<String> chunks = new ArrayList<>();
        List<Integer> chunkDoc = new ArrayList<>();
        for (int d = 0; d < CORPUS.length; d++) {
            for (String c : chunker.chunk(CORPUS[d], CHUNK_SIZE, CHUNK_OVERLAP)) {
                chunks.add(c);
                chunkDoc.add(d);
            }
        }
        log.info("语料分块数 = {}", chunks.size());

        // 2) 批量向量化（单次 API 请求，降本）
        List<float[]> vectors = embeddingService.embedBatch(chunks);

        int hits = 0;
        for (Golden g : GOLDEN) {
            String query = g.query();
            int expectedDoc = g.expectedDoc();
            float[] qv = embeddingService.embed(query);

            // 3) 余弦相似度排序，取 Top-K 块
            List<Integer> ranked = new ArrayList<>();
            for (int i = 0; i < chunks.size(); i++) {
                ranked.add(i);
            }
            ranked.sort(Comparator.comparingDouble((Integer i) -> cosine(qv, vectors.get(i))).reversed());

            // 4) 收集 Top-K 命中了哪些文档
            boolean hit = ranked.subList(0, Math.min(TOP_K, ranked.size())).stream()
                    .map(chunkDoc::get)
                    .anyMatch(d -> d == expectedDoc);

            double best = cosine(qv, vectors.get(ranked.get(0)));
            log.info("query='{}' -> 期望文档#{} | 命中={} | 最高相似度={}",
                    query, expectedDoc, hit, String.format("%.3f", best));
            if (hit) {
                hits++;
            }
        }

        double recall = hits / (double) GOLDEN.length;
        log.info("recall@{K} = {}/{} = {}", TOP_K, hits, GOLDEN.length, String.format("%.2f", recall));
        // 保守阈值：黄金集精心构造，理想为 1.0；低于 0.8 说明分块/向量策略需调优
        assertTrue(recall >= 0.8,
                String.format("RAG 检索 recall@%d=%.2f 过低，请检查分块策略或 embedding 模型", TOP_K, recall));
    }

    private static double cosine(float[] a, float[] b) {
        double dot = 0, na = 0, nb = 0;
        for (int i = 0; i < a.length; i++) {
            dot += a[i] * b[i];
            na += a[i] * a[i];
            nb += b[i] * b[i];
        }
        if (na == 0 || nb == 0) {
            return 0;
        }
        return dot / (Math.sqrt(na) * Math.sqrt(nb));
    }
}
