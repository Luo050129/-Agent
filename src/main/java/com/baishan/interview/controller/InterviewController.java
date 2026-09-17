package com.baishan.interview.controller;

import com.baishan.interview.dto.AnswerRequest;
import com.baishan.interview.dto.CreateSessionRequest;
import com.baishan.interview.dto.EvaluationResult;
import com.baishan.interview.dto.InterviewSummary;
import com.baishan.interview.dto.SessionView;
import com.baishan.interview.service.InterviewService;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** 模拟面试：会话 / 流式出题 / 回答评估 / 总结。 */
@RestController
@RequestMapping("/api/interviews")
public class InterviewController {

    private final InterviewService interviewService;

    public InterviewController(InterviewService interviewService) {
        this.interviewService = interviewService;
    }

    /** 创建面试会话（含开场白）。 */
    @PostMapping
    public SessionView create(@Valid @RequestBody CreateSessionRequest request) {
        return interviewService.create(request);
    }

    @GetMapping("/{id}")
    public SessionView get(@PathVariable UUID id) {
        return interviewService.get(id);
    }

    @GetMapping(params = "resumeId")
    public List<SessionView> listByResume(@RequestParam UUID resumeId) {
        return interviewService.listByResume(resumeId);
    }

    /**
     * 流式提出下一个问题（SSE）。
     * 前端通过 fetch 读取 text/event-stream，逐段展示"打字机"效果。
     */
    @PostMapping(value = "/{id}/ask", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> ask(@PathVariable UUID id) {
        return interviewService.askNext(id);
    }

    /** 提交回答，返回结构化评估（含下一题方向建议）。 */
    @PostMapping("/{id}/answer")
    public EvaluationResult answer(@PathVariable UUID id, @Valid @RequestBody AnswerRequest request) {
        return interviewService.evaluateAnswer(id, request.content());
    }

    /** 结束面试并生成总结。 */
    @PostMapping("/{id}/finish")
    public InterviewSummary finish(@PathVariable UUID id) {
        return interviewService.finish(id);
    }

    @GetMapping("/{id}/report-info")
    public Map<String, Object> reportInfo(@PathVariable UUID id) {
        return Map.of("sessionId", id, "reportUrl", "/api/reports/interview/" + id + "/pdf");
    }
}
