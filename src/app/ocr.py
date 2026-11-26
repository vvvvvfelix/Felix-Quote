"""图片 OCR 解析 — 截图/照片 → 文本 → 产品字段。

完全本地推理（RapidOCR / PP-OCR ONNX），0 Token、0 联网。
支持用户直接丢微信截图、产品照片、纸质报价单照片。

与 docparser 的区别：
  docparser  → 结构化表格（Excel/CSV/TXT/DOCX）
  ocr        → 非结构化图片（截图/照片/PDF扫描页）
"""
import os
import re

from .categories_params import build_alias_map, get_family

# --------------------------------------------------------------------------
# OCR 引擎（惰性单例，首次调用约 1.5s 加载模型）
# --------------------------------------------------------------------------
_ENGINE = None
_ENGINE_ERR = None

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}
# 便携版为压缩体积移除了 AVIF 解码器，遇到这类格式给出明确指引
NO_DECODER_EXT = {".avif", ".heic", ".heif"}


def get_engine():
    global _ENGINE, _ENGINE_ERR
    if _ENGINE is not None or _ENGINE_ERR is not None:
        return _ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    except Exception as exc:  # pragma: no cover - 环境缺失时降级
        _ENGINE_ERR = str(exc)
        _ENGINE = None
    return _ENGINE


def engine_available() -> bool:
    return get_engine() is not None


# --------------------------------------------------------------------------
# 文本行合并
# --------------------------------------------------------------------------
def _merge_lines(ocr_result) -> list[dict]:
    """把 OCR 结果按行归组，返回 [{'y','x','text','conf'}]。"""
    if not ocr_result:
        return []
    boxes = []
    for item in ocr_result:
        box, txt, conf = item[0], item[1], float(item[2])
        x = min(p[0] for p in box)
        y = min(p[1] for p in box)
        h = max(p[1] for p in box) - y
        boxes.append({"x": x, "y": y, "h": h, "text": txt.strip(), "conf": conf})
    if not boxes:
        return []
    boxes.sort(key=lambda b: b["y"])

    lines: list[dict] = []
    for b in boxes:
        placed = False
        for ln in lines:
            # 纵向重叠超过 60% 视为同一行
            overlap = min(ln["y"] + ln["h"], b["y"] + b["h"]) - max(ln["y"], b["y"])
            if overlap > 0.6 * min(ln["h"], b["h"]):
                ln["parts"].append(b)
                ln["x"] = min(ln["x"], b["x"])
                ln["h"] = max(ln["h"], b["h"])
                placed = True
                break
        if not placed:
            lines.append({"y": b["y"], "x": b["x"], "h": b["h"],
                          "parts": [b], "text": "", "conf": b["conf"]})
    for ln in lines:
        parts = sorted(ln["parts"], key=lambda p: p["x"])
        ln["text"] = " ".join(p["text"] for p in parts).strip()
        ln["conf"] = min(p["conf"] for p in parts)
    return lines


# --------------------------------------------------------------------------
# 字段抽取
# --------------------------------------------------------------------------
_SEP = r"[:：=＝]"
_NUM_UNIT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(mm|cm|m|kg|g|L|l|kW|KW|kw|W|℃|m3|m³)")


def _clean_key(k: str) -> str:
    k = re.sub(r"[:：=＝\s]+$", "", k or "").strip()
    return k


def _match_field(key: str, alias_map: dict) -> str | None:
    k = _clean_key(key).lower()
    if not k:
        return None
    if k in alias_map:
        return alias_map[k]
    # 子串匹配（去掉括号、单位）
    k2 = re.sub(r"[（(].*?[)）]", "", k).strip()
    if k2 in alias_map:
        return alias_map[k2]
    k3 = re.sub(r"\s*\(.*?\)\s*", "", k2)
    for a, f in alias_map.items():
        if len(a) >= 2 and (a in k or k in a):
            return f
    return None


def _to_float(v):
    if v is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(v).replace(",", ""))
    return float(m.group(1)) if m else None


def extract_fields(text: str, category_code: str = None) -> dict:
    """从纯文本中按品类别名抽取字段。"""
    alias = build_alias_map(category_code)
    out: dict = {}
    certs: list[str] = []

    for raw in text.split("\n"):
        line = raw.strip()
        if not line or len(line) > 200:
            continue
        # 支持 "键: 值" 与 "键 值"（制表符/多空格）
        m = re.split(_SEP, line, maxsplit=1)
        if len(m) == 2:
            key, val = m[0], m[1]
        else:
            m2 = re.split(r"\t|\s{2,}", line, maxsplit=1)
            if len(m2) == 2:
                key, val = m2[0], m2[1]
            else:
                continue
        val = val.strip()
        if not val:
            continue
        field = _match_field(key, alias)
        if not field:
            continue
        if field == "certs":
            certs.extend(x for x in re.split(r"[,，;；/、\s]+", val) if x)
        elif field in ("net_weight", "gross_weight", "price_cny", "price_usd", "moq"):
            out[field] = _to_float(val)
        elif field == "sku":
            out.setdefault("sku", val)
        else:
            out.setdefault(field, val)

    if certs:
        seen, uniq = set(), []
        for c in certs:
            k = c.upper().strip()
            if k and k not in seen:
                seen.add(k)
                uniq.append(k)
        out["certs"] = uniq
    return out


def detect_category(text: str, default: str = "OTHER") -> str:
    """从文本猜测产品品类（返回系统标准分类码）。"""
    rules = [
        ("MIXER", ["搅拌机", "和面机", "打蛋机", "起酥机", "压面机", "mixer", "blender", "spiral"]),
        ("DISPLAY", ["展示柜", "保温柜", "陈列柜", "food warmer", "showcase"]),
        ("FREEZER", ["冷藏", "冷冻", "冷柜", "冰箱", "冷藏工作台",
                     "refrigerator", "freezer", "chiller"]),
        ("GLASSWASHER", ["洗杯机", "glass washer", "杯洗"]),
        ("DISHWASHER", ["洗碗机", "dishwasher", "洗碗"]),
        ("DISINFECTION", ["消毒柜", "消毒烘干", "disinfection"]),
        ("COOKER", ["炒炉", "炒锅", "wok range", "电磁炉"]),
        ("STOVE", ["汤炉", "灶具", "炉灶", "卤水炉", "soup stove", "stove", "cooker"]),
        ("FRYER", ["炸炉", "油炸", "fryer"]),
        ("SLICER", ["切肉机", "切丝机", "meat slicer", "slicer"]),
        ("CUTTER", ["绞肉机", "斩切机", "meat grinder", "cutter"]),
        ("SAUSAGE", ["灌肠机", "sausage"]),
        ("VEGETABLE", ["切菜机", "切配机", "vegetable cutter"]),
        # 烤箱类细分
        ("OVEN-TUNNEL", ["隧道炉", "tunnel oven"]),
        ("OVEN-PIZZA", ["披萨炉", "披萨烤箱", "pizza oven"]),
        ("OVEN-CHICKEN", ["烤鸡炉", "chicken oven", "roast chicken"]),
        ("OVEN-COMBI", ["蒸烤箱", "万能烤箱", "combi oven", "steam oven"]),
        ("OVEN-ROTARY", ["旋转炉", "rotary oven"]),
        ("PROOFER", ["醒发箱", "发酵箱", "proofer", "proofing"]),
        ("OVEN-DECK", ["层炉", "层烤箱", "deck oven", "烤箱", "烤炉", "烘炉", "oven", "bakery"]),
    ]
    low = (text or "").lower()
    for code, keys in rules:
        if any(k in low for k in keys):
            return code
    return default


def ocr_image(path: str, category_code: str = None) -> dict:
    """对图片执行 OCR 并抽取产品字段。"""
    engine = get_engine()
    if engine is None:
        return {"ok": False, "error": f"OCR 引擎不可用：{_ENGINE_ERR}",
                "text": "", "fields": {}, "lines": []}

    try:
        result, _elapse = engine(path)
    except Exception as exc:
        return {"ok": False, "error": f"OCR 执行失败：{exc}",
                "text": "", "fields": {}, "lines": []}

    lines = _merge_lines(result)
    text = "\n".join(ln["text"] for ln in lines)
    cat = category_code or detect_category(text)
    fields = extract_fields(text, cat)

    # 补全：从整段文本再扫一次（处理换行拆断的情况）
    if not fields.get("name_cn"):
        for ln in lines:
            f2 = extract_fields(ln["text"], cat)
            for k, v in f2.items():
                fields.setdefault(k, v)

    # SKU 缺省时用型号回落
    if not fields.get("sku") and fields.get("model"):
        fields["sku"] = fields["model"]

    avg_conf = (sum(ln["conf"] for ln in lines) / len(lines)) if lines else 0
    return {
        "ok": True,
        "text": text,
        "lines": [{"y": int(ln["y"]), "x": int(ln["x"]), "text": ln["text"],
                   "conf": round(ln["conf"], 3)} for ln in lines],
        "fields": fields,
        "category_code": cat,
        "category_family": get_family(cat),
        "line_count": len(lines),
        "avg_conf": round(avg_conf, 3),
    }


def build_candidate(ocr: dict) -> dict:
    """把 OCR 结果整理成候选产品记录（与文档解析产物同构）。"""
    f = dict(ocr.get("fields") or {})
    name = f.get("name_cn") or f.get("model") or f.get("sku") or ""
    if not name:
        return {}
    name_en = f.get("name_en") or ""
    spec_parts = [f.get("capacity"), f.get("power"), f.get("material")]
    spec = " ".join(x for x in spec_parts if x)
    # 未落在模板字段上的额外信息写入规格
    known = {"capacity", "power", "material", "name_cn", "name_en", "model", "sku",
             "net_weight", "gross_weight", "price_cny", "price_usd", "moq",
             "lead_time", "hs_code", "certs", "dimension", "packing_size"}
    extras = [f"{k}: {v}" for k, v in f.items() if k not in known and v]
    if extras:
        spec = (spec + " | " if spec else "") + "; ".join(extras)

    return {
        "sku": f.get("sku") or f.get("model") or "",
        "model": f.get("model") or "",
        "name_cn": name,
        "name_en": name_en,
        "category_code": ocr.get("category_code") or "OTHER",
        "material": f.get("material") or "",
        "power": f.get("power") or "",
        "capacity": f.get("capacity") or "",
        "dimension": f.get("dimension") or "",
        "packing_size": f.get("packing_size") or "",
        "net_weight": f.get("net_weight"),
        "gross_weight": f.get("gross_weight"),
        "price_cny": f.get("price_cny"),
        "price_usd": f.get("price_usd"),
        "moq": f.get("moq") or 1,
        "lead_time": f.get("lead_time") or "",
        "hs_code": f.get("hs_code") or "",
        "certs": f.get("certs") or [],
        "spec": spec,
        "spec_en": " ".join(x for x in spec_parts if x),
        "image_path": f.get("image_path") or "",
        "keywords": f.get("keywords") or "OCR导入",
    }