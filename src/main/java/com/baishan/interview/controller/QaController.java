package com.baishan.interview.controller;

import com.baishan.interview.service.QaAgentService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

/** 智能问答 Agent（带知识库 / 简历工具），流式输出。 */
@RestController
@RequestMapping("/api/qa")
public class QaController {

    private final QaAgentService qaAgentService;

    public QaController(QaAgentService qaAgentService) {
        this.qaAgentService = qaAgentService;
    }

    public record QaRequest(String message, String resumeId) {
    }

    @PostMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> stream(@RequestBody QaRequest request) {
        if (request.message() == null || request.message().isBlank()) {
            return Flux.just("请输入问题");
        }
        return qaAgentService.stream(request.message(), request.resumeId());
    }
}
