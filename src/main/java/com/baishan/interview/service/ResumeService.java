package com.baishan.interview.service;

import com.baishan.interview.config.AppProperties;
import com.baishan.interview.domain.Resume;
import com.baishan.interview.dto.ResumeAnalysis;
import com.baishan.interview.dto.ResumeView;
import com.baishan.interview.exception.AiCallException;
import com.baishan.interview.exception.NotFoundException;
import com.baishan.interview.repository.ResumeRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 简历服务：上传解析 + LLM 结构化评估。
 * 分析结果写入 DB 并缓存于 Redis，重复获取不再调用模型。
 */
@Service
public class ResumeService {

    private static final Logger log = LoggerFactory.getLogger(ResumeService.class);

    private static final String ANALYSIS_SYSTEM_PROMPT = """
            你是一位资深的人力资源（HR）与职业发展专家，擅长从技术能力、项目经验、简历呈现三个角度评估求职简历。
            请严格依据提供的简历原文进行分析，不得编造简历中不存在的信息。

            【分析要求】
            1. candidateName：候选人的姓名；无法确定时填空字符串。
            2. targetPosition：根据简历内容推测其求职目标岗位。
            3. summary：用 80-120 字概括候选人的整体画像与职业竞争力。
            4. skills：提取简历中明确提到的技能（技术栈/工具/软技能），最多 12 项。
            5. strengths：候选人的核心优势，3-5 条，具体、可验证。
            6. weaknesses：简历中暴露的短板（如项目描述缺乏量化结果、技术栈单一、经历时间断层等），2-4 条。
            7. suggestions：针对简历的改进建议，3-5 条，条条可落地。
            8. overallScore：综合评分，0-100 整数。
            9. dimensions：从以下固定维度评估，每项 score 为 0-100 整数：
               - 简历完整度：信息是否完整（教育/经历/技能/联系方式）
               - 技能匹配度：技能与目标岗位的匹配程度
               - 项目深度：项目描述的技术深度与业务价值
               - 表达与量化：是否使用数据与成果量化表达
               每个维度附带一句 30 字以内的 comment。
            """;

    private final ResumeRepository resumeRepository;
    private final DocumentParsingService parsingService;
    private final ChatClient chatClient;
    private final CacheService cacheService;
    private final JsonService jsonService;
    private final AppProperties appProperties;

    public ResumeService(ResumeRepository resumeRepository,
                         DocumentParsingService parsingService,
                         ChatClient.Builder chatClientBuilder,
                         CacheService cacheService,
                         JsonService jsonService,
                         AppProperties appProperties) {
        this.resumeRepository = resumeRepository;
        this.parsingService = parsingService;
        this.chatClient = chatClientBuilder.build();
        this.cacheService = cacheService;
        this.jsonService = jsonService;
        this.appProperties = appProperties;
    }

    @Transactional
    public ResumeView upload(MultipartFile file) {
        DocumentParsingService.ParsedText parsed = parsingService.parse(file, appProperties.getUpload().getMaxFileSize());
        Resume resume = new Resume(UUID.randomUUID(), file.getOriginalFilename(),
                parsed.mimeType(), file.getSize(), parsed.text());
        OffsetDateTime now = OffsetDateTime.now();
        resume.setCreatedAt(now);
        resume.setUpdatedAt(now);
        resumeRepository.save(resume);
        log.info("简历上传 {}：{} 字符", file.getOriginalFilename(), parsed.text().length());
        return ResumeView.from(resume);
    }

    public Resume getEntity(UUID id) {
        return resumeRepository.findById(id)
                .orElseThrow(() -> new NotFoundException("简历不存在: " + id));
    }

    public ResumeView get(UUID id) {
        return ResumeView.from(getEntity(id));
    }

    public List<ResumeView> list() {
        return resumeRepository.findAll().stream()
                .sorted((a, b) -> b.getCreatedAt().compareTo(a.getCreatedAt()))
                .map(ResumeView::from)
                .toList();
    }

    public void delete(UUID id) {
        if (!resumeRepository.existsById(id)) {
            throw new NotFoundException("简历不存在: " + id);
        }
        resumeRepository.deleteById(id);
        cacheService.evict("resume-analysis:" + id);
    }

    /** 触发 LLM 分析（幂等：已分析则直接返回缓存结果）。 */
    @Transactional
    public ResumeAnalysis analyze(UUID id) {
        Resume resume = getEntity(id);
        if (resume.getRawText() == null || resume.getRawText().isBlank()) {
            throw new IllegalArgumentException("简历文本为空，无法分析");
        }
        ResumeAnalysis cached = getCachedAnalysis(id);
        if (cached != null) {
            return cached;
        }
        try {
            ResumeAnalysis analysis = chatClient.prompt()
                    .system(ANALYSIS_SYSTEM_PROMPT)
                    .user("以下是候选人简历原文：\n\n" + resume.getRawText())
                    .call()
                    .entity(ResumeAnalysis.class);
            if (analysis == null) {
                throw new AiCallException("模型未返回有效的简历分析结果");
            }
            resume.setAnalysisJson(jsonService.toJson(analysis));
            resume.setOverallScore(analysis.overallScore());
            resume.setUpdatedAt(OffsetDateTime.now());
            resumeRepository.save(resume);
            cacheService.put("resume-analysis:" + id, jsonService.toJson(analysis), Duration.ofHours(6));
            return analysis;
        } catch (AiCallException e) {
            throw e;
        } catch (Exception e) {
            log.error("简历分析失败", e);
            throw new AiCallException("AI 分析调用失败: " + e.getMessage(), e);
        }
    }

    public ResumeAnalysis getAnalysis(UUID id) {
        Resume resume = getEntity(id);
        ResumeAnalysis cached = getCachedAnalysis(id);
        if (cached != null) {
            return cached;
        }
        if (resume.getAnalysisJson() != null) {
            ResumeAnalysis fromDb = jsonService.fromJson(resume.getAnalysisJson(), ResumeAnalysis.class);
            if (fromDb != null) {
                cacheService.put("resume-analysis:" + id, jsonService.toJson(fromDb), Duration.ofHours(6));
                return fromDb;
            }
        }
        throw new NotFoundException("该简历尚未分析，请先调用分析接口: " + id);
    }

    private ResumeAnalysis getCachedAnalysis(UUID id) {
        String cached = cacheService.get("resume-analysis:" + id);
        return cached == null ? null : jsonService.fromJson(cached, ResumeAnalysis.class);
    }
}
