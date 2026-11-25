"""Extract searchable text together with positions in the immutable original."""

import csv
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from bs4 import BeautifulSoup
from docx import Document
from docx.table import Table
from openpyxl import load_workbook
from PIL import Image, ImageOps

SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".html", ".csv", ".xlsx"}
MAX_BYTES = 12 * 1024 * 1024
MAX_CHARS = 250_000
MAX_PIXELS = 22_000_000


class ReviewNeeded(ValueError):
    pass


@dataclass
class Passage:
    anchor: str
    text: str
    method: str = "text"


def ocr(image, anchor):
    if image.width * image.height > MAX_PIXELS:
        raise ReviewNeeded(f"{anchor} 超出图片大小限制")
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        ImageOps.exif_transpose(image).convert("RGB").save(tmp.name)
        result = subprocess.run(
            ["tesseract", tmp.name, "stdout", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
            capture_output=True,
            timeout=45,
            check=True,
        )
    words = [
        r
        for r in csv.DictReader(
            result.stdout.decode().splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE
        )
        if r["level"] == "5" and (r["text"] or "").strip()
    ]
    if not words or sum(float(r["conf"]) for r in words) / len(words) < 60:
        raise ReviewNeeded(f"{anchor} 无文字或 OCR 置信度低，请核对原件")
    lines = {}
    for word in words:
        key = (word["block_num"], word["par_num"], word["line_num"])
        lines.setdefault(key, []).append(word["text"].strip())
    return Passage(anchor, "\n".join(" ".join(line) for line in lines.values()), "ocr")


def pdf_pages(path):
    with pdfium.PdfDocument(path) as doc:
        if len(doc) > 150:
            raise ReviewNeeded("PDF 超出 150 页")
        for i in range(len(doc)):
            page = doc[i]
            try:
                anchor = f"第 {i + 1} 页"
                textpage = page.get_textpage()
                text = textpage.get_text_bounded().strip()
                textpage.close()
                if text:
                    yield Passage(anchor, text)
                width, height = page.get_size()
                # Also recognise a scanned body underneath a short digital header.
                large_image = any(
                    abs((r - l) * (t - b)) > width * height * 0.30
                    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])
                    for l, b, r, t in [obj.get_bounds()]
                )
                if not text or large_image:
                    scale = min(
                        2.8, (MAX_PIXELS / max(1, width * height)) ** 0.5 * 0.99
                    )
                    bitmap = page.render(scale=scale)
                    try:
                        yield ocr(bitmap.to_pil(), anchor)
                    finally:
                        bitmap.close()
            finally:
                page.close()


def word_parts(path):
    doc = Document(path)
    if doc.element.body.xpath(".//w:tbl//w:drawing") or doc.element.body.xpath(
        ".//w:tbl//w:pict"
    ):
        raise ReviewNeeded("Word 表格含嵌入图片，请将图片单独导入")
    heading = ""
    for i, item in enumerate(doc.iter_inner_content(), 1):
        if isinstance(item, Table):
            for j, row in enumerate(item.rows, 1):
                yield Passage(
                    f"{heading} · 表 {i} 行 {j}", " | ".join(c.text for c in row.cells)
                )
        else:
            if item._p.xpath(".//w:drawing") or item._p.xpath(".//w:pict"):
                raise ReviewNeeded(f"第 {i} 段含嵌入图片，请将图片单独导入")
            if item.style and item.style.name.startswith("Heading"):
                heading = item.text[:100]
            yield Passage(f"{heading} · 第 {i} 段".strip(" ·"), item.text)


def table_rows(path):
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as src:
            rows = csv.reader(src)
            header = next(rows, [])
            for row in rows:
                if len(row) != len(header):
                    raise ReviewNeeded(f"CSV 第 {rows.line_num} 行列数不符")
                yield Passage(
                    f"CSV 至第 {rows.line_num} 行",
                    " | ".join(f"{k}={v}" for k, v in zip(header, row)),
                )
        return
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in book:
            rows = sheet.values
            header = next(rows, ())
            for i, row in enumerate(rows, 2):
                yield Passage(
                    f"{sheet.title} · 第 {i} 行",
                    " | ".join(
                        f"{k}={v}" for k, v in zip(header, row) if v is not None
                    ),
                )
    finally:
        book.close()


def html_elements(path):
    soup = BeautifulSoup(path.read_bytes(), "html.parser", from_encoding="utf-8")
    return [
        (tag.name, tag.get_text(" ", strip=True))
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr"])
    ]


def extract(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".docx", ".xlsx"}:
        with zipfile.ZipFile(path) as z:
            if sum(e.file_size for e in z.infolist()) > 64 * 1024**2:
                raise ReviewNeeded("解包后超出 64 MiB")
    if suffix == ".pdf":
        parts = pdf_pages(path)
    elif suffix in {".png", ".jpg", ".jpeg"}:
        with Image.open(path) as img:
            parts = [ocr(img, "原图")]
    elif suffix == ".docx":
        parts = word_parts(path)
    elif suffix in {".csv", ".xlsx"}:
        parts = table_rows(path)
    elif suffix == ".html":
        heading = ""
        parts = []
        for i, (tag, content) in enumerate(html_elements(path), 1):
            if tag.startswith("h"):
                heading = content[:100]
            parts.append(Passage(f"{heading} · 元素 {i}", content))
    else:
        raise ReviewNeeded("文件格式不支持")
    result, total = [], 0
    for part in parts:
        if part.text.strip():
            total += len(part.text)
            if total > MAX_CHARS or len(result) >= 5000:
                raise ReviewNeeded("文档文本超出单次导入限制")
            result.append(part)
    if not result:
        raise ReviewNeeded("没有可索引的文字，请核对原件")
    return result
