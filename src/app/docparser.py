"""本地文档解析 — PDF / TXT / DOCX / XLSX / CSV 全部在本地解析，0 Token。

目标：从使用者提供的资料中抽取产品字段（型号/规格/材质/尺寸/净重毛重/证书/价格），
生成候选产品记录供人工确认后入库。
"""
import csv
import io
import json
import os
import re
import zipfile

from .categories_params import build_alias_map, get_family, get_params as _get_params
from .database import get_conn, jdump, now, release_conn

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

ALLOWED = {".pdf", ".txt", ".md", ".csv", ".docx", ".xlsx", ".xls", ".json"}

# 字段别名映射（中英文）
FIELD_ALIASES = {
    "sku": ["sku", "货号", "料号", "编号", "编码", "item no", "item no.", "model no"],
    "model": ["model", "型号", "规格型号", "model no", "型号规格"],
    "name_cn": ["名称", "品名", "中文名称", "产品名称", "name", "description", "品名规格"],
    "name_en": ["english name", "英文名称", "name(eng)", "en name", "英文品名"],
    "material": ["材质", "材料", "material", "material&", "主体材质"],
    "power": ["功率", "电压", "power", "voltage", "电源", "功率/电压", "kw"],
    "capacity": ["容积", "容量", "层数", "capacity", "层/盘", "盘数", "trays"],
    "dimension": ["外形尺寸", "尺寸", "dimension", "size", "外型尺寸", "长*宽*高", "l*w*h"],
    "packing_size": ["包装尺寸", "packing size", "包装外形", "carton size"],
    "net_weight": ["净重", "nw", "net weight", "net wt"],
    "gross_weight": ["毛重", "gw", "gross weight", "gross wt"],
    "price_cny": ["价格", "单价", "出厂价", "人民币价", "price", "unit price", "参考价", "出厂价格", "rmb"],
    "price_usd": ["usd", "美元价", "出口价", "fob price", "usd price", "fob价格", "fob usd", "美元"],
    "moq": ["moq", "最小起订量", "起订量"],
    "lead_time": ["交期", "交货期", "lead time", "delivery"],
    "hs_code": ["hs code", "海关编码", "hs编码", "hs"],
    "certs": ["证书", "认证", "certificates", "certificate", "certs", "cert"],
    "desc_en": ["英文描述", "english description", "en description"],
    "image_path": ["图片", "image", "photo", "image path", "图片路径"],
    "keywords": ["关键词", "keywords", "keyword", "标签"],
}

NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _norm(header: str) -> str:
    return re.sub(r"[\s\u3000]+", " ", str(header or "").strip().lower().replace("：", ":"))


def _match_key(header: str, extra_alias: dict | None = None):
    h = _norm(header)
    # 品类专属别名优先（如「缸容量」只在搅拌机类目中存在）
    if extra_alias:
        if h in extra_alias:
            return extra_alias[h]
        for a, f in extra_alias.items():
            if len(a) >= 2 and (a in h or h in a):
                return f
    # 货币提示优先判断，避免「FOB价格USD」被误判为人民币价
    if any(k in h for k in ("usd", "美元", "$", "fob")):
        for field in ("price_usd",):
            for a in FIELD_ALIASES[field]:
                if h == a or a in h:
                    return field
    for field, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if h == a or a in h:
                return field
    return None


def _to_float(v):
    if v is None:
        return None
    m = NUM_RE.search(str(v).replace(",", ""))
    return float(m.group(1)) if m else None


def _to_int(v):
    f = _to_float(v)
    return int(f) if f is not None else None


def _extract_text(path: str) -> tuple[str, str]:
    """返回 (全文文本, doc_type)"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        if PdfReader is None:
            return "", "pdf"
        reader = PdfReader(path)
        pages = [(p.extract_text() or "") for p in reader.pages]
        return "\n".join(pages), "pdf"
    if ext in (".txt", ".md", ".json"):
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read(), ext.lstrip(".")
            except (UnicodeDecodeError, LookupError):
                continue
        return "", ext.lstrip(".")
    if ext == ".docx":
        try:
            import docx  # python-docx

            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    parts.append("\t".join(c.text.strip() for c in row.cells))
            return "\n".join(parts), "docx"
        except Exception:
            return _docx_xml_text(path), "docx"
    if ext == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True)
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                parts.append("\t".join("" if c is None else str(c) for c in row))
        return "\n".join(parts), "xlsx"
    if ext == ".csv":
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    return f.read(), "csv"
            except UnicodeDecodeError:
                continue
        return "", "csv"
    return "", ext.lstrip(".")


def _docx_xml_text(path: str) -> str:
    """纯 zip/XML 兜底解析 docx。"""
    out = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.startswith("word/") and name.endswith(".xml"):
                    xml = z.read(name).decode("utf-8", "ignore")
                    out.extend(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    except Exception:
        pass
    return " ".join(out)


def _rows_from_tabular(text: str, extra_alias: dict | None = None) -> list[dict]:
    """把 TSV/CSV 文本转成行字典（首行为表头）。

    仅当首行有 >= 2 个可识别字段、且数据行确实存在分隔符时才视为表格，
    避免把「Key: Value」形态的规格段落误判成单列表格。
    """
    sample = text[:4096]
    delim = "\t" if sample.count("\t") >= sample.count(",") else ","
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = [r for r in reader if any((c or "").strip() for c in r)]
    except Exception:
        return []
    if len(rows) < 2:
        return []
    header, body = rows[0], rows[1:]
    mapping = [_match_key(h, extra_alias) for h in header]
    matched = [m for m in mapping if m]
    # 真实表格至少要有两个可识别列头，且数据行保持相同列数
    if len(matched) < 2:
        return []
    if not any(len(r) >= len(matched) for r in body):
        return []
    out = []
    for r in body:
        item = {}
        for i, cell in enumerate(r):
            if i < len(mapping) and mapping[i]:
                item.setdefault(mapping[i], (cell or "").strip())
        if item:
            out.append(item)
    return out


def _rows_from_keyvalue(text: str, extra_alias: dict | None = None) -> list[dict]:
    """从 key: value 形态的文本（如 PDF 提取的规格段落）构造单条记录。"""
    item = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        for sep in (":", "：", "\t"):
            if sep in line:
                k, v = line.split(sep, 1)
                field = _match_key(k, extra_alias)
                if field and v.strip():
                    item.setdefault(field, v.strip())
                break
    return [item] if item else []


def parse_document(doc_id: int, category_code: str | None = None) -> dict:
    """解析已入库文档，生成候选产品。

    category_code 由调用方指定；未指定时从文本自动判定，
    以便按品类套用专属字段别名（同行不同品类的表头写法差异很大）。
    """
    db = get_conn()
    row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise ValueError("文档不存在")

    text, dtype = _extract_text(row["stored_path"])

    cat = category_code
    if not cat:
        from .ocr import detect_category
        cat = detect_category(text)
    extra_alias = build_alias_map(cat)

    candidates: list[dict] = []
    if text.strip():
        candidates = _rows_from_tabular(text, extra_alias)
        if not candidates:
            candidates = _rows_from_keyvalue(text, extra_alias)

    normalized = []
    tpl_keys = {p["key"] for p in _get_params(cat)["params"]}
    for c in candidates:
        item = {
            "sku": (c.get("sku") or c.get("model") or "").strip(),
            "model": (c.get("model") or "").strip(),
            "name_cn": (c.get("name_cn") or "").strip(),
            "name_en": (c.get("name_en") or c.get("name_cn") or "").strip(),
            "category_code": cat,
            "material": (c.get("material") or "").strip(),
            "power": (c.get("power") or "").strip(),
            "capacity": (c.get("capacity") or "").strip(),
            "dimension": (c.get("dimension") or "").strip(),
            "packing_size": (c.get("packing_size") or "").strip(),
            "net_weight": _to_float(c.get("net_weight")),
            "gross_weight": _to_float(c.get("gross_weight")),
            "price_cny": _to_float(c.get("price_cny")),
            "price_usd": _to_float(c.get("price_usd")),
            "moq": _to_int(c.get("moq")) or 1,
            "lead_time": (c.get("lead_time") or "").strip(),
            "hs_code": (c.get("hs_code") or "").strip(),
            "certs": [x for x in re.split(r"[,，;；/、\s]+", c.get("certs", "") or "") if x],
            "desc_en": (c.get("desc_en") or "").strip(),
            "keywords": (c.get("keywords") or "").strip(),
            "spec": (c.get("spec") if isinstance(c.get("spec"), str) else "") or
                    " ".join(filter(None, [c.get("capacity"), c.get("power"), c.get("material")])),
        }
        item["spec_en"] = " ".join(filter(None, [item["capacity"], item["power"], item["material"]]))
        # 品类专属参数（如搅拌机的缸容量/调速方式，冷藏柜的温度范围/制冷剂）
        common_keys = {"sku", "model", "name_cn", "name_en", "category_code", "material",
                       "power", "capacity", "dimension", "packing_size", "net_weight",
                       "gross_weight", "price_cny", "price_usd", "moq", "lead_time",
                       "hs_code", "certs", "desc_en", "keywords", "spec", "spec_en"}
        item["specs"] = {k: v for k, v in c.items()
                         if k in tpl_keys and k not in common_keys and v not in (None, "")}
        if not (item["name_cn"] or item["name_en"] or item["model"]):
            continue
        normalized.append(item)

    status = "parsed" if normalized else ("text_only" if text.strip() else "failed")
    message = (f"识别到 {len(normalized)} 条产品记录" if normalized
               else ("已提取文本但未匹配到产品表头/字段，可在文档详情中手工录入" if text.strip()
                     else "未能提取内容"))

    db.execute("UPDATE documents SET parsed_text=?, parsed_json=?, status=?, message=? WHERE id=?",
               (text[:200000], jdump(normalized), status, message, doc_id))
    db.commit()
    release_conn()
    return {"doc_id": doc_id, "status": status, "message": message,
            "count": len(normalized), "candidates": normalized, "text_len": len(text),
            "category_code": cat}


def commit_candidates(doc_id: int, items: list[dict], username: str = "system") -> dict:
    """把候选产品正式入库（跳过已存在 SKU）。"""
    db = get_conn()
    ts = now()
    inserted, skipped = 0, 0
    for it in items:
        sku = (it.get("sku") or it.get("model") or "").strip()
        if not sku:
            skipped += 1
            continue
        if db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone():
            skipped += 1
            continue
        ncn = (it.get("name_cn") or it.get("name_en") or sku).strip()
        nen = (it.get("name_en") or it.get("name_cn") or sku).strip()
        db.execute(
            """INSERT INTO products
               (sku,name_cn,name_en,category_code,supplier_code,brand,model,spec,spec_en,material,
                power,capacity,dimension,packing_size,net_weight,gross_weight,price_cny,price_usd,
                moq,lead_time,hs_code,certs,image_path,images,desc_en,keywords,specs,source,doc_id,
                is_active,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (sku, ncn, nen, it.get("category_code") or "OTHER", it.get("supplier_code") or "SELF",
             it.get("brand") or "Felixv", (it.get("model") or sku).strip(), it.get("spec") or "",
             it.get("spec_en") or "", it.get("material") or "", it.get("power") or "",
             it.get("capacity") or "", it.get("dimension") or "", it.get("packing_size") or "",
             it.get("net_weight"), it.get("gross_weight"), it.get("price_cny"), it.get("price_usd"),
             it.get("moq") or 1, it.get("lead_time") or "", it.get("hs_code") or "",
             jdump(it.get("certs") or []), it.get("image_path") or "", jdump([]),
             it.get("desc_en") or "", it.get("keywords") or "",
             jdump(it.get("specs") or {}), f"文档导入:{username}", doc_id, ts),
        )
        inserted += 1
    db.commit()
    release_conn()
    return {"inserted": inserted, "skipped": skipped}