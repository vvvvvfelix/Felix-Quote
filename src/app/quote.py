"""报价单引擎 — 模板渲染 + HTML/PDF 输出。

PDF 采用自研的 HTML→PDF 轻量排版（ReportLab），完全离线。
模板使用 Jinja2，用户可在后台编辑，也可直接使用系统内置模板。
"""
import io
import math
import os
from datetime import datetime, timedelta

from jinja2 import ChainableUndefined, Template, Undefined

from .database import (BASE_DIR, effective_company, get_conn, jdump, jload,
                       now, release_conn)


class SafeUndefined(ChainableUndefined):
    """未定义变量渲染为空串，缺失字典键不影响整份报价单生成。"""

    def __str__(self):
        return ""

    def __bool__(self):
        return False

LEAD_TIME_DEFAULT = "30-45 days after receipt of deposit and artwork confirmation"

CERT_TEXT = {
    "CVC": "CVC Food Contact Safety Certificate",
    "SC": "China Industrial Product Production License (SC)",
    "ISO9001": "ISO 9001 Quality Management System",
    "CE": "CE Certificate",
    "FDA": "FDA Food Contact Compliance Declaration",
    "ETL": "ETL Listed Certification",
    "ETL_SANITATION": "ETL Sanitation Certified",
}
CERT_TEXT_CN = {
    "CVC": "CVC食品接触安全认证",
    "SC": "全国工业产品生产许可证",
    "ISO9001": "ISO9001质量管理体系认证",
    "CE": "CE认证",
    "FDA": "FDA食品接触材料符合性声明",
    "ETL": "ETL列名认证",
    "ETL_SANITATION": "ETL卫生认证",
}


def next_quote_no() -> str:
    db = get_conn()
    year = datetime.now().year
    prefix = f"WGQ{year}-"
    n = db.execute("SELECT COUNT(*) c FROM quotations WHERE quote_no LIKE ?", (prefix + "%",)).fetchone()["c"]
    return f"{prefix}{n + 1:04d}"


def get_company() -> dict:
    # 当前生效的公司信息（优先读管理员在 brand:company_cn/en 上的覆盖，否则回落默认）。
    # 与关于页共用同一事实来源，保证单据抬头与关于页署名一致（docs/09 §2.2 ⑧）。
    company = effective_company()
    company["cert_text"] = "、".join(
        CERT_TEXT.get(c.get("code", ""), c.get("code", "")) for c in company.get("certs", [])
    ) or "CVC, SC, ISO9001, CE"
    return company


def json_str_to_dict(v):
    import json
    try:
        data = json.loads(v) if isinstance(v, (str, bytes)) else v
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _num(value, default=0.0) -> float:
    """非正常状态闸门：NaN/inf/非法类型统一回落，金额链禁止传播非有限值。"""
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return out if math.isfinite(out) else default


def calc_totals(items: list[dict], freight: float = 0, insurance: float = 0,
                discount_rate: float = 0) -> dict:
    subtotal = 0.0
    packed = []
    for it in items:
        qty = _num(it.get("qty"))
        price = _num(it.get("price"))
        amount = round(qty * price, 2)
        it = dict(it)
        it["qty"] = qty
        it["price"] = price
        it["amount"] = amount
        if not it.get("carton") and isinstance(it.get("packing_size"), str):
            parts = [_num(p) for p in it["packing_size"].replace("x", "*").replace("X", "*").split("*") if p.strip().isdigit()]
            if len(parts) == 3:
                it["carton"] = round(parts[0] * parts[1] * parts[2] / 1e9, 3)
        certs = it.get("certs") or []
        if isinstance(certs, list):
            it["certs"] = ", ".join(certs)
        subtotal += amount
        packed.append(it)
    if not math.isfinite(subtotal):
        subtotal = 0.0
    subtotal = round(subtotal, 2)
    discount = round(subtotal * _num(discount_rate) / 100, 2)
    total = round(subtotal + _num(freight) + _num(insurance) - discount, 2)
    return {"subtotal": subtotal, "freight": _num(freight), "insurance": _num(insurance),
            "discount": discount, "total": total, "items": packed}


def render(quote_id: int) -> tuple[str, dict]:
    """渲染报价单 HTML。"""
    db = get_conn()
    q = db.execute("SELECT * FROM quotations WHERE id=?", (quote_id,)).fetchone()
    if not q:
        raise ValueError("报价单不存在")
    q = dict(q)
    cust = {}
    if q["customer_id"]:
        c = db.execute("SELECT * FROM customers WHERE id=?", (q["customer_id"],)).fetchone()
        if c:
            cust = dict(c)
    tpl = db.execute("SELECT * FROM templates WHERE key=?", (q["template_key"],)).fetchone()
    if not tpl:
        tpl = db.execute("SELECT * FROM templates WHERE lang='en' LIMIT 1").fetchone()
    tpl = dict(tpl) if tpl else {"body_html": "<html><body>{{ items|length }}</body></html>", "css": ""}

    company = get_company()
    company["cert_text"] = "、".join(company.get("certs") and CERT_TEXT.get(c["code"], c["code"])
                                   for c in company.get("certs", [])) or "CVC, SC, ISO9001, CE"

    items = jload(q["items_json"], [])
    lang = q["lang"] or "en"
    cert_map = CERT_TEXT_CN if lang == "cn" else CERT_TEXT
    for it in items:
        certs = it.get("certs") or []
        it["certs"] = ", ".join(cert_map.get(c, c) for c in certs) if isinstance(certs, list) else certs

    totals = calc_totals(items, q["freight"], q["insurance"], q["discount_rate"])
    items = totals["items"]

    date = datetime.strptime(q["created_at"], "%Y-%m-%d %H:%M:%S").date()
    ctx = {
        "quote": {
            "quote_no": q["quote_no"],
            "title": q["title"] or "Quotation",
            "date": date.strftime("%Y-%m-%d"),
            "valid_until": (date + timedelta(days=int(q["valid_days"] or 30))).strftime("%Y-%m-%d"),
            "currency": q["currency"] or "USD",
            "incoterm": q["incoterm"] or "FOB Guangzhou",
            "port": q["port"] or "Guangzhou / Shenzhen",
            "payment_terms": q["payment_terms"] or "30% T/T deposit, 70% balance before shipment",
            "valid_days": q["valid_days"] or 30,
            "delivery": (it_lead(items) or LEAD_TIME_DEFAULT),
            "remark": q["remark"] or "",
            "discount_rate": q["discount_rate"] or 0,
        },
        "customer": {
            "company_en": cust.get("company_en", ""),
            "company_cn": cust.get("company_en", ""),
            "contact": cust.get("contact", ""),
            "address": cust.get("address", ""),
            "country": cust.get("country", ""),
            "phone": cust.get("phone", ""),
            "email": cust.get("email", ""),
        },
        "company": company,
        "items": items,
        "totals": totals,
        "css": tpl.get("css") or "",
    }
    html = Template(tpl["body_html"], undefined=SafeUndefined).render(**ctx)
    return html, ctx


def it_lead(items: list[dict]) -> str:
    leads = [str(i.get("lead_time") or "").strip() for i in items if i.get("lead_time")]
    if not leads:
        return LEAD_TIME_DEFAULT
    joined = " / ".join(dict.fromkeys(leads))
    return f"{joined} after receipt of deposit"


# --------------------------------------------------------------------------
# PDF 输出（ReportLab 表格排版，跨页表头重复）
# --------------------------------------------------------------------------
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

GOLD = colors.HexColor("#B8860B")
DARK = colors.HexColor("#8A6508")
LIGHT = colors.HexColor("#F7F5EE")
GREY = colors.HexColor("#CCCCCC")

# --------------------------------------------------------------------------
# 字体：优先系统中文字体，确保报价单中的中文品名不乱码
# --------------------------------------------------------------------------
FONT_CANDIDATES = [
    ("msyh.ttc", "FelixCN", 0),      # 微软雅黑
    ("simhei.ttf", "FelixCN", 0),    # 黑体
    ("simsun.ttc", "FelixCN", 0),    # 宋体
    ("msyhbd.ttc", "FelixCNB", 0),   # 微软雅黑 Bold
    ("simhei.ttf", "FelixCNB", 0),
]

_REGISTERED = False


def _register_fonts():
    """注册中文字体，返回 (regular_name, bold_name)。"""
    global _REGISTERED
    if _REGISTERED:
        return "FelixCN", "FelixCNB"
    font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    reg = bold = None
    for fname, alias, _ in FONT_CANDIDATES:
        path = os.path.join(font_dir, fname)
        if not os.path.isfile(path):
            continue
        if fname == "msyhbd.ttc":
            if not bold:
                try:
                    pdfmetrics.registerFont(TTFont("FelixCNB", path, subfontIndex=0))
                    bold = "FelixCNB"
                except Exception:
                    pass
        elif reg is None:
            try:
                pdfmetrics.registerFont(TTFont("FelixCN", path, subfontIndex=0))
                reg = "FelixCN"
            except Exception:
                continue
    # 黑体同时充当粗体回退
    if bold is None and reg:
        try:
            pdfmetrics.registerFont(TTFont("FelixCNB", os.path.join(font_dir, "simhei.ttf"),
                                          subfontIndex=0))
            bold = "FelixCNB"
        except Exception:
            bold = reg
    _REGISTERED = True
    return (reg or "Helvetica"), (bold or reg or "Helvetica")


def _styles():
    fr, fb = _register_fonts()
    return {
        "brand": ParagraphStyle("brand", fontName=fb, fontSize=17, textColor=GOLD,
                                leading=20, spaceAfter=2),
        "cname": ParagraphStyle("cname", fontName=fb, fontSize=10.5, leading=13),
        "cmeta": ParagraphStyle("cmeta", fontName=fr, fontSize=7.4, leading=9.4,
                                textColor=colors.HexColor("#444444")),
        "qtitle": ParagraphStyle("qtitle", fontName=fb, fontSize=17, leading=20,
                                  alignment=TA_RIGHT, textColor=GOLD),
        "to": ParagraphStyle("to", fontName=fr, fontSize=9, leading=12),
        "th": ParagraphStyle("th", fontName=fb, fontSize=7, leading=8.4,
                             textColor=colors.white, alignment=1),
        "td": ParagraphStyle("td", fontName=fr, fontSize=7, leading=8.8),
        "tdr": ParagraphStyle("tdr", fontName=fr, fontSize=7, leading=8.8, alignment=TA_RIGHT),
        "tds": ParagraphStyle("tds", fontName=fr, fontSize=6, leading=7.4,
                              textColor=colors.HexColor("#555555")),
        "terms": ParagraphStyle("terms", fontName=fr, fontSize=6.8, leading=9,
                                textColor=colors.HexColor("#333333")),
        "sign": ParagraphStyle("sign", fontName=fb, fontSize=9, leading=12,
                               alignment=TA_RIGHT),
        "font": fr,
        "fontBold": fb,
    }


def html_to_pdf(quote_id: int) -> bytes:
    html, ctx = render(quote_id)
    S = _styles()
    q, c, comp, totals, items = (ctx["quote"], ctx["customer"], ctx["company"],
                                 ctx["totals"], ctx["items"])

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=10 * mm, rightMargin=10 * mm,
                          topMargin=10 * mm, bottomMargin=12 * mm,
                          title=f"Quotation {q['quote_no']}", author=comp.get("name_en", ""))
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(S["font"], 7)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(doc.leftMargin, 8 * mm, f"{comp.get('brand','')}  {comp.get('website','')}")
        canvas.drawRightString(A4[0] - doc.rightMargin, 8 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=footer)])

    story = []

    # --- Header ---
    left = [
        Paragraph(f"<b>{comp.get('brand','')}</b>", S["brand"]),
        Paragraph(f"<b>{comp.get('name_en','')}</b>", S["cname"]),
        Paragraph(f"{comp.get('address_cn','')}<br/>Tel: {comp.get('phone','')} &nbsp;|&nbsp; "
                  f"Hotline: {comp.get('hotline','')}<br/>Email: sales@example.com &nbsp;|&nbsp; "
                  f"Web: {comp.get('website','')}", S["cmeta"]),
    ]
    qmeta = Table(
        [["Quo. No.", Paragraph(f"<b>{q['quote_no']}</b>", S["td"])],
         ["Date", Paragraph(q["date"], S["td"])],
         ["Valid Until", Paragraph(q["valid_until"], S["td"])],
         ["Currency", Paragraph(q["currency"], S["td"])],
         ["Incoterm", Paragraph(q["incoterm"], S["td"])]],
        colWidths=[26 * mm, 42 * mm],
        style=TableStyle([
            ("FONT", (0, 0), (-1, -1), S["font"], 7.6),
            ("BACKGROUND", (0, 0), (0, -1), LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.4, GREY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]),
    )
    head = Table([[left, Paragraph("<b>QUOTATION</b>", S["qtitle"])], ["", qmeta]],
                 colWidths=[125 * mm, 43 * mm], style=TableStyle([
                     ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("LEFTPADDING", (0, 0), (-1, -1), 0),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                     ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
                     ("LINEBELOW", (0, -1), (-1, -1), 1.8, GOLD),
                 ]))
    story += [head, Spacer(1, 6)]

    # --- TO ---
    story.append(Paragraph(
        f"<b>TO:</b> {c.get('company_en','')}"
        + (f"<br/>{c.get('contact','')} {c.get('phone','')}" if c.get("contact") else "")
        + (f"<br/>{c.get('address','')}" if c.get("address") else "")
        + (f"<br/>{c.get('country','')}" if c.get("country") else ""), S["to"]))
    story.append(Spacer(1, 6))

    # --- Items ---
    headers = ["No.", "Model", "Description", "Material", "Dimension<br/>(mm)",
               "N.W.<br/>(kg)", "G.W.<br/>(kg)", "Carton<br/>(m&sup3;)", "Qty",
               f"Unit Price<br/>({q['currency']})", "Amount"]
    data = [[Paragraph(h, S["th"]) for h in headers]]
    for i, it in enumerate(items, 1):
        certs = it.get("certs") or ""
        desc = f"<b>{it.get('name_en','')}</b><br/>{Paragraph('<font size=6 color=\"#555\">%s</font>' % (it.get('name_cn','')), S['tds']).text}"
        extra = "".join([
            f"<br/>{it.get('spec_en','')}" if it.get("spec_en") else "",
            f"<br/>Cert: {certs}" if certs else "",
        ])
        data.append([
            Paragraph(str(i), S["td"]),
            Paragraph(f"{it.get('model','')}<br/><font size=6>{it.get('sku','')}</font>", S["td"]),
            Paragraph(desc + extra, S["td"]),
            Paragraph(str(it.get("material") or ""), S["td"]),
            Paragraph(str(it.get("dimension") or ""), S["td"]),
            Paragraph(str(it.get("net_weight") or ""), S["tdr"]),
            Paragraph(str(it.get("gross_weight") or ""), S["tdr"]),
            Paragraph(str(it.get("carton") or ""), S["tdr"]),
            Paragraph(str(it.get("qty")), S["tdr"]),
            Paragraph(f"{it['price']:.2f}", S["tdr"]),
            Paragraph(f"<b>{it['amount']:.2f}</b>", S["tdr"]),
        ])
    if not items:
        data.append([Paragraph("No items", S["td"])] + [""] * 10)

    colw = [8, 17, 58, 17, 16, 11, 11, 12, 9, 16, 18]
    colw = [w * mm / 1.0 for w in [w for w in [8, 17, 58, 17, 16, 11, 11, 12, 9, 16, 18]]]
    scale = (A4[0] - doc.leftMargin - doc.rightMargin) / sum(colw)
    colw = [w * scale for w in colw]
    tbl = Table(data, colWidths=colw, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("GRID", (0, 0), (-1, -1), 0.4, GREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (5, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FBFAF6")))
    tbl.setStyle(TableStyle(style))
    story += [tbl, Spacer(1, 7)]

    # --- Certs + Totals ---
    certbox = Paragraph(
        f"<b>Certification:</b> {comp.get('cert_text','')}<br/>"
        f"<b>Packing:</b> Export standard wooden carton / bubble wrap, palletized.<br/>"
        f"<b>Warranty:</b> 12 months against manufacturing defects (wear parts excluded).", S["terms"])
    trows = [["Subtotal", Paragraph(f"{totals['subtotal']:.2f} {q['currency']}", S["tdr"])]]
    if totals["freight"]:
        trows.append(["Freight", Paragraph(f"{totals['freight']:.2f}", S["tdr"])])
    if totals["insurance"]:
        trows.append(["Insurance", Paragraph(f"{totals['insurance']:.2f}", S["tdr"])])
    if totals["discount"]:
        trows.append([f"Discount ({q['discount_rate']:.1f}%)",
                      Paragraph(f"-{totals['discount']:.2f}", S["tdr"])])
    trows.append([Paragraph("<b>TOTAL</b>", S["td"]),
                  Paragraph(f"<b>{totals['total']:.2f} {q['currency']}</b>", S["tdr"])])
    ttable = Table(trows, colWidths=[36 * mm, 34 * mm])
    tstyle = [("GRID", (0, 0), (-1, -1), 0.4, GREY), ("BACKGROUND", (0, 0), (0, -2), LIGHT),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3),
              ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
              ("BACKGROUND", (0, -1), (-1, -1), GOLD), ("TEXTCOLOR", (0, -1), (-1, -1), colors.white)]
    ttable.setStyle(TableStyle(tstyle))
    story.append(Table([[certbox, ""], ["", ttable]],
                       colWidths=[100 * mm, 70 * mm], style=TableStyle([
                           ("VALIGN", (0, 0), (0, 0), "TOP"),
                           ("VALIGN", (1, 1), (1, 1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                       ])))
    story.append(Spacer(1, 8))

    # --- Terms ---
    terms = [
        f"Price basis: <b>{q['incoterm']}</b>, port of loading: <b>{q['port']}</b>. "
        "Freight / insurance quoted separately unless otherwise noted.",
        f"Payment: {q['payment_terms']}.",
        f"Delivery: {q['delivery']}.",
        f"Quotation validity: {q['valid_days']} days from the date above.",
        "All machines are brand new, never used, packed in export standard cartons on wooden pallets.",
        "Any change in specification, capacity or optional accessories will affect the price accordingly.",
        "Bank details will never be changed by email. Please confirm banking details by phone "
        "if you receive any suspicious request.",
    ]
    if q.get("remark"):
        terms.insert(0, f"Remark: {q['remark']}")
    story.append(Paragraph("<b>Terms &amp; Conditions</b><br/>" +
                           "<br/>".join(f"{i}. {t}" for i, t in enumerate(terms, 1)), S["terms"]))
    story.append(Spacer(1, 16))
    story.append(Paragraph(f"<b>{comp.get('name_en','')}</b><br/>"
                           '<font size="7" color="#666">Authorized Signature / Company Stamp</font>',
                           S["sign"]))

    doc.build(story)  # footer 已通过 PageTemplate.onPage 注册
    return buf.getvalue()