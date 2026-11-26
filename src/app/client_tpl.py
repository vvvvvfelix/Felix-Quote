"""客户自带报价单模板的解析与填充。

真实业务里，每个客户给的报价单格式都不一样（有 Excel、有 Word），
没法用系统预设模板套。所以这里的做法是：

1. 客户上传自己的模板文件（.xlsx / .xlsm / .docx）
2. 系统解析出结构，列出所有可填的位置（Excel 单元格 / Word 表格与段落）
3. 用户做一次「字段映射」：哪个位置填哪个数据（报价单号、客户、明细...）
4. 之后每次出报价单，选这份模板 + 选报价单 → 一键按原格式填好导出

关键点：填充时**不动模板原有样式**。Excel 用 openpyxl 读写保留格式；
Word 用 python-docx 在原有 run 上替换文字，保留字体段落。
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime

import openpyxl
from openpyxl.utils import get_column_letter

from .database import get_conn, jdump, jload, now, release_conn

SUPPORTED = {".xlsx": "xlsx", ".xlsm": "xlsm", ".docx": "docx"}

# 可映射的字段（key -> 中文标签），与 quote.py 的 ctx 结构一致
FIELD_LABELS = {
    "quote.quote_no": "报价单号",
    "quote.date": "报价日期",
    "quote.valid_until": "有效期至",
    "quote.currency": "币种",
    "quote.incoterm": "贸易条款",
    "quote.port": "装运港",
    "quote.payment_terms": "付款方式",
    "quote.delivery": "交货期",
    "quote.valid_days": "有效天数",
    "quote.title": "报价单标题",
    "quote.remark": "备注",
    "customer.company_en": "客户公司名",
    "customer.company_cn": "客户公司名(中文)",
    "customer.contact": "客户联系人",
    "customer.country": "客户国家",
    "customer.address": "客户地址",
    "customer.email": "客户邮箱",
    "customer.phone": "客户电话",
    "company.name_en": "我方公司(英文)",
    "company.name_cn": "我方公司(中文)",
    "company.brand": "品牌",
    "company.phone": "我方电话",
    "company.email": "我方邮箱",
    "company.website": "我方网址",
    "company.address": "我方地址",
    "totals.subtotal": "小计",
    "totals.freight": "运费",
    "totals.insurance": "保险费",
    "totals.discount": "折扣",
    "totals.total": "总计",
    "totals.total_cny": "总计(人民币)",
    "items.count": "产品条目数",
    "today": "今天日期",
    "now": "当前时间",
}

# 明细行可映射的列
ITEM_FIELDS = {
    "model": "型号 Model",
    "sku": "货号 SKU",
    "name_cn": "品名(中文)",
    "name_en": "品名(英文)",
    "spec": "规格",
    "spec_en": "规格(英文)",
    "material": "材质",
    "power": "功率/电压",
    "capacity": "容积/层数",
    "dimension": "外形尺寸",
    "packing_size": "包装尺寸",
    "net_weight": "净重",
    "gross_weight": "毛重",
    "carton": "包装体积",
    "certs": "认证",
    "hs_code": "HS编码",
    "lead_time": "交期",
    "qty": "数量",
    "price": "单价",
    "amount": "金额",
    "remark": "备注",
}


# --------------------------------------------------------------------------
# 结构解析
# --------------------------------------------------------------------------
def parse_excel(path: str) -> dict:
    """列出 Excel 里所有非空单元格，供用户做映射。"""
    wb = openpyxl.load_workbook(path, data_only=False)
    sheets = []
    for ws in wb.worksheets:
        cells = []
        max_r = min(ws.max_row or 0, 200)
        max_c = min(ws.max_column or 0, 40)
        for row in ws.iter_rows(min_row=1, max_row=max_r,
                                min_col=1, max_col=max_c):
            for c in row:
                v = c.value
                if v is None:
                    continue
                text = str(v).strip()
                if not text:
                    continue
                cells.append({
                    "ref": c.coordinate,          # B3
                    "row": c.row,
                    "col": c.column,
                    "text": text[:80],
                    "merged": _is_merged(ws, c.coordinate),
                })
        sheets.append({
            "name": ws.title,
            "cells": cells,
            "max_row": ws.max_row,
            "max_col": ws.max_column,
            "merged_ranges": [str(r) for r in ws.merged_cells.ranges][:60],
        })
    wb.close()
    return {"kind": "excel", "sheets": sheets,
            "default_sheet": sheets[0]["name"] if sheets else ""}


def _is_merged(ws, coord: str) -> bool:
    try:
        return any(coord in r for r in ws.merged_cells.ranges)
    except Exception:
        return False


def parse_docx(path: str) -> dict:
    """列出 Word 里的段落与表格位置，供占位符替换。"""
    import docx
    d = docx.Document(path)
    blocks = []
    p_idx = t_idx = 0
    for el in d.element.body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            texts = [t.text for t in el.iter() if t.tag.endswith("}t")]
            s = "".join(texts).strip()
            if s:
                blocks.append({"type": "p", "index": p_idx, "text": s[:120]})
            p_idx += 1
        elif tag == "tbl":
            rows = []
            for tr in el.findall(".//{*}tr"):
                cells = []
                for tc in tr.findall("./{*}tc"):
                    txt = "".join(t.text or "" for t in tc.iter()
                                  if t.tag.endswith("}t")).strip()
                    cells.append(txt[:40])
                if any(cells):
                    rows.append(cells)
            if rows:
                blocks.append({"type": "table", "index": t_idx,
                               "rows": rows[:30],
                               "row_count": len(rows)})
            t_idx += 1
    return {"kind": "word", "blocks": blocks,
            "paragraph_count": len(d.paragraphs),
            "table_count": len(d.tables)}


def parse_template(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return parse_excel(path)
    if ext == ".docx":
        return parse_docx(path)
    raise ValueError(f"不支持的模板格式：{ext}（可用 .xlsx / .xlsm / .docx）")


# --------------------------------------------------------------------------
# 取值
# --------------------------------------------------------------------------
def build_context(quote_id: int) -> dict:
    """把报价单 + 客户 + 公司 + 明细组装成扁平可填充的字典。"""
    from .quote import render
    _html, ctx = render(quote_id)
    q, cu, comp, totals = (ctx["quote"], ctx["customer"],
                           ctx["company"], ctx["totals"])

    amt = float(totals.get("total") or 0)
    flat = {
        "quote.quote_no": q.get("quote_no", ""),
        "quote.date": q.get("date", ""),
        "quote.valid_until": q.get("valid_until", ""),
        "quote.currency": q.get("currency", "USD"),
        "quote.incoterm": q.get("incoterm", ""),
        "quote.port": q.get("port", ""),
        "quote.payment_terms": q.get("payment_terms", ""),
        "quote.delivery": q.get("delivery", ""),
        "quote.valid_days": q.get("valid_days", ""),
        "quote.title": q.get("title", ""),
        "quote.remark": q.get("remark", ""),
        "customer.company_en": cu.get("company_en", ""),
        "customer.company_cn": cu.get("company_cn", ""),
        "customer.contact": cu.get("contact", ""),
        "customer.country": cu.get("country", ""),
        "customer.address": cu.get("address", ""),
        "customer.email": cu.get("email", ""),
        "customer.phone": cu.get("phone", ""),
        "company.name_en": comp.get("name_en", ""),
        "company.name_cn": comp.get("name_cn", ""),
        "company.brand": comp.get("brand", ""),
        "company.phone": comp.get("phone", ""),
        "company.email": comp.get("email", ""),
        "company.website": comp.get("website", ""),
        "company.address": comp.get("address", ""),
        "totals.subtotal": totals.get("subtotal", 0),
        "totals.freight": totals.get("freight", 0),
        "totals.insurance": totals.get("insurance", 0),
        "totals.discount": totals.get("discount", 0),
        "totals.total": amt,
        "totals.total_cny": round(amt * 7.1, 2),
        "items.count": len(ctx["items"]),
        "today": datetime.now().strftime("%Y-%m-%d"),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return {"flat": flat, "items": ctx["items"],
            "currency": q.get("currency", "USD")}


def _fmt_item(it: dict, key: str, currency: str) -> str:
    """明细单元格文本格式化。"""
    if key == "amount":
        try:
            return f"{float(it.get('qty') or 0) * float(it.get('price') or 0):,.2f}"
        except (TypeError, ValueError):
            return ""
    if key == "price":
        try:
            v = float(it.get("price") or 0)
            return f"{v:,.2f}"
        except (TypeError, ValueError):
            return ""
    if key == "qty":
        return str(it.get("qty", ""))
    if key == "certs":
        c = it.get("certs")
        if isinstance(c, list):
            return ", ".join(str(x) for x in c)
        return str(c or "")
    v = it.get(key, "")
    return "" if v is None else str(v)


# --------------------------------------------------------------------------
# 填充
# --------------------------------------------------------------------------
def fill_excel(src: str, dst: str, quote_id: int,
               field_map: dict, item_map: dict, sheet_name: str = "") -> dict:
    """按映射把报价单填进 Excel 模板，保留原有格式。"""
    ctx = build_context(quote_id)
    flat, items, cur = ctx["flat"], ctx["items"], ctx["currency"]

    # xlsm 含宏，必须 keep_vba
    keep_vba = src.lower().endswith(".xlsm")
    wb = openpyxl.load_workbook(src, keep_vba=keep_vba)
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active

    filled, skipped = [], []

    # 1) 普通字段
    for ref, key in (field_map or {}).items():
        if not ref or not key:
            continue
        if key not in flat:
            skipped.append({"ref": ref, "key": key, "why": "未知字段"})
            continue
        try:
            ws[ref] = flat[key]
            filled.append({"ref": ref, "key": key,
                           "value": str(flat[key])[:40]})
        except Exception as exc:
            skipped.append({"ref": ref, "key": key, "why": str(exc)[:60]})

    # 2) 明细行
    item_cells = 0
    im = item_map or {}
    header_row = int(im.get("header_row") or 0)
    cols = im.get("cols") or {}
    max_items = int(im.get("max_rows") or 0) or len(items)

    if header_row and cols:
        for i, it in enumerate(items[:max_items]):
            r = header_row + i + 1        # 表头下一行开始放数据
            for col_letter, key in cols.items():
                if not col_letter or not key:
                    continue
                try:
                    ws[f"{col_letter}{r}"] = _fmt_item(it, key, cur)
                    item_cells += 1
                except Exception:
                    continue
        # 明细行格式：复制首行数据行的样式给新增行
        _clone_row_style(ws, header_row + 1, header_row + len(items), cols)
        # 序号列自动编号
        seq_col = im.get("seq_col")
        if seq_col:
            for i in range(len(items[:max_items])):
                try:
                    ws[f"{seq_col}{header_row + 1 + i}"] = i + 1
                except Exception:
                    continue
        # 删除多余空行
        if im.get("clear_extra") and max_items > len(items):
            _clear_rows(ws, header_row + 1 + len(items),
                        header_row + max_items, cols)

    # 3) 占位符替换：客户模板里直接写 {{quote.remark}} 这类标记
    ph_replaced = _replace_excel_placeholders(ws, flat)

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    wb.save(dst)
    wb.close()

    return {
        "output": dst, "filled": filled, "skipped": skipped,
        "item_rows": len(items[:max_items]), "item_cells": item_cells,
        "field_count": len(filled) + ph_replaced,
        "placeholder_replaced": ph_replaced,
    }


# Excel/Word 都能识别的占位符：{{ quote.remark }} / {{quote.remark}}
PH_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


def _replace_excel_placeholders(ws, flat: dict) -> int:
    """扫描所有单元格，把 {{字段}} 替换成实际值（保留同格其他文字）。"""
    n = 0
    max_r = min(ws.max_row or 0, 500)
    max_c = min(ws.max_column or 0, 60)
    for row in ws.iter_rows(min_row=1, max_row=max_r,
                            min_col=1, max_col=max_c):
        for c in row:
            v = c.value
            if not isinstance(v, str) or "{{" not in v:
                continue
            if not PH_PATTERN.search(v):
                continue
            def rep(m):
                return str(flat.get(m.group(1), m.group(0)))
            nv = PH_PATTERN.sub(rep, v)
            if nv != v:
                c.value = nv
                n += 1
    return n


def _clone_row_style(ws, src_row: int, end_row: int, cols: dict) -> None:
    """把源行的样式复制到后续行（Excel 模板常见：只有一行示例格式）。"""
    from copy import copy
    if end_row <= src_row:
        return
    targets = list(cols.keys()) or [get_column_letter(i)
                                    for i in range(1, (ws.max_column or 1) + 1)]
    for r in range(src_row + 1, end_row + 1):
        for cl in targets:
            src = f"{cl}{src_row}"
            dst = f"{cl}{r}"
            try:
                if src in ws and dst in ws:
                    ws[dst]._style = copy(ws[src]._style)
            except Exception:
                continue
    # 行高
    try:
        h = ws.row_dimensions[src_row].height
        if h:
            for r in range(src_row + 1, end_row + 1):
                ws.row_dimensions[r].height = h
    except Exception:
        pass


def _clear_rows(ws, start: int, end: int, cols: dict) -> None:
    for r in range(start, end + 1):
        for cl in cols:
            try:
                ws[f"{cl}{r}"] = None
            except Exception:
                continue


def fill_docx(src: str, dst: str, quote_id: int,
              field_map: dict, item_map: dict) -> dict:
    """按映射填 Word 模板。

    两种填法：
    - 段落/表格里有 {{quote.quote_no}} 这类占位符 → 直接替换
    - 没有占位符 → 按段落序号定位填入（field_map 的 key 写成 "p:12" 或 "t:0:r:2:c:1"）
    """
    import docx
    ctx = build_context(quote_id)
    flat, items, cur = ctx["flat"], ctx["items"], ctx["currency"]

    d = docx.Document(src)
    replaced = {"placeholder": 0, "positional": 0, "item_rows": 0}

    # 1) 占位符替换（形如 {{quote.quote_no}} 或 {{ quote.quote_no }}）
    ph = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")

    def _sub_paragraph(p) -> int:
        n = 0
        text = "".join(r.text for r in p.runs)
        if not ph.search(text):
            return 0

        def rep(m):
            k = m.group(1)
            return str(flat.get(k, m.group(0)))
        new = ph.sub(rep, text)
        if new != text:
            # 保留首个 run 的格式，其余清空
            if p.runs:
                p.runs[0].text = new
                for r in p.runs[1:]:
                    r.text = ""
            else:
                p.add_run(new)
            n += 1
        return n

    for p in d.paragraphs:
        replaced["placeholder"] += _sub_paragraph(p)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    replaced["placeholder"] += _sub_paragraph(p)

    # 2) 定位填入（field_map: {"p:12": "quote.quote_no", "t:0.2.1": "customer.contact"}）
    paras = d.paragraphs
    tables = d.tables
    for pos, key in (field_map or {}).items():
        if not pos or key not in flat:
            continue
        val = flat[key]
        try:
            if pos.startswith("p:"):
                idx = int(pos[2:])
                if 0 <= idx < len(paras):
                    p = paras[idx]
                    if p.runs:
                        p.runs[0].text = str(val)
                        for r in p.runs[1:]:
                            r.text = ""
                    else:
                        p.add_run(str(val))
                    replaced["positional"] += 1
            elif pos.startswith("t:"):
                _, ti, ri, ci = pos.split(":")
                cell = tables[int(ti)].rows[int(ri)].cells[int(ci)]
                p = cell.paragraphs[0]
                if p.runs:
                    p.runs[0].text = str(val)
                    for r in p.runs[1:]:
                        r.text = ""
                else:
                    p.add_run(str(val))
                replaced["positional"] += 1
        except (ValueError, IndexError, KeyError):
            continue

    # 3) 明细行：定位表格后按行写入，行数不够时复制已有行补足
    im = item_map or {}
    t_idx = im.get("table_index")
    cols = im.get("cols") or {}
    if t_idx is not None and cols and 0 <= int(t_idx) < len(tables):
        tbl = tables[int(t_idx)]
        start = int(im.get("start_row", 1))
        need = start + len(items)          # 需要的最后一行下标
        _ensure_table_rows(tbl, need, im.get("style_row", start - 1) or 0)
        for i, it in enumerate(items):
            r = start + i
            if r >= len(tbl.rows):
                break
            for ci, key in cols.items():
                if not key:
                    continue
                ci = int(ci)
                if ci >= len(tbl.rows[r].cells):
                    continue
                cell = tbl.rows[r].cells[ci]
                txt = _fmt_item(it, key, cur)
                p = cell.paragraphs[0]
                if p.runs:
                    p.runs[0].text = txt
                    for rr in p.runs[1:]:
                        rr.text = ""
                else:
                    p.add_run(txt)
                replaced["item_rows"] += 1

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    d.save(dst)
    return {"output": dst, **replaced}


def _ensure_table_rows(table, need: int, style_row: int) -> None:
    """Word 表格行数不足时，复制 style_row 行的格式补足（保留字体边框）。"""
    from copy import deepcopy
    while len(table.rows) < need and len(table.rows) > 0:
        src = table.rows[style_row] if 0 <= style_row < len(table.rows) \
            else table.rows[-1]
        new_tr = deepcopy(src._tr)
        src._tr.addnext(new_tr)
        # 清空新行内容，只留格式
        for tc in new_tr.findall(".//{*}tc"):
            for p in tc.findall("./{*}p"):
                for r in p.findall("./{*}r"):
                    for t in r.findall("./{*}t"):
                        t.text = ""


def fill_quote(quote_id: int, template_id: int, out_dir: str) -> dict:
    """主入口：按客户模板生成报价单文件。"""
    db = get_conn()
    tpl = db.execute("SELECT * FROM client_templates WHERE id=?",
                     (template_id,)).fetchone()
    if not tpl:
        release_conn()
        raise ValueError("客户模板不存在")
    q = db.execute("SELECT quote_no FROM quotations WHERE id=?",
                   (quote_id,)).fetchone()
    release_conn()
    if not q:
        raise ValueError("报价单不存在")

    src = tpl["stored_path"]
    if not os.path.exists(src):
        raise ValueError("模板文件已丢失，请重新上传")

    field_map = jload(tpl["field_map"], {}) or {}
    item_map = jload(tpl["item_map"], {}) or {}
    ftype = tpl["file_type"]
    stem = f"{q['quote_no']}_{tpl['name']}"
    safe = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    os.makedirs(out_dir, exist_ok=True)

    if ftype in ("xlsx", "xlsm"):
        dst = os.path.join(out_dir, f"{safe}.{ftype}")
        r = fill_excel(src, dst, quote_id, field_map, item_map,
                       tpl["sheet_name"] or "")
    elif ftype == "docx":
        dst = os.path.join(out_dir, f"{safe}.docx")
        r = fill_docx(src, dst, quote_id, field_map, item_map)
    else:
        raise ValueError(f"不支持的模板类型：{ftype}")

    db = get_conn()
    db.execute("INSERT INTO filled_quotes(quotation_id,template_id,output_path,created_at)"
               " VALUES(?,?,?,?)", (quote_id, template_id, dst, now()))
    db.commit()
    release_conn()
    return r
