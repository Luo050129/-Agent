package com.baishan.interview.service;

import com.baishan.interview.config.AppProperties;
import com.baishan.interview.domain.InterviewMessage;
import com.baishan.interview.domain.InterviewMode;
import com.baishan.interview.domain.InterviewSession;
import com.baishan.interview.domain.MessageKind;
import com.baishan.interview.domain.Resume;
import com.baishan.interview.domain.SessionStatus;
import com.baishan.interview.dto.CreateSessionRequest;
import com.baishan.interview.dto.EvaluationResult;
import com.baishan.interview.dto.InterviewSummary;
import com.baishan.interview.dto.MessageView;
import com.baishan.interview.dto.ResumeAnalysis;
import com.baishan.interview.dto.SessionView;
import com.baishan.interview.exception.AiCallException;
import com.baishan.interview.exception.NotFoundException;
import com.baishan.interview.repository.InterviewMessageRepository;
import com.baishan.interview.repository.InterviewSessionRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import reactor.core.publisher.Flux;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 模拟面试 Agent：
 * 面试官角色（系统提示词）由「简历分析 + 岗位 JD + 知识库上下文」动态组装；
 * 出题走流式 SSE，回答评估与总结走结构化输出。
 */
@Service
public class InterviewService {

    private static final Logger log = LoggerFactory.getLogger(InterviewService.class);

    private final InterviewSessionRepository sessionRepository;
    private final InterviewMessageRepository messageRepository;
    private final ResumeService resumeService;
    private final KnowledgeService knowledgeService;
    private final ChatClient chatClient;
    private final JsonService jsonService;
    private final AppProperties appProperties;

    public InterviewService(InterviewSessionRepository sessionRepository,
                            InterviewMessageRepository messageRepository,
                            ResumeService resumeService,
                            KnowledgeService knowledgeService,
                            ChatClient.Builder chatClientBuilder,
                            JsonService jsonService,
                            AppProperties appProperties) {
        this.sessionRepository = sessionRepository;
        this.messageRepository = messageRepository;
        this.resumeService = resumeService;
        this.knowledgeService = knowledgeService;
        this.chatClient = chatClientBuilder.build();
        this.jsonService = jsonService;
        this.appProperties = appProperties;
    }

    // ---------------------------------------------------------------- 会话管理

    /** 创建面试会话：校验简历已分析，组装面试官上下文（简历+JD+知识库）。 */
    @Transactional
    public SessionView create(CreateSessionRequest request) {
        ResumeAnalysis analysis = resumeService.getAnalysis(request.resumeId());
        String mode = request.mode() == null || request.mode().isBlank()
                ? InterviewMode.MIXED.name()
                : InterviewMode.of(request.mode()).name();

        String kbContext = knowledgeService.formatForPrompt(
                (analysis.targetPosition() == null ? "" : analysis.targetPosition())
                        + " " + (request.jdText() == null ? "" : request.jdText()),
                appProperties.getRag().getTopK());

        InterviewSession session = new InterviewSession(
                UUID.randomUUID(), request.resumeId(), mode, request.jdText(), SessionStatus.ACTIVE.name());
        session.setCreatedAt(OffsetDateTime.now());
        Map<String, Object> context = Map.of(
                "resumeAnalysis", analysis.toView(),
                "jd", request.jdText() == null ? "" : request.jdText(),
                "kbContext", kbContext
        );
        session.setContextJson(jsonService.toJson(context));
        sessionRepository.save(session);

        // 开场白（系统消息）
        InterviewMessage greeting = new InterviewMessage();
        greeting.setSessionId(session.getId());
        greeting.setRole("ASSISTANT");
        greeting.setKind(MessageKind.SYSTEM.name());
        greeting.setContent("你好，我是本次" + modeLabel(mode) + "的面试官。面试共 " + appProperties.getInterview().getQuestionCount()
                + " 个问题，我会结合你的简历与目标岗位提问。准备好了我们就开始。");
        greeting.setCreatedAt(OffsetDateTime.now());
        messageRepository.save(greeting);

        log.info("创建面试会话 {}：模式 {}，简历 {}", session.getId(), mode, request.resumeId());
        return toView(session);
    }

    public SessionView get(UUID sessionId) {
        return toView(getEntity(sessionId));
    }

    public List<SessionView> listByResume(UUID resumeId) {
        return sessionRepository.findByResumeIdOrderByCreatedAtDesc(resumeId).stream()
                .map(this::toView).toList();
    }

    private InterviewSession getEntity(UUID sessionId) {
        return sessionRepository.findById(sessionId)
                .orElseThrow(() -> new NotFoundException("面试会话不存在: " + sessionId));
    }

    // ---------------------------------------------------------------- 出题（流式）

    /**
     * 流式提出下一个问题（SSE）。
     * 流结束后持久化问题消息并推进会话计数。
     */
    public Flux<String> askNext(UUID sessionId) {
        InterviewSession session = getEntity(sessionId);
        if (!SessionStatus.ACTIVE.name().equals(session.getStatus())) {
            throw new IllegalStateException("该面试已结束");
        }
        int asked = countQuestions(sessionId);
        if (asked >= appProperties.getInterview().getQuestionCount()) {
            throw new IllegalStateException("面试问题已全部提出（共 " + asked + " 题），请结束面试生成总结");
        }
        int nextIndex = asked + 1;
        String system = buildSystemPrompt(session);
        String user = buildAskPrompt(session, nextIndex);

        AtomicReference<StringBuilder> acc = new AtomicReference<>(new StringBuilder());
        return chatClient.prompt()
                .system(system)
                .user(user)
                .stream()
                .content()
                .doOnNext(chunk -> acc.get().append(chunk))
                .doOnComplete(() -> {
                    String question = acc.get().toString().trim();
                    if (!question.isEmpty()) {
                        persistQuestion(sessionId, question);
                    }
                });
    }

    private void persistQuestion(UUID sessionId, String question) {
        InterviewMessage msg = new InterviewMessage();
        msg.setSessionId(sessionId);
        msg.setRole("ASSISTANT");
        msg.setKind(MessageKind.QUESTION.name());
        msg.setContent(question);
        msg.setCreatedAt(OffsetDateTime.now());
        messageRepository.save(msg);

        sessionRepository.findById(sessionId).ifPresent(s -> {
            s.setQuestionCount(s.getQuestionCount() + 1);
            sessionRepository.save(s);
        });
    }

    // ---------------------------------------------------------------- 回答评估（结构化）

    /** 评估候选人回答：持久化回答 + 评估，返回结构化评估结果。 */
    @Transactional
    public EvaluationResult evaluateAnswer(UUID sessionId, String content) {
        InterviewSession session = getEntity(sessionId);
        if (!SessionStatus.ACTIVE.name().equals(session.getStatus())) {
            throw new IllegalStateException("该面试已结束，无法继续作答");
        }
        String answer = content.trim();
        if (answer.isEmpty()) {
            throw new IllegalArgumentException("回答内容不能为空");
        }
        if (answer.length() > appProperties.getInterview().getMaxAnswerLength()) {
            throw new IllegalArgumentException("回答过长（超过 " + appProperties.getInterview().getMaxAnswerLength() + " 字符）");
        }

        List<InterviewMessage> qa = messageRepository.findBySessionIdOrderByIdAsc(sessionId);
        String lastQuestion = qa.stream()
                .filter(m -> MessageKind.QUESTION.name().equals(m.getKind()))
                .reduce((a, b) -> b)
                .map(InterviewMessage::getContent)
                .orElse("（无前置问题）");

        EvaluationResult result;
        try {
            result = chatClient.prompt()
                    .system(EVALUATION_SYSTEM_PROMPT)
                    .user(buildEvaluationUserPrompt(session, lastQuestion, answer))
                    .call()
                    .entity(EvaluationResult.class);
        } catch (Exception e) {
            log.error("回答评估失败", e);
            throw new AiCallException("回答评估调用失败: " + e.getMessage(), e);
        }
        if (result == null) {
            throw new AiCallException("模型未返回有效的评估结果");
        }

        InterviewMessage answerMsg = new InterviewMessage();
        answerMsg.setSessionId(sessionId);
        answerMsg.setRole("USER");
        answerMsg.setKind(MessageKind.ANSWER.name());
        answerMsg.setContent(answer);
        answerMsg.setCreatedAt(OffsetDateTime.now());
        messageRepository.save(answerMsg);

        InterviewMessage evalMsg = new InterviewMessage();
        evalMsg.setSessionId(sessionId);
        evalMsg.setRole("ASSISTANT");
        evalMsg.setKind(MessageKind.EVALUATION.name());
        evalMsg.setContent("第 " + countQuestions(sessionId) + " 题评估");
        evalMsg.setEvaluationJson(jsonService.toJson(result));
        evalMsg.setScore(result.score());
        evalMsg.setCreatedAt(OffsetDateTime.now());
        messageRepository.save(evalMsg);

        log.info("会话 {} 第 {} 题评估得分 {}", sessionId, countQuestions(sessionId), result.score());
        return result;
    }

    // ---------------------------------------------------------------- 面试总结（结构化）

    /** 结束面试并生成整场总结。 */
    @Transactional
    public InterviewSummary finish(UUID sessionId) {
        InterviewSession session = getEntity(sessionId);
        List<InterviewMessage> messages = messageRepository.findBySessionIdOrderByIdAsc(sessionId);
        String transcript = buildTranscript(messages);

        InterviewSummary summary;
        try {
            summary = chatClient.prompt()
                    .system(SUMMARY_SYSTEM_PROMPT)
                    .user("候选人简历分析：\n" + briefAnalysis(session)
                            + "\n\n目标岗位：\n" + (session.getJdText() == null ? "未提供" : session.getJdText())
                            + "\n\n面试问答记录：\n" + transcript)
                    .call()
                    .entity(InterviewSummary.class);
        } catch (Exception e) {
            log.error("面试总结失败", e);
            throw new AiCallException("面试总结调用失败: " + e.getMessage(), e);
        }
        if (summary == null) {
            throw new AiCallException("模型未返回有效的总结结果");
        }

        session.setSummaryJson(jsonService.toJson(summary));
        session.setStatus(SessionStatus.FINISHED.name());
        session.setEndedAt(OffsetDateTime.now());
        sessionRepository.save(session);
        log.info("面试 {} 结束，综合得分 {}", sessionId, summary.overallScore());
        return summary;
    }

    // ---------------------------------------------------------------- 视图

    private SessionView toView(InterviewSession session) {
        List<InterviewMessage> msgs = messageRepository.findBySessionIdOrderByIdAsc(session.getId());
        List<MessageView> views = new ArrayList<>();
        for (InterviewMessage m : msgs) {
            EvaluationResult eval = null;
            if (m.getEvaluationJson() != null) {
                eval = jsonService.fromJson(m.getEvaluationJson(), EvaluationResult.class);
            }
            views.add(new MessageView(m.getId(), m.getRole(), m.getKind(), m.getContent(), eval,
                    m.getScore(), m.getCreatedAt()));
        }
        InterviewSummary summary = session.getSummaryJson() == null
                ? null
                : jsonService.fromJson(session.getSummaryJson(), InterviewSummary.class);
        return new SessionView(session.getId(), session.getResumeId(), session.getMode(), session.getStatus(),
                session.getQuestionCount(), session.getCreatedAt(), session.getEndedAt(), views, summary);
    }

    private int countQuestions(UUID sessionId) {
        return messageRepository.findBySessionIdOrderByIdAsc(sessionId).stream()
                .filter(m -> MessageKind.QUESTION.name().equals(m.getKind()))
                .toList().size();
    }

    // ---------------------------------------------------------------- 提示词组装

    private String buildSystemPrompt(InterviewSession session) {
        String context = session.getContextJson();
        String resumeBrief = "（无简历上下文）";
        String jd = session.getJdText() == null ? "未提供" : session.getJdText();
        String kb = "（无知识库资料）";
        if (context != null) {
            var ctx = jsonService.fromJson(context, java.util.Map.class);
            if (ctx != null) {
                Object ra = ctx.get("resumeAnalysis");
                if (ra != null) {
                    resumeBrief = jsonService.toJson(ra);
                }
                Object k = ctx.get("kbContext");
                if (k != null && !String.valueOf(k).isBlank() && !"(知识库无相关内容)".equals(String.valueOf(k))) {
                    kb = String.valueOf(k);
                }
            }
        }
        return """
                你是「白山面试官」，一位经验丰富、专业且温和的招聘面试官，正在进行%s面试。

                【候选人简历分析】
                %s

                【目标岗位描述（JD）】
                %s

                【企业/岗位背景资料（来自知识库，可能有也可能没有，请勿编造）】
                %s

                【面试规则】
                1. 一次只提出 1 个问题；问题要具体、有针对性，紧扣候选人简历中的真实经历与目标岗位要求，避免空泛。
                2. 问题长度控制在 60-150 字，口语化，像真人面试官。
                3. 严格基于简历信息提问，不得虚构简历中没有的经历；涉及简历未覆盖的领域时以岗位要求为锚点提问。
                4. 根据上一题的回答质量动态调整：回答扎实则适度加深追问，回答含糊则引导补充细节。
                5. 全程使用简体中文。
                """.formatted(modeLabel(session.getMode()), resumeBrief, jd, kb);
    }

    private String buildAskPrompt(InterviewSession session, int nextIndex) {
        List<InterviewMessage> qa = messageRepository.findBySessionIdOrderByIdAsc(session.getId());
        StringBuilder askedList = new StringBuilder();
        List<String> questions = qa.stream()
                .filter(m -> MessageKind.QUESTION.name().equals(m.getKind()))
                .map(InterviewMessage::getContent)
                .toList();
        for (int i = 0; i < questions.size(); i++) {
            askedList.append(i + 1).append(". ").append(questions.get(i)).append('\n');
        }

        StringBuilder feedback = new StringBuilder();
        // 上题回答与评估
        List<InterviewMessage> answers = qa.stream()
                .filter(m -> MessageKind.ANSWER.name().equals(m.getKind()))
                .toList();
        List<InterviewMessage> evals = qa.stream()
                .filter(m -> MessageKind.EVALUATION.name().equals(m.getKind()))
                .toList();
        if (!answers.isEmpty()) {
            InterviewMessage lastAnswer = answers.get(answers.size() - 1);
            feedback.append("上一题回答：").append(truncate(lastAnswer.getContent(), 800)).append('\n');
            if (!evals.isEmpty()) {
                EvaluationResult e = jsonService.fromJson(evals.get(evals.size() - 1).getEvaluationJson(), EvaluationResult.class);
                if (e != null) {
                    feedback.append("上题评分：").append(e.score()).append("/10\n")
                            .append("上题点评：").append(e.feedback()).append('\n')
                            .append("后续出题方向建议：").append(e.nextFocus() == null ? "无" : e.nextFocus()).append('\n');
                }
            }
        }

        int total = appProperties.getInterview().getQuestionCount();
        return """
                请提出第 %d 个面试问题（共 %d 题）。

                %s
                %s

                【已问过的问题（不得重复）】
                %s

                请只输出问题本身，不要输出其他内容。
                """.formatted(nextIndex, total, feedback.isEmpty() ? "" : feedback, 
                modeRule(session.getMode()), askedList.length() == 0 ? "（暂无）" : askedList);
    }

    private String buildEvaluationUserPrompt(InterviewSession session, String question, String answer) {
        return """
                目标岗位：%s

                面试问题：%s

                候选人回答：%s
                """.formatted(session.getJdText() == null ? "未提供" : session.getJdText(),
                truncate(question, 1000), truncate(answer, 3000));
    }

    private String buildTranscript(List<InterviewMessage> messages) {
        StringBuilder sb = new StringBuilder();
        for (InterviewMessage m : messages) {
            String tag = switch (m.getKind()) {
                case "QUESTION" -> "面试官";
                case "ANSWER" -> "候选人";
                case "EVALUATION" -> "评估";
                default -> "系统";
            };
            sb.append("【").append(tag).append("】").append(m.getContent()).append('\n');
            if (m.getEvaluationJson() != null) {
                EvaluationResult e = jsonService.fromJson(m.getEvaluationJson(), EvaluationResult.class);
                if (e != null) {
                    sb.append("（得分 ").append(e.score()).append("/10：").append(e.feedback()).append("）\n");
                }
            }
            sb.append('\n');
        }
        return sb.toString();
    }

    private String briefAnalysis(InterviewSession session) {
        if (session.getContextJson() == null) {
            return "（无）";
        }
        var ctx = jsonService.fromJson(session.getContextJson(), java.util.Map.class);
        return ctx != null && ctx.get("resumeAnalysis") != null
                ? jsonService.toJson(ctx.get("resumeAnalysis"))
                : "（无）";
    }

    private String modeLabel(String mode) {
        return switch (mode) {
            case "TECHNICAL" -> "技术面";
            case "BEHAVIORAL" -> "行为面";
            default -> "综合面";
        };
    }

    private String modeRule(String mode) {
        return switch (mode) {
            case "TECHNICAL" -> "【模式：技术面】重点考察专业技能深度、项目实现细节、技术方案权衡；可追问代码思路、难点与选型理由。";
            case "BEHAVIORAL" -> "【模式：行为面】重点考察职业动机、团队协作、抗压能力、成长复盘（STAR 法则）。";
            default -> "【模式：综合面】技术考察与行为考察交替进行。";
        };
    }

    private String truncate(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() <= max ? s : s.substring(0, max) + "……";
    }

    private static final String EVALUATION_SYSTEM_PROMPT = """
            你是一位严格的招聘面试官，正在评估候选人对面试问题的回答质量。

            【评估要求】
            - score：0-10 整数，衡量回答的整体质量（内容相关性、深度、结构化、表达）。
            - level：优秀(8-10) / 良好(6-7) / 一般(4-5) / 待提升(0-3) 四档。
            - feedback：80-150 字总体点评，具体指出亮点与不足。
            - strengths：回答中的亮点，2-3 条。
            - improvements：回答中可改进之处，2-3 条，给出可操作的改进方向。
            - nextFocus：给面试官的下一条出题方向建议（例如"追问项目中的技术难点"、"考察候选人抗压能力"），一句话。
            请客观评分，回答质量差就给低分，不要无原则宽容。
            """;

    private static final String SUMMARY_SYSTEM_PROMPT = """
            你是一位资深招聘专家，正在对一场模拟面试进行总结评估。

            【评估要求】
            - overallScore：0-100 整数，综合面试表现。
            - level：推荐录用 / 建议进入下一轮 / 待定 / 不建议录用 四档。
            - overallEvaluation：150-250 字整体评价。
            - strengths：候选人在面试中表现出的优势，3-4 条。
            - weaknesses：暴露的不足，2-3 条。
            - developmentSuggestions：针对不足给出的提升建议，3-4 条，具体可执行。
            - hiringAdvice：给候选人的求职综合建议（岗位匹配度、面试技巧），2-3 句话。
            评估要客观、具体、有说服力。
            """;
}
