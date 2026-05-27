"""Render PDF pages to PNGs using PyMuPDF (fitz)."""
import os, sys
try:
    import fitz
except ImportError:
    os.system(sys.executable + " -m pip install --quiet pymupdf")
    import fitz

PDF = r"D:\论文\毕业材料\毕业材料\毕业论文_基于大语言模型的直播互动智能助手系统设计与实现_最终格式版.pdf"
OUT = r"d:\Pjt\260414\pdf_pages"
os.makedirs(OUT, exist_ok=True)

doc = fitz.open(PDF)
print(f"Pages: {doc.page_count}")
for i in range(doc.page_count):
    page = doc[i]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x scale
    out_path = os.path.join(OUT, f"page_{i+1:02d}.png")
    pix.save(out_path)
print("done")
