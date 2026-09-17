package com.baishan.interview.controller;

import com.baishan.interview.dto.ResumeAnalysis;
import com.baishan.interview.dto.ResumeView;
import com.baishan.interview.service.ResumeService;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/** 简历：上传 / 分析 / 查询。 */
@RestController
@RequestMapping("/api/resumes")
public class ResumeController {

    private final ResumeService resumeService;

    public ResumeController(ResumeService resumeService) {
        this.resumeService = resumeService;
    }

    @PostMapping(value = "/upload", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResumeView upload(@RequestParam("file") MultipartFile file) {
        return resumeService.upload(file);
    }

    @GetMapping
    public List<ResumeView> list() {
        return resumeService.list();
    }

    @GetMapping("/{id}")
    public ResumeView get(@PathVariable UUID id) {
        return resumeService.get(id);
    }

    @DeleteMapping("/{id}")
    public Map<String, String> delete(@PathVariable UUID id) {
        resumeService.delete(id);
        return Map.of("message", "已删除");
    }

    /** 触发 LLM 分析（已分析则返回缓存结果）。 */
    @PostMapping("/{id}/analyze")
    public ResumeAnalysis analyze(@PathVariable UUID id) {
        return resumeService.analyze(id);
    }

    @GetMapping("/{id}/analysis")
    public ResumeAnalysis analysis(@PathVariable UUID id) {
        return resumeService.getAnalysis(id);
    }
}
