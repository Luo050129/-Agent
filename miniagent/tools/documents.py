"""工具层：文档解析。

一个入口 `read_document`，按扩展名分派：
    .docx / .xlsx  → 本质是 zip + xml，标准库直接解，不装 python-docx / openpyxl
    .pdf           → 提文本层：解 FlateDecode、按 BT/ET 文本块扫描、认 ToUnicode 映射
    文本类          → 直接读
    图片           → 只能读尺寸和大小，读不了画面内容，明确告诉模型别瞎猜

PDF 这块刻意不追求全能：扫描件要 OCR、复杂 CID 字体要完整字体表，这些都不是标准库能干的活。
做不到就直说，比糊一段乱码强。
"""

from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

from ._util import ERR, truncate  # 标记单一来源见 ../markers.py


TEXT_EXTS = {".txt", ".md", ".markdown", ".py", ".java", ".js", ".ts", ".json", ".yaml", ".yml",
             ".csv", ".tsv", ".log", ".sql", ".xml", ".html", ".css", ".sh", ".bat", ".ini", ".toml"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


# ---------------------------------------------------------------- docx


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            return f"{ERR}不是有效的 docx：缺少 word/document.xml"
        xml = zf.read("word/document.xml")

    root = ElementTree.fromstring(xml)
    lines: list[str] = []
    for para in root.iter():
        if _local(para.tag) != "p":
            continue
        buf = []
        for node in para.iter():
            tag = _local(node.tag)
            if tag == "t" and node.text:
                buf.append(node.text)
            elif tag == "tab":
                buf.append("\t")
            elif tag == "br":
                buf.append("\n")
        text = "".join(buf).strip()
        if text:
            lines.append(text)
    return "\n".join(lines) if lines else f"{ERR}文档里没有可提取的文字"


# ---------------------------------------------------------------- xlsx


_COL_RE = re.compile(r"([A-Z]+)(\d+)")


def _col_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                if _local(si.tag) != "si":
                    continue
                shared.append("".join(node.text or "" for node in si.iter() if _local(node.tag) == "t"))

        sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not sheets:
            return f"{ERR}不是有效的 xlsx：没有工作表"

        out: list[str] = []
        for sheet in sheets:
            root = ElementTree.fromstring(zf.read(sheet))
            rows: list[list[str]] = []
            for row in root.iter():
                if _local(row.tag) != "row":
                    continue
                cells: dict[int, str] = {}
                for cell in row:
                    if _local(cell.tag) != "c":
                        continue
                    ref = cell.get("r") or ""
                    match = _COL_RE.match(ref)
                    col = _col_index(match.group(1)) if match else len(cells)
                    ctype = cell.get("t")
                    value = ""
                    for node in cell.iter():
                        tag = _local(node.tag)
                        if tag == "v" and node.text:
                            value = node.text
                        elif tag == "t" and node.text and ctype == "inlineStr":
                            value += node.text
                    if ctype == "s" and value.isdigit():
                        idx = int(value)
                        value = shared[idx] if 0 <= idx < len(shared) else value
                    cells[col] = value
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
            name = sheet.rsplit("/", 1)[-1].replace(".xml", "")
            body = "\n".join(" | ".join(r) for r in rows)
            out.append(f"[工作表 {name}] {len(rows)} 行\n{body}" if rows else f"[工作表 {name}] 空")
        return "\n\n".join(out)


# ---------------------------------------------------------------- pdf


_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
_BT_ET_RE = re.compile(rb"BT(.*?)ET", re.S)
_ARRAY_TJ_RE = re.compile(rb"\[(.*?)\]\s*TJ", re.S)
_TOKEN_RE = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>")
_SCAN_RE = re.compile(
    rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>|[-+]?\d*\.?\d+|T[jJ]|Td|TD|T\*|Tm",
    re.S,
)
_PRINTABLE_RE = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")

_ESCAPE_TABLE = {
    b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
    b"(": b"(", b")": b")", b"\\": b"\\",
}


def _pdf_unescape(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == 0x5C and i + 1 < len(raw):  # 反斜杠转义
            nxt = raw[i + 1 : i + 2]
            if nxt in _ESCAPE_TABLE:
                out += _ESCAPE_TABLE[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1 : i + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF)
                i += 1 + len(octal.group(0))
                continue
            i += 2
            continue
        out.append(ch)
        i += 1
    return bytes(out)


def _parse_tounicode(streams: list[bytes]) -> tuple[dict[str, str], int]:
    """解析 ToUnicode CMap，返回 (码 → 字符, 码字节宽度)。

    没有它，子集化 CID 字体的 PDF 只能解出字形编号，也就是一堆乱码。
    """
    mapping: dict[str, str] = {}

    for raw in streams:
        if b"beginbfchar" not in raw and b"beginbfrange" not in raw:
            continue
        text = raw.decode("latin-1")

        for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
            for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                try:
                    mapping[src.upper()] = bytes.fromhex(dst).decode("utf-16-be", "replace")
                except ValueError:
                    continue

        for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
            for lo, hi, dst in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
            ):
                try:
                    start, stop, base = int(lo, 16), int(hi, 16), int(dst, 16)
                except ValueError:
                    continue
                digits = len(lo)
                for offset in range(min(stop - start, 0xFFFF) + 1):
                    mapping[f"{start + offset:0{digits}X}"] = chr(base + offset)

    width = max((len(k) // 2 for k in mapping), default=1)
    return mapping, max(1, width)


def _decode_hex(hexstr: str, mapping: dict[str, str], width: int) -> str:
    step = width * 2
    out: list[str] = []
    for i in range(0, len(hexstr), step):
        code = hexstr[i : i + step].upper()
        if mapping:
            out.append(mapping.get(code, ""))
        else:
            try:
                out.append(bytes.fromhex(code).decode("latin-1"))
            except ValueError:
                continue
    return "".join(out)


def _decode_literal(raw: bytes, mapping: dict[str, str], width: int) -> str:
    if mapping and width == 1:
        return "".join(mapping.get(f"{b:02X}", "") for b in raw)
    if mapping and width == 2 and len(raw) % 2 == 0:
        return "".join(mapping.get(raw[i : i + 2].hex().upper(), "") for i in range(0, len(raw), 2))
    for encoding in ("utf-8", "utf-16-be"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _decode_token(token: bytes, mapping: dict[str, str], width: int) -> str:
    if token.startswith(b"<"):
        hexdata = re.sub(rb"\s", b"", token[1:-1]).decode("latin-1")
        return _decode_hex(hexdata, mapping, width)
    return _decode_literal(_pdf_unescape(token[1:-1]), mapping, width)


def _array_text(inner: bytes, mapping: dict[str, str], width: int) -> str:
    """TJ 数组：字符串直接拼，数字是字间距，明显负值说明这里该有空格。"""
    parts: list[str] = []
    for match in re.finditer(rb"(\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>)|([-+]?\d*\.?\d+)", inner):
        if match.group(1):
            parts.append(_decode_token(match.group(1), mapping, width))
        elif match.group(2):
            try:
                if float(match.group(2)) < -120:
                    parts.append(" ")
            except ValueError:
                continue
    return "".join(parts)


def _scan_segment(block: bytes, mapping: dict[str, str], width: int, last_y: float | None) -> tuple[str, float | None]:
    """扫一段不含 TJ 数组的指令，靠 Td / Tm / T* 判断断行。

    注意：不靠水平位移猜空格 —— 很多 PDF 的空格本身就是字形，猜了反而拆坏单词。
    """
    out: list[str] = []
    pending: list[str] = []
    nums: list[float] = []

    for match in _SCAN_RE.finditer(block):
        token = match.group(0)

        if token in (b"Tj", b"TJ"):
            if pending:
                out.append("".join(pending))
                pending = []
            nums.clear()
            continue

        if token in (b"Td", b"TD"):
            if len(nums) >= 2 and abs(nums[-1]) > 0.5:
                out.append("\n")
            nums.clear()
            continue

        if token == b"Tm":
            if len(nums) >= 6:
                new_y = nums[-1]
                out.append("\n" if last_y is not None and abs(new_y - last_y) > 0.5 else "")
                last_y = new_y
            nums.clear()
            continue

        if token == b"T*":
            out.append("\n")
            nums.clear()
            continue

        if token.startswith((b"<", b"(")):
            pending.append(_decode_token(token, mapping, width))
            continue

        try:
            nums.append(float(token))
        except ValueError:
            continue

    if pending:
        out.append("".join(pending))
    return "".join(out), last_y


def _block_text(block: bytes, mapping: dict[str, str], width: int) -> str:
    """一个 BT…ET 块：把 TJ 数组抠出来单独处理，其余片段走扫描器。"""
    out: list[str] = []
    last_y: float | None = None
    pos = 0

    for match in _ARRAY_TJ_RE.finditer(block):
        text, last_y = _scan_segment(block[pos : match.start()], mapping, width, last_y)
        out.append(text)
        out.append(_array_text(match.group(1), mapping, width))
        pos = match.end()

    text, _ = _scan_segment(block[pos:], mapping, width, None)
    out.append(text)
    return "".join(out)


def _pdf_text(path: Path) -> str:
    data = path.read_bytes()
    streams: list[bytes] = []
    for raw in _STREAM_RE.findall(data):
        try:  # 大多数 PDF 的内容流是 Flate 压缩的
            streams.append(zlib.decompress(raw))
        except zlib.error:
            streams.append(raw)

    mapping, width = _parse_tounicode(streams)

    chunks: list[str] = []
    for stream in streams:
        if b"BT" not in stream:  # 字体、图片这些流里没有文本块，跳过
            continue
        for block in _BT_ET_RE.findall(stream):
            text = _block_text(block, mapping, width)
            text = re.sub(r"[ \t]{2,}", " ", text).strip()
            if text:
                chunks.append(text)

    body = "\n".join(chunks).strip()
    if not body:
        return f"{ERR}没提取到文本层：可能是扫描件或纯图片 PDF，需要 OCR，本工具做不到"

    printable = len(_PRINTABLE_RE.findall(body))
    if printable / max(1, len(body)) < 0.85:
        return (
            f"{ERR}提取到的内容无法还原：这份 PDF 的字体既没有 ToUnicode 映射，也不是常见编码。"
            "本工具只能提文本层，建议用专业库或先转成文本"
        )

    return truncate(body, 6000)


# ---------------------------------------------------------------- 图片


def _image_info(path: Path) -> str:
    data = path.read_bytes()
    size = f"{len(data) / 1024:.1f} KB"
    dims = ""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        dims = f"{width}x{height}"
    elif data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                dims = f"{width}x{height}"
                break
            seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + seg
    return (
        f"图片：{path.name}（{dims or '尺寸未知'}，{size}）\n"
        "注意：当前模型读不了图片内容，我只能给你这些元信息。\n"
        "要继续就用文字描述图里的内容，或者换一个支持视觉的模型。"
    )


# ---------------------------------------------------------------- 入口


def read_document(path: str, max_chars: int = 6000) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"{ERR}文件不存在：{p}"

    suffix = p.suffix.lower()
    try:
        if suffix == ".docx":
            body = _docx_text(p)
        elif suffix == ".xlsx":
            body = _xlsx_text(p)
        elif suffix == ".pdf":
            body = _pdf_text(p)
        elif suffix in IMAGE_EXTS:
            return _image_info(p)
        elif suffix in TEXT_EXTS or suffix == "":
            body = p.read_text(encoding="utf-8", errors="replace")
        else:
            return f"{ERR}不支持的文件类型：{suffix}（支持 docx / xlsx / pdf / 文本 / 图片元信息）"
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return f"{ERR}文件解析失败（可能已损坏或格式不对）：{exc}"
    except OSError as exc:
        return f"{ERR}读取失败：{exc}"

    return truncate(f"[{p.name}]\n{body}", max_chars)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "读取文档内容，按扩展名自动解析：docx、xlsx、pdf、常见文本/markdown/代码。图片只能返回尺寸和大小，读不出画面内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "max_chars": {"type": "integer", "description": "最多返回字符数，默认 6000"},
                },
                "required": ["path"],
            },
        },
    }
]

HANDLERS = {"read_document": read_document}
DANGEROUS: set[str] = set()
