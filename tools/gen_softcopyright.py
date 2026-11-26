"""软著申请材料生成 —— 把源码与文档排版成中国版权保护中心要求的提交格式。

产出（默认落在 dist/软著材料_<版本>/）：
1. 源程序_<版本>.docx   前 30 页 + 后 30 页，每页 50 行（不足 60 页则全部提交）
2. 用户手册_<版本>.docx 操作说明文档，最多 60 页（不足则全部提交）
3. 申请信息_<版本>.md   软件全称/版本号/运行环境/编程语言/代码量/功能概述（填表用）

为什么做成脚本：源码每天都在变，临近申请时一键重出即可，
不用手工复制粘贴 60 页。版本与著作权人从 app/brand.py 取，保证署名一致。

用法：
    python tools/gen_softcopyright.py
    python tools/gen_softcopyright.py --out D:/软著材料
"""
import argparse
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
APP = os.path.join(SRC, "app")
DOCS = os.path.join(ROOT, "docs")

sys.path.insert(0, SRC)
from app import brand                                          # noqa: E402

LINES_PER_PAGE = 50        # 源程序每页不少于 50 行（软著要求）
SRC_PAGES_HEAD = 30        # 前 30 页
SRC_PAGES_TAIL = 30        # 后 30 页
DOC_PAGES = 60             # 文档最多 60 页
DOC_LINES_PER_PAGE = 30


# ==========================================================================
# 源码收集
# ==========================================================================
def collect_py_files() -> list[str]:
    out = []
    for root, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "static", "templates")]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return sorted(out)


def read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except Exception:
        return []


def paginate(lines: list[str], per: int) -> list[list[str]]:
    return [lines[i:i + per] for i in range(0, len(lines), per)]


# ==========================================================================
# 文档生成
# ==========================================================================
def _style(doc):
    from docx.shared import Pt
    st = doc.styles["Normal"]
    st.font.name = "宋体"
    st.font.size = Pt(9)


def build_source_doc(files: list[str], out_path: str, version: str) -> dict:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc = Document()
    _style(doc)

    all_lines: list[str] = []
    for p in files:
        rel = os.path.relpath(p, SRC).replace("\\", "/")
        all_lines.append(f"# ===== 文件：{rel} =====")
        all_lines.extend(read_lines(p))
        all_lines.append("")

    pages = paginate(all_lines, LINES_PER_PAGE)
    if len(pages) > SRC_PAGES_HEAD + SRC_PAGES_TAIL:
        chosen = [(i + 1, pg) for i, pg in enumerate(pages[:SRC_PAGES_HEAD])]
        base = len(pages) - SRC_PAGES_TAIL
        chosen += [(base + i + 1, pg) for i, pg in enumerate(pages[-SRC_PAGES_TAIL:])]
        note = f"共 {len(pages)} 页，按要求提交前 {SRC_PAGES_HEAD} 页与后 {SRC_PAGES_TAIL} 页"
    else:
        chosen = [(i + 1, pg) for i, pg in enumerate(pages)]
        note = f"共 {len(pages)} 页，不足 60 页，全部提交"

    for pageno, pg in chosen:
        h = doc.add_paragraph()
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = h.add_run(f"{brand.PRODUCT_NAME_CN} V{version}　源程序　第 {pageno} 页")
        r.bold = True
        r.font.size = Pt(10)
        for line in pg:
            para = doc.add_paragraph()
            para.paragraph_format.space_after = Pt(0)
            para.add_run(line if line.strip() else " ")
        doc.add_page_break()
    doc.save(out_path)
    return {"pages_total": len(pages), "pages_submitted": len(chosen), "note": note,
            "files": len(files), "lines": len(all_lines)}


def build_manual_doc(out_path: str, version: str) -> dict:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    _style(doc)
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(f"{brand.PRODUCT_NAME_CN} V{version}\n用户手册")
    r.bold = True
    r.font.size = Pt(16)
    doc.add_paragraph(f"著作权人：{brand.OWNER}（{brand.OWNER_ID}）").alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(brand.COPYRIGHT_HOLDER).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_page_break()

    # 正文：把 docs 下的 md 原样排入（标题行加粗），再补模块清单
    blocks: list[str] = []
    for name in sorted(os.listdir(DOCS)):
        if not name.endswith(".md"):
            continue
        blocks.append(f"## {name}")
        blocks.extend(read_lines(os.path.join(DOCS, name)))
        blocks.append("")

    sys.path.insert(0, SRC)
    from app.core import registry as R
    blocks.append("## 模块清单")
    blocks.append(f"{'模块':<12}{'代号':<14}{'分组':<8}{'说明'}")
    for m in R.MODULES:
        blocks.append(f"{m['name']:<12}{m['code']:<14}{m['group']:<8}{m['desc']}")
    blocks.append("")
    blocks.append("## 部署方式")
    blocks.extend([
        "1. 运行环境检测：python deploy/detect_env.py",
        "2. 一键部署：python deploy/setup.py --port 8765",
        "3. 部署成功后将在桌面生成部署报告，含访问地址与管理员账号",
        "4. 桌面「Felix 管理控制台」图标可监控运行状态并开关模块",
        "5. 数据保存在 data 目录，建议定期备份",
    ])

    pages = paginate(blocks, DOC_LINES_PER_PAGE)
    chosen = pages[:DOC_PAGES]
    for i, pg in enumerate(chosen, 1):
        h = doc.add_paragraph()
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        hr = h.add_run(f"用户手册　第 {i} 页")
        hr.bold = True
        hr.font.size = Pt(10)
        for line in pg:
            para = doc.add_paragraph()
            para.paragraph_format.space_after = Pt(0)
            run = para.add_run(line if line.strip() else " ")
            if line.startswith("#"):
                run.bold = True
        doc.add_page_break()
    doc.save(out_path)
    return {"pages_total": len(pages), "pages_submitted": len(chosen)}


def build_apply_info(out_path: str, version: str, src_stat: dict, man_stat: dict) -> dict:
    total_lines = src_stat["lines"]
    info = f"""# 软件著作权申请材料信息（填表用）

- 软件全称：{brand.PRODUCT_NAME_CN} V{version}
- 软件简称：{brand.PRODUCT_ABBR}
- 版本号：V{version}
- 著作权人：{brand.OWNER}（{brand.OWNER_ID}）
- 权利取得方式：原始取得
- 权利范围：全部权利
- 开发完成日期：{datetime.now():%Y-%m-%d}
- 首次发表日期：{datetime.now():%Y-%m-%d}
- 开发环境：Windows 10 / Python {sys.version.split()[0]}
- 运行环境：Windows / Linux / macOS，自带 Python 运行时，无需额外安装
- 编程语言：Python 3（后端 FastAPI，前端 JavaScript）
- 数据库：SQLite（WAL 模式）
- 源程序量：{total_lines} 行 / {src_stat['files']} 个文件
- 提交页数：源程序 {src_stat['pages_submitted']} 页（{src_stat['note']}）；文档 {man_stat['pages_submitted']} 页

## 功能与技术特点

1. 模块化架构：{len(__import__('app.core.registry', fromlist=['x']).MODULES)} 个业务模块可按需启停，
   关闭模块只隐藏功能与菜单，数据保留、随时可再开启。
2. 多组织穿透解析：组织架构中间层级缺失时自动关联到最近的有效组织，
   小公司扁平架构与大集团多层架构共用一套代码，不因层级缺失影响运行。
3. 一键部署：部署前强制环境检测，不满足则给出可执行修复方案；
   部署成功自动在桌面生成部署报告。
4. 数据全部本地存储，不上传云端；操作全程留痕，支持审计追溯。

## 提交清单

- [ ] 源程序docx（前30页+后30页，每页50行）
- [ ] 用户手册docx（最多60页）
- [ ] 申请表（本文件信息填入）
- [ ] 身份证明（个人：身份证复印件）
- [ ] 说明书与源程序页眉须含软件名称与版本号（本脚本已自动添加）
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(info)
    return {"path": out_path}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()

    version = brand.VERSION
    outdir = os.path.join(args.out, f"软著材料_{version}")
    os.makedirs(outdir, exist_ok=True)

    print(f"[软著] 软件 {brand.PRODUCT_NAME_CN} V{version}　著作权人 {brand.OWNER}")
    files = collect_py_files()
    print(f"[软著] 扫描到 {len(files)} 个源码文件")

    src_path = os.path.join(outdir, f"源程序_V{version}.docx")
    s = build_source_doc(files, src_path, version)
    print(f"[软著] 源程序：{s['lines']} 行 → {s['pages_submitted']} 页（{s['note']}）")

    man_path = os.path.join(outdir, f"用户手册_V{version}.docx")
    m = build_manual_doc(man_path, version)
    print(f"[软著] 用户手册：{m['pages_submitted']} 页")
    if m["pages_submitted"] < DOC_PAGES:
        print(f"[软著] 提示：文档不足 {DOC_PAGES} 页（当前 {m['pages_submitted']} 页）。"
              "不足可全部提交，但建议补充各模块操作步骤与界面说明，降低补正概率。")

    info_path = os.path.join(outdir, f"申请信息_V{version}.md")
    build_apply_info(info_path, version, s, m)
    print(f"[软著] 申请信息：{info_path}")

    print(f"\n[软著] 全部产出在：{outdir}")
    print("      打印为 PDF 后即可提交（页眉已含软件名与版本号）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
