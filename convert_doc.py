"""Convert .doc -> .docx using Word COM."""
import os, sys
import win32com.client as win32

files = [
    r"D:\论文\附件4 毕业设计（论文）参考样本.doc",
    r"D:\论文\附件3 毕业设计（论文）撰写规范及格式.doc",
    r"D:\论文\毕业材料\毕业材料\毕业论文_基于大语言模型的直播互动智能助手系统设计与实现_附件4模板版.doc",
]

word = win32.gencache.EnsureDispatch("Word.Application")
word.Visible = False
try:
    for src in files:
        dst = src[:-4] + ".docx"
        if os.path.exists(dst):
            print(f"SKIP exists: {dst}")
            continue
        print(f"Converting: {src}")
        doc = word.Documents.Open(src, ReadOnly=True)
        # 16 = wdFormatXMLDocument (.docx)
        doc.SaveAs2(dst, FileFormat=16)
        doc.Close(False)
        print(f"  -> {dst}")
finally:
    word.Quit()
print("Done.")
