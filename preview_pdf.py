"""Convert generated docx to PDF using Word COM and split into page images."""
import os, sys
import win32com.client as win32

DOCX = r"D:\论文\毕业材料\毕业材料\毕业论文_基于大语言模型的直播互动智能助手系统设计与实现_最终格式版.docx"
PDF  = DOCX[:-5] + ".pdf"

word = win32.gencache.EnsureDispatch("Word.Application")
word.Visible = False
try:
    doc = word.Documents.Open(DOCX, ReadOnly=True)
    # Refresh fields (TOC, PAGE numbers)
    doc.Fields.Update()
    # 17 = wdFormatPDF
    doc.SaveAs2(PDF, FileFormat=17)
    doc.Close(False)
    print(f"PDF saved: {PDF}")
finally:
    word.Quit()
