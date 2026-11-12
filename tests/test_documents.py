"""文档解析测试：用标准库现场造 docx / xlsx / pdf / png 样本再解析。

`python tests/test_documents.py` 直接跑，不需要网络和第三方包。
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miniagent.tools.documents import read_document  # noqa: E402

DOCX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>第一段内容</w:t></w:r></w:p>
    <w:p><w:r><w:t>第二段 </w:t></w:r><w:r><w:t>接着写</w:t></w:r></w:p>
  </w:body>
</w:document>
"""

SHARED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>姓名</t></si><si><t>小罗</t></si>
</sst>
"""

SHEET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2"><v>42</v></c><c r="B2" t="inlineStr"><is><t>行内</t></is></c></row>
  </sheetData>
</worksheet>
"""


def make_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", DOCX_XML)


def make_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", SHARED_XML)
        zf.writestr("xl/worksheets/sheet1.xml", SHEET_XML)


def make_pdf(path: Path, compress: bool = True) -> None:
    raw = b"BT /F1 12 Tf (Hello PDF World) Tj ET"
    if compress:
        body = zlib.compress(raw)
        head = b"<< /Length %d /Filter /FlateDecode >>" % len(body)
    else:
        body = raw
        head = b"<< /Length %d >>" % len(body)
    pdf = (
        b"%PDF-1.4\n1 0 obj\n" + head + b"\nstream\n" + body + b"\nendstream\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    path.write_bytes(pdf)


def make_png(path: Path) -> None:
    header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 640, 480)
    path.write_bytes(header + b"\x08\x02\x00\x00\x00" + b"\x00" * 16)


def make_cid_pdf(path: Path, with_cmap: bool = True) -> None:
    """模拟子集化 CID 字体：正文是字形编码 <010203>，靠 ToUnicode 才能还原。"""
    content = b"BT /F1 12 Tf <010203> Tj ET"
    streams = [(b"<< /Length %d /Filter /FlateDecode >>" % len(zlib.compress(content)),
                zlib.compress(content))]
    if with_cmap:
        cmap = (
            b"/CIDInit /ProcSet findresource begin\n"
            b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
            b"3 beginbfchar\n<01> <0041>\n<02> <0042>\n<03> <0043>\nendbfchar\n"
            b"endcmap"
        )
        streams.append((b"<< /Length %d >>" % len(cmap), cmap))

    body = b"%PDF-1.4\n"
    for index, (head, data) in enumerate(streams, start=1):
        body += b"%d 0 obj\n" % index + head + b"\nstream\n" + data + b"\nendstream\nendobj\n"
    body += b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    path.write_bytes(body)


def main() -> int:
    print("[documents] 开始")
    checks: list[tuple[str, bool]] = []
    tmp = Path(tempfile.mkdtemp(prefix="docs_"))

    try:
        docx = tmp / "a.docx"
        make_docx(docx)
        text = read_document(str(docx))
        checks.append(("docx 提到第一段", "第一段内容" in text))
        checks.append(("docx 合并了同段多块", "第二段 接着写" in text))

        xlsx = tmp / "b.xlsx"
        make_xlsx(xlsx)
        text = read_document(str(xlsx))
        checks.append(("xlsx 有工作表名", "工作表 sheet1" in text))
        checks.append(("xlsx 解析共享字符串", "姓名 | 小罗" in text))
        checks.append(("xlsx 解析数字与行内串", "42 | 行内" in text))

        pdf = tmp / "c.pdf"
        make_pdf(pdf)
        checks.append(("pdf 提取压缩流文本", "Hello PDF World" in read_document(str(pdf))))

        pdf_raw = tmp / "d.pdf"
        make_pdf(pdf_raw, compress=False)
        checks.append(("pdf 提取未压缩流文本", "Hello PDF World" in read_document(str(pdf_raw))))

        cid = tmp / "cid.pdf"
        make_cid_pdf(cid, with_cmap=True)
        checks.append(("CID 字体靠 ToUnicode 还原", "ABC" in read_document(str(cid))))

        cid_bad = tmp / "cid_bad.pdf"
        make_cid_pdf(cid_bad, with_cmap=False)
        checks.append(("没有 ToUnicode 时如实说做不到", "无法还原" in read_document(str(cid_bad))))

        png = tmp / "e.png"
        make_png(png)
        text = read_document(str(png))
        checks.append(("图片返回尺寸", "640x480" in text))
        checks.append(("图片说明读不了内容", "读不了图片内容" in text))

        md = tmp / "f.md"
        md.write_text("# 标题\n正文一段", encoding="utf-8")
        checks.append(("markdown 正常读取", "正文一段" in read_document(str(md))))

        bad = tmp / "g.docx"
        bad.write_bytes(b"not a zip")
        checks.append(("坏文件给出提示", "解析失败" in read_document(str(bad))))

        checks.append(("不存在的文件", "文件不存在" in read_document(str(tmp / "nope.docx"))))
        binfile = tmp / "x.bin"
        binfile.write_bytes(b"\x00\x01")
        checks.append(("不支持的扩展名", "不支持" in read_document(str(binfile))))

        # 截断
        long_md = tmp / "h.md"
        long_md.write_text("字" * 20000, encoding="utf-8")
        out = read_document(str(long_md), max_chars=500)
        checks.append(("超长内容被截断", len(out) < 900))
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    ok_all = all(ok for _, ok in checks)
    print(f"[documents] {'全部通过' if ok_all else '存在失败项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
