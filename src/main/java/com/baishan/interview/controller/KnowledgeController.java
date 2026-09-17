package com.baishan.interview.controller;

import com.baishan.interview.dto.KnowledgeDocView;
import com.baishan.interview.dto.KnowledgeHit;
import com.baishan.interview.service.KnowledgeService;
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

/** 知识库：文档上传 / 管理 / 向量检索。 */
@RestController
@RequestMapping("/api/knowledge")
public class KnowledgeController {

    private final KnowledgeService knowledgeService;

    public KnowledgeController(KnowledgeService knowledgeService) {
        this.knowledgeService = knowledgeService;
    }

    @PostMapping(value = "/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public KnowledgeDocView upload(@RequestParam("file") MultipartFile file,
                                   @RequestParam(value = "docType", defaultValue = "KNOWLEDGE") String docType) {
        return knowledgeService.upload(file, docType);
    }

    @GetMapping("/documents")
    public List<KnowledgeDocView> list() {
        return knowledgeService.list();
    }

    @DeleteMapping("/documents/{id}")
    public Map<String, String> delete(@PathVariable UUID id) {
        knowledgeService.delete(id);
        return Map.of("message", "已删除");
    }

    @GetMapping("/search")
    public List<KnowledgeHit> search(@RequestParam String query,
                                     @RequestParam(defaultValue = "4") int topK) {
        return knowledgeService.search(query, topK);
    }
}
