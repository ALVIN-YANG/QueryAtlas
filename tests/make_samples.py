"""Generate owned, redistributable acceptance documents on the Linux test image."""

import csv
import io
import sys
from pathlib import Path

import pypdfium2 as pdfium
from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont


def text_pdf():
    stream = b"BT /F1 16 Tf 32 128 Td (Manual reference: QA-739. Delivery cutoff is UTC.) Tj ET"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Count 1/Kids[3 0 R]>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 550 200]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length "
        + str(len(stream)).encode()
        + b">>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 6\n0000000000 65535 f \n")
    for o in offsets:
        buf.write(f"{o:010} 00000 n \n".encode())
    buf.write(f"trailer<</Size 6/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode())
    return buf.getvalue()


def picture(lines):
    img = Image.new("RGB", (1250, 240), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 48)
    for i, s in enumerate(lines):
        draw.text((45, 30 + i * 82), s, fill="black", font=font)
    return img


def create(folder):
    folder.mkdir(parents=True, exist_ok=True)
    pic = picture(["复核单号  QA-4827", "异常订单须由数据负责人复核"])
    pic.save(folder / "复核单.png")
    pic.save(folder / "复核单照片.jpg", quality=95)
    img = picture(["归档期限为九十天", "归档编号  QA-9206"])
    scan = io.BytesIO()
    img.save(scan, "PDF", resolution=144)
    with pdfium.PdfDocument.new() as doc:
        for content in (text_pdf(), scan.getvalue()):
            with pdfium.PdfDocument(content) as src:
                doc.import_pages(src)
        doc.save(folder / "归档手册.pdf")
    doc = Document()
    doc.add_heading("延期收货的订单口径", 1)
    doc.add_paragraph("同一订单即使有两条延期收货明细，也只计一次。规则编号 QA-3618。")
    tbl = doc.add_table(2, 2)
    tbl.cell(0, 0).text = "字段"
    tbl.cell(0, 1).text = "单位"
    tbl.cell(1, 0).text = "订单数"
    tbl.cell(1, 1).text = "单"
    doc.save(folder / "指标口径.docx")
    (folder / "版本说明.html").write_text(
        "<meta charset=utf-8><h1>发布说明</h1><p>核对后发布，编号 QA-7102。</p>"
    )
    with (folder / "字段字典.csv").open("w") as f:
        csv.writer(f).writerows([["字段", "含义"], ["o_orderkey", "订单键 QA-5120"]])
    book = Workbook()
    sheet = book.active
    sheet.title = "审核表"
    sheet.append(["部门", "复核编号"])
    sheet.append(["供应分析", "QA-6552"])
    book.save(folder / "审核清单.xlsx")


if __name__ == "__main__":
    create(Path(sys.argv[1]))
