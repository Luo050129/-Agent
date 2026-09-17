package com.baishan.interview.service;

import com.baishan.interview.domain.InterviewMessage;
import com.baishan.interview.domain.InterviewSession;
import com.baishan.interview.dto.EvaluationResult;
import com.baishan.interview.dto.InterviewSummary;
import com.baishan.interview.dto.ResumeAnalysis;
import com.baishan.interview.exception.NotFoundException;
import com.baishan.interview.repository.InterviewMessageRepository;
import com.baishan.interview.repository.InterviewSessionRepository;
import com.itextpdf.kernel.events.PdfDocumentEvent;
import com.itextpdf.kernel.font.PdfFont;
import com.itextpdf.kernel.font.PdfFontFactory;
import com.itextpdf.kernel.geom.PageSize;
import com.itextpdf.kernel.pdf.PdfDocument;
import com.itextpdf.kernel.pdf.PdfWriter;
import com.itextpdf.layout.Document;
import com.itextpdf.layout.element.Cell;
import com.itextpdf.layout.element.Paragraph;
import com.itextpdf.layout.element.Table;
import com.itextpdf.layout.properties.TextAlignment;
import com.itextpdf.layout.properties.VerticalAlignment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.UUID;

/**
 * 面试评估报告（PDF，iText 8 + 中文字体 STSong-Light）。
 * 包含：简历分析 / 问答记录与逐题评估 / 综合总结。
 */
@Service
public class ReportService {

    private static final Logger log = LoggerFactory.getLogger(ReportService.class);

    private static final DateTimeFormatter DTF = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");

    private final InterviewSessionRepository sessionRepository;
    private final InterviewMessageRepository messageRepository;
    private final ResumeService resumeService;
    private final JsonService jsonService;

    public ReportService(InterviewSessionRepository sessionRepository,
                         InterviewMessageRepository messageRepository,
                         ResumeService resumeService,
                         JsonService jsonService) {
        this.sessionRepository = sessionRepository;
        this.messageRepository = messageRepository;
        this.resumeService = resumeService;
        this.jsonService = jsonService;
    }

    public byte[] generateInterviewReport(UUID sessionId) {
        InterviewSession session = sessionRepository.findById(sessionId)
                .orElseThrow(() -> new NotFoundException("面试会话不存在: " + sessionId));
        ResumeAnalysis analysis = resumeService.getAnalysis(session.getResumeId());
        List<InterviewMessage> messages = messageRepository.findBySessionIdOrderByIdAsc(sessionId);
        InterviewSummary summary = session.getSummaryJson() == null
                ? null
                : jsonService.fromJson(session.getSummaryJson(), InterviewSummary.class);

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try {
            PdfWriter writer = new PdfWriter(out);
            PdfDocument pdf = new PdfDocument(writer);
            Document doc = new Document(pdf, PageSize.A4);
            doc.setMargins(48, 48, 60, 48);

            PdfFont font = PdfFontFactory.createFont("STSong-Light", "UniGB-UCS2-H");
            PdfFont bold = PdfFontFactory.createFont("STSong-Light", "UniGB-UCS2-H");

            // ---------- 封面标题 ----------
            doc.add(new Paragraph("求职面试评估报告").setFont(bold).setFontSize(24)
                    .setTextAlignment(TextAlignment.CENTER).setMarginBottom(6));
            doc.add(new Paragraph("白山面试辅助 Agent 生成").setFont(font).setFontSize(10)
                    .setFontColor(new com.itextpdf.kernel.colors.DeviceRgb(120, 120, 120))
                    .setTextAlignment(TextAlignment.CENTER).setMarginBottom(24));

            // ---------- 基本信息 ----------
            String candidate = analysis.candidateName() == null || analysis.candidateName().isBlank()
                    ? "（未识别）" : analysis.candidateName();
            Table info = new Table(2).useAllAvailableWidth();
            addInfoRow(info, "候选人", candidate, font);
            addInfoRow(info, "求职目标", orDash(analysis.targetPosition()), font);
            addInfoRow(info, "面试模式", modeLabel(session.getMode()), font);
            addInfoRow(info, "面试时间", session.getCreatedAt() == null ? "-" : session.getCreatedAt().format(DTF), font);
            addInfoRow(info, "综合得分", (summary != null ? summary.overallScore() : analysis.overallScore()) + " / 100", font);
            doc.add(info);
            doc.add(space());

            // ---------- 一、简历分析 ----------
            sectionTitle(doc, "一、简历分析", bold);
            bulletBlock(doc, "候选人画像", analysis.summary(), font);
            bulletBlock(doc, "核心技能", joinList(analysis.skills()), font);
            bulletBlock(doc, "主要优势", joinList(analysis.strengths()), font);
            bulletBlock(doc, "存在的不足", joinList(analysis.weaknesses()), font);
            bulletBlock(doc, "简历改进建议", joinList(analysis.suggestions()), font);

            if (analysis.dimensions() != null && !analysis.dimensions().isEmpty()) {
                Table dim = new Table(3).useAllAvailableWidth();
                dim.addHeaderCell(headerCell("评估维度", font));
                dim.addHeaderCell(headerCell("得分", font));
                dim.addHeaderCell(headerCell("评语", font));
                for (ResumeAnalysis.DimensionScore d : analysis.dimensions()) {
                    dim.addCell(new Cell().add(new Paragraph(orDash(d.name())).setFont(font)));
                    dim.addCell(new Cell().add(new Paragraph(d.score() + " / 100").setFont(font)));
                    dim.addCell(new Cell().add(new Paragraph(orDash(d.comment())).setFont(font)));
                }
                doc.add(dim);
            }
            doc.add(space());

            // ---------- 二、面试问答记录 ----------
            sectionTitle(doc, "二、面试问答记录", bold);
            int qIndex = 0;
            for (InterviewMessage m : messages) {
                switch (m.getKind()) {
                    case "QUESTION" -> {
                        qIndex++;
                        doc.add(new Paragraph("第 " + qIndex + " 题 · 面试官").setFont(bold).setFontSize(11)
                                .setMarginTop(8).setMarginBottom(4));
                        doc.add(new Paragraph(orDash(m.getContent())).setFont(font).setMarginBottom(6));
                    }
                    case "ANSWER" -> {
                        doc.add(new Paragraph("候选人回答").setFont(bold).setFontSize(10)
                                .setFontColor(new com.itextpdf.kernel.colors.DeviceRgb(90, 90, 90))
                                .setMarginTop(4).setMarginBottom(3));
                        doc.add(new Paragraph(orDash(m.getContent())).setFont(font).setMarginBottom(6));
                    }
                    case "EVALUATION" -> {
                        EvaluationResult e = m.getEvaluationJson() == null
                                ? null : jsonService.fromJson(m.getEvaluationJson(), EvaluationResult.class);
                        if (e != null) {
                            doc.add(new Paragraph("评估 · 得分 " + e.score() + " / 10（" + orDash(e.level()) + "）")
                                    .setFont(bold).setFontSize(10).setMarginTop(4).setMarginBottom(3));
                            doc.add(new Paragraph(orDash(e.feedback())).setFont(font).setMarginBottom(3));
                            if (e.strengths() != null && !e.strengths().isEmpty()) {
                                doc.add(new Paragraph("亮点：" + joinList(e.strengths())).setFont(font).setMarginBottom(2));
                            }
                            if (e.improvements() != null && !e.improvements().isEmpty()) {
                                doc.add(new Paragraph("改进：" + joinList(e.improvements())).setFont(font).setMarginBottom(6));
                            }
                        }
                    }
                    default -> {
                    }
                }
            }
            doc.add(space());

            // ---------- 三、综合评估 ----------
            sectionTitle(doc, "三、综合评估", bold);
            if (summary != null) {
                doc.add(new Paragraph("面试结论：" + orDash(summary.level()))
                        .setFont(bold).setFontSize(11).setMarginBottom(6));
                bulletBlock(doc, "整体评价", summary.overallEvaluation(), font);
                bulletBlock(doc, "表现优势", joinList(summary.strengths()), font);
                bulletBlock(doc, "待提升项", joinList(summary.weaknesses()), font);
                bulletBlock(doc, "发展建议", joinList(summary.developmentSuggestions()), font);
                bulletBlock(doc, "求职综合建议", summary.hiringAdvice(), font);
            } else {
                doc.add(new Paragraph("该面试尚未生成总结，请先完成面试。").setFont(font));
            }

            // ---------- 页脚 ----------
            pdf.addEventHandler(PdfDocumentEvent.END_PAGE, event -> {
                PdfDocumentEvent docEvent = (PdfDocumentEvent) event;
                com.itextpdf.kernel.pdf.PdfPage page = docEvent.getPage();
                int pageNum = docEvent.getDocument().getPageNumber(page);
                try (com.itextpdf.layout.Canvas canvas = new com.itextpdf.layout.Canvas(page, page.getPageSize())) {
                    Paragraph footer = new Paragraph("第 " + pageNum + " 页 · 白山面试辅助 Agent")
                            .setFont(font).setFontSize(9)
                            .setFontColor(new com.itextpdf.kernel.colors.DeviceRgb(140, 140, 140));
                    canvas.showTextAligned(footer,
                            page.getPageSize().getWidth() / 2, 24,
                            TextAlignment.CENTER, VerticalAlignment.BOTTOM);
                }
            });

            int totalPages = pdf.getNumberOfPages();
            doc.close();
            log.info("面试报告生成成功：会话 {}，{} 页", sessionId, totalPages);
            return out.toByteArray();
        } catch (Exception e) {
            log.error("PDF 生成失败", e);
            throw new IllegalStateException("报告生成失败: " + e.getMessage(), e);
        }
    }

    // ---------------------------------------------------------------- 排版辅助

    private void sectionTitle(Document doc, String text, PdfFont font) {
        doc.add(new Paragraph(text).setFont(font).setFontSize(14).setBold()
                .setMarginTop(18).setMarginBottom(10));
    }

    private void bulletBlock(Document doc, String title, String body, PdfFont font) {
        if (body == null || body.isBlank() || "(暂无)".equals(body)) {
            return;
        }
        doc.add(new Paragraph(title).setFont(font).setBold().setFontSize(10)
                .setMarginTop(6).setMarginBottom(2));
        doc.add(new Paragraph(body).setFont(font).setFontSize(10).setMarginBottom(6));
    }

    private void addInfoRow(Table table, String label, String value, PdfFont font) {
        table.addCell(new Cell().add(new Paragraph(label).setFont(font)).setBold()
                .setBackgroundColor(new com.itextpdf.kernel.colors.DeviceRgb(243, 244, 246)));
        table.addCell(new Cell().add(new Paragraph(value).setFont(font)));
    }

    private Cell headerCell(String text, PdfFont font) {
        return new Cell().add(new Paragraph(text).setFont(font).setBold())
                .setBackgroundColor(new com.itextpdf.kernel.colors.DeviceRgb(230, 232, 236));
    }

    private Paragraph space() {
        return new Paragraph(" ").setFontSize(6).setMargin(0);
    }

    private String joinList(List<String> items) {
        if (items == null || items.isEmpty()) {
            return "（暂无）";
        }
        return String.join("；", items);
    }

    private String orDash(String s) {
        return s == null || s.isBlank() ? "-" : s;
    }

    private String modeLabel(String mode) {
        return switch (mode) {
            case "TECHNICAL" -> "技术面";
            case "BEHAVIORAL" -> "行为面";
            default -> "综合面";
        };
    }
}
