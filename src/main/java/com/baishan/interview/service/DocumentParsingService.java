package com.baishan.interview.service;

import com.baishan.interview.exception.NotFoundException;
import org.apache.tika.exception.TikaException;
import org.apache.tika.metadata.Metadata;
import org.apache.tika.parser.AutoDetectParser;
import org.apache.tika.parser.ParseContext;
import org.apache.tika.sax.BodyContentHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.xml.sax.SAXException;

import java.io.IOException;
import java.io.InputStream;
import java.util.Set;

/**
 * 文档解析：基于 Apache Tika 自动检测类型并抽取纯文本。
 * 支持 pdf / docx / doc / txt / md 等常见简历与知识库格式。
 */
@Service
public class DocumentParsingService {

    private static final Logger log = LoggerFactory.getLogger(DocumentParsingService.class);

    private static final Set<String> SUPPORTED_EXT = Set.of("pdf", "doc", "docx", "txt", "md", "markdown", "rtf");

    /** 解析结果：纯文本 + 检测到的 MIME 类型。 */
    public record ParsedText(String text, String mimeType) {
    }

    public ParsedText parse(MultipartFile file, long maxFileSize) {
        String name = file.getOriginalFilename();
        if (name == null || name.isBlank()) {
            throw new IllegalArgumentException("文件名不能为空");
        }
        String ext = extOf(name);
        if (!SUPPORTED_EXT.contains(ext)) {
            throw new IllegalArgumentException("不支持的文件类型: ." + ext + "（支持 pdf/doc/docx/txt/md/rtf）");
        }
        if (file.getSize() > maxFileSize) {
            throw new IllegalArgumentException("文件大小超出限制（最大 10MB）");
        }

        Metadata metadata = new Metadata();
        BodyContentHandler handler = new BodyContentHandler(-1);
        ParseContext context = new ParseContext();
        AutoDetectParser parser = new AutoDetectParser();

        try (InputStream in = file.getInputStream();
             org.apache.tika.io.TikaInputStream tis = org.apache.tika.io.TikaInputStream.get(in)) {
            parser.parse(tis, handler, metadata, context);
        } catch (IOException | TikaException | SAXException e) {
            log.warn("文档解析失败 {}: {}", name, e.getMessage());
            throw new IllegalArgumentException("文档解析失败，请确认为可解析的文本类文件: " + e.getMessage());
        }

        String text = handler.toString().trim();
        if (text.isEmpty()) {
            throw new IllegalArgumentException("未能从文件中提取到文本内容（可能是扫描件图片，暂不支持 OCR）");
        }
        // Tika 4 已移除 Metadata.CONTENT_TYPE 常量，MIME 类型仍写入 "Content-Type" 键
        return new ParsedText(text, metadata.get("Content-Type"));
    }

    private String extOf(String name) {
        int dot = name.lastIndexOf('.');
        return dot >= 0 ? name.substring(dot + 1).toLowerCase() : "";
    }
}
