"""品类参数模板 — 同行不同品类的规格参数体系。

核心思想：不同品类（如燃气烤箱 vs 搅拌机 vs 冰箱）的关键参数完全不同，
不能用一套「规格/材质/功率」字段糊弄。这里为每个品类定义专属参数模板，
前端表单、文档解析、报价单输出都按模板动态渲染。

每个参数字段定义：
  key         数据库/接口字段名
  label_cn    中文标签
  label_en    英文标签（外贸报价单用）
  type        text | number | select | textarea
  unit        单位（kg / mm / kW / L / ℃ …）
  options     select 类型的候选值
  required    是否必填
  export      是否导出到报价单
  aliases     文档解析时的表头别名（OCR/Excel 通用）
"""

# --------------------------------------------------------------------------
# 参数模板库
# --------------------------------------------------------------------------
PARAM_TEMPLATES = {
    "OVEN": {
        "label": "烘焙烤箱类",
        "params": [
            {"key": "capacity", "label_cn": "容积/层数", "label_en": "Capacity", "type": "text",
             "unit": "盘", "required": True, "export": True,
             "aliases": ["层数", "盘数", "容积", "capacity", "层/盘", "trays", "decks", "层"]},
            {"key": "power", "label_cn": "功率/电压", "label_en": "Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["功率", "电压", "power", "voltage", "kw", "电源", "功率/电压", "能耗"]},
            {"key": "temp_range", "label_cn": "控温范围", "label_en": "Temp. Range", "type": "text",
             "unit": "℃", "required": False, "export": True,
             "aliases": ["温度范围", "控温", "调温", "temperature", "temp range", "工作温度", "温度"]},
            {"key": "heating", "label_cn": "加热方式", "label_en": "Heating", "type": "select",
             "options": ["电热", "燃气", "蒸汽", "微波", "热风循环"], "required": True, "export": True,
             "aliases": ["加热方式", "能源", "燃料", "heating", "fuel", "能源类型", "供电"]},
            {"key": "material", "label_cn": "内胆材质", "label_en": "Chamber Material", "type": "select",
             "options": ["SUS304 不锈钢", "SUS430 不锈钢", "镀锌板", "冷轧板喷漆"], "required": True, "export": True,
             "aliases": ["材质", "内胆", "material", "主体材质", "内胆材质", "板材"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "外型尺寸", "dimension", "size", "长宽高", "l*w*h"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw", "net weight", "净重(kg)"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw", "gross weight", "毛重(kg)"]},
            {"key": "door_glass", "label_cn": "炉门玻璃", "label_en": "Door Glass", "type": "text",
             "required": False, "export": True,
             "aliases": ["炉门", "玻璃", "门玻璃", "door", "glass"]},
            {"key": "control", "label_cn": "控制系统", "label_en": "Control", "type": "text",
             "required": False, "export": True,
             "aliases": ["控制系统", "控制方式", "面板", "control", "温控", "操作面板"]},
        ],
    },
    "MIXER": {
        "label": "搅拌设备类",
        "params": [
            {"key": "capacity", "label_cn": "缸容量", "label_en": "Bowl Capacity", "type": "number",
             "unit": "L", "required": True, "export": True,
             "aliases": ["容量", "缸容量", "容积", "capacity", "桶容量", "bowl"]},
            {"key": "dough_capacity", "label_cn": "一次搅拌面团量", "label_en": "Dough Capacity", "type": "number",
             "unit": "kg", "required": False, "export": True,
             "aliases": ["面团量", "搅拌量", "一次搅拌", "dough capacity", "kg/次"]},
            {"key": "motor", "label_cn": "电机功率", "label_en": "Motor Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["电机", "功率", "motor", "motor power", "电机功率", "kw"]},
            {"key": "speed", "label_cn": "调速方式", "label_en": "Speed Control", "type": "select",
             "options": ["定速", "两档调速", "变频调速"], "required": True, "export": True,
             "aliases": ["调速", "变速", "档位", "speed", "调速方式", "转速"]},
            {"key": "agitator", "label_cn": "搅拌型式", "label_en": "Agitator Type", "type": "select",
             "options": ["螺旋搅拌", "行星搅拌", "打蛋搅拌", "液压搅拌"], "required": True, "export": True,
             "aliases": ["搅拌方式", "型式", "agitator", "搅拌型式", "结构"]},
            {"key": "bowl_material", "label_cn": "缸体材质", "label_en": "Bowl Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["缸体", "缸材质", "bowl material", "材质"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw", "net weight"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw", "gross weight"]},
            {"key": "voltage", "label_cn": "电压", "label_en": "Voltage", "type": "text",
             "required": False, "export": True,
             "aliases": ["电压", "voltage", "电源"]},
        ],
    },
    "FREEZER": {
        "label": "冷冻冷藏类",
        "params": [
            {"key": "volume", "label_cn": "有效容积", "label_en": "Volume", "type": "number",
             "unit": "L", "required": True, "export": True,
             "aliases": ["容积", "容量", "volume", "有效容积", "升"]},
            {"key": "temp_range", "label_cn": "温度范围", "label_en": "Temp. Range", "type": "text",
             "unit": "℃", "required": True, "export": True,
             "aliases": ["温度范围", "控温", "temperature", "温度", "冷冻温度"]},
            {"key": "compressor", "label_cn": "压缩机", "label_en": "Compressor", "type": "text",
             "required": True, "export": True,
             "aliases": ["压缩机", "compressor", "机组", "品牌压缩机"]},
            {"key": "refrigerant", "label_cn": "制冷剂", "label_en": "Refrigerant", "type": "text",
             "required": False, "export": True,
             "aliases": ["制冷剂", "冷媒", "refrigerant", "氟利昂"]},
            {"key": "power", "label_cn": "功率", "label_en": "Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["功率", "power", "耗电量", "功率/电压"]},
            {"key": "material", "label_cn": "内胆/台面材质", "label_en": "Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["材质", "内胆", "material", "台面"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "dimension", "size"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw"]},
            {"key": "defrost", "label_cn": "化霜方式", "label_en": "Defrost", "type": "select",
             "options": ["手动化霜", "自动化霜", "无霜"], "required": False, "export": True,
             "aliases": ["化霜", "defrost", "除霜"]},
        ],
    },
    "WASH": {
        "label": "清洗设备类",
        "params": [
            {"key": "capacity", "label_cn": "洗涤容量", "label_en": "Wash Capacity", "type": "number",
             "unit": "kg/次", "required": True, "export": True,
             "aliases": ["容量", "洗涤量", "capacity", "每次洗涤", "筐数"]},
            {"key": "cycle", "label_cn": "循环次数", "label_en": "Cycle", "type": "text",
             "unit": "次/小时", "required": False, "export": True,
             "aliases": ["循环", "cycle", "洗涤次数", "循环次数"]},
            {"key": "water_temp", "label_cn": "洗涤水温", "label_en": "Water Temp", "type": "text",
             "unit": "℃", "required": True, "export": True,
             "aliases": ["水温", "温度", "water temperature", "洗涤温度"]},
            {"key": "heater", "label_cn": "加热方式", "label_en": "Heater", "type": "text",
             "required": False, "export": True,
             "aliases": ["加热", "heater", "加热方式", "电热"]},
            {"key": "power", "label_cn": "功率/电压", "label_en": "Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["功率", "电压", "power"]},
            {"key": "material", "label_cn": "材质", "label_en": "Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["材质", "material"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "dimension"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw"]},
            {"key": "rack_type", "label_cn": "碗架类型", "label_en": "Rack Type", "type": "text",
             "required": False, "export": True,
             "aliases": ["碗架", "篮架", "rack", "架型"]},
        ],
    },
    "FURNACE": {
        "label": "炉具灶具类",
        "params": [
            {"key": "burner", "label_cn": "炉头/灶眼配置", "label_en": "Burner", "type": "text",
             "required": True, "export": True,
             "aliases": ["炉头", "灶眼", "burner", "火头", "炉盘"]},
            {"key": "heat_load", "label_cn": "热负荷", "label_en": "Heat Load", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["热负荷", "火力", "heat load", "功率", "热功率"]},
            {"key": "gas_type", "label_cn": "燃料类型", "label_en": "Gas Type", "type": "select",
             "options": ["天然气", "液化气", "煤气", "电磁"], "required": True, "export": True,
             "aliases": ["燃料", "气源", "gas type", "能源", "燃料类型"]},
            {"key": "ignition", "label_cn": "点火方式", "label_en": "Ignition", "type": "text",
             "required": False, "export": True,
             "aliases": ["点火", "ignition", "点火方式"]},
            {"key": "material", "label_cn": "材质", "label_en": "Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["材质", "material", "台面材质"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "dimension"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw"]},
            {"key": "flue", "label_cn": "排烟方式", "label_en": "Flue", "type": "text",
             "required": False, "export": True,
             "aliases": ["排烟", "烟道", "flue", "排烟方式"]},
        ],
    },
    "PROCESS": {
        "label": "食品加工机械类",
        "params": [
            {"key": "throughput", "label_cn": "产能", "label_en": "Throughput", "type": "text",
             "unit": "kg/h", "required": True, "export": True,
             "aliases": ["产能", "产量", "throughput", "处理量", "加工量", "效率"]},
            {"key": "power", "label_cn": "功率/电压", "label_en": "Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["功率", "电压", "power"]},
            {"key": "blade_speed", "label_cn": "刀具转速", "label_en": "Blade Speed", "type": "text",
             "unit": "r/min", "required": False, "export": True,
             "aliases": ["转速", "blade speed", "刀速"]},
            {"key": "feed_size", "label_cn": "进料尺寸", "label_en": "Feed Size", "type": "text",
             "unit": "mm", "required": False, "export": True,
             "aliases": ["进料", "投料", "feed size", "料径", "进料口径"]},
            {"key": "material", "label_cn": "接触物料材质", "label_en": "Contact Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["材质", "material", "刀具材质", "接触面材质"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "dimension"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw"]},
        ],
    },
    "DISPLAY": {
        "label": "展示保温柜类",
        "params": [
            {"key": "volume", "label_cn": "容积", "label_en": "Volume", "type": "number",
             "unit": "L", "required": True, "export": True,
             "aliases": ["容积", "容量", "volume", "升数"]},
            {"key": "temp_range", "label_cn": "保温温度", "label_en": "Temp. Range", "type": "text",
             "unit": "℃", "required": True, "export": True,
             "aliases": ["温度", "保温温度", "temp", "控温"]},
            {"key": "glass_type", "label_cn": "玻璃类型", "label_en": "Glass Type", "type": "text",
             "required": False, "export": True,
             "aliases": ["玻璃", "glass", "门玻璃", "玻璃类型"]},
            {"key": "power", "label_cn": "功率/电压", "label_en": "Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["功率", "电压", "power"]},
            {"key": "material", "label_cn": "材质", "label_en": "Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["材质", "material"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "dimension"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw"]},
            {"key": "lighting", "label_cn": "照明", "label_en": "Lighting", "type": "text",
             "required": False, "export": True,
             "aliases": ["照明", "灯光", "lighting", "灯"]},
        ],
    },
    "GENERIC": {
        "label": "通用/其他",
        "params": [
            {"key": "capacity", "label_cn": "规格/容量", "label_en": "Specification", "type": "text",
             "required": True, "export": True,
             "aliases": ["规格", "容量", "specification", "capacity", "型号规格", "规格型号"]},
            {"key": "power", "label_cn": "功率/电压", "label_en": "Power", "type": "text",
             "unit": "kW", "required": True, "export": True,
             "aliases": ["功率", "电压", "power"]},
            {"key": "material", "label_cn": "材质", "label_en": "Material", "type": "text",
             "required": True, "export": True,
             "aliases": ["材质", "material"]},
            {"key": "dimension", "label_cn": "外形尺寸", "label_en": "Dimension", "type": "text",
             "unit": "mm", "required": True, "export": True,
             "aliases": ["外形尺寸", "尺寸", "dimension"]},
            {"key": "net_weight", "label_cn": "净重", "label_en": "Net Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["净重", "nw", "net weight"]},
            {"key": "gross_weight", "label_cn": "毛重", "label_en": "Gross Weight", "type": "number",
             "unit": "kg", "required": True, "export": True,
             "aliases": ["毛重", "gw", "gross weight"]},
        ],
    },
}

# --------------------------------------------------------------------------
# 品类 → 模板族 映射（同行为主，品类不同则参数不同）
# --------------------------------------------------------------------------
CATEGORY_FAMILY = {
    # ---- 烘焙设备 ----
    "OVEN-DECK": "OVEN", "OVEN-ROTARY": "OVEN", "OVEN-CONV": "OVEN",
    "OVEN-COMBI": "OVEN", "OVEN-PIZZA": "OVEN", "OVEN-CHICKEN": "OVEN",
    "OVEN-TUNNEL": "OVEN", "PROOFER": "OVEN",
    # ---- 搅拌 / 面点 ----
    "MIXER": "MIXER", "SPINETTABLE": "MIXER",
    # ---- 冷冻冷藏 ----
    "FREEZER": "FREEZER", "REFRIGERATOR": "FREEZER", "FREEZER-WORK": "FREEZER",
    "ICE-MACHINE": "FREEZER",
    # ---- 清洗消毒 ----
    "DISHWASHER": "WASH", "GLASSWASHER": "WASH", "DISINFECTION": "WASH",
    "WASH": "WASH",
    # ---- 炉具灶具 ----
    "COOKER": "FURNACE", "STOVE": "FURNACE", "FRYER": "FURNACE",
    "GRILL": "FURNACE", "HOTPLATE": "FURNACE",
    # ---- 食品加工机械 ----
    "SLICER": "PROCESS", "CUTTER": "PROCESS", "MIXER-FOOD": "PROCESS",
    "SAUSAGE": "PROCESS", "VEGETABLE": "PROCESS",
    # ---- 展示保温 ----
    "DISPLAY": "DISPLAY", "WARMER": "DISPLAY",
    # ---- 其他 ----
    "OTHER": "GENERIC",
}


def get_family(category_code: str) -> str:
    return CATEGORY_FAMILY.get((category_code or "").upper(), "GENERIC")


def get_template(family: str) -> dict:
    return PARAM_TEMPLATES.get(family, PARAM_TEMPLATES["GENERIC"])


def get_params(category_code: str) -> dict:
    return get_template(get_family(category_code))


def build_alias_map(category_code: str) -> dict:
    """构建 alias → 字段 的映射，供文档/OCR 解析按品类使用。"""
    tpl = get_params(category_code)
    m = {}
    for p in tpl["params"]:
        for a in p.get("aliases", []):
            m[a.lower()] = p["key"]
    # 通用字段别名（所有品类共享）
    for a, k in [("sku", "sku"), ("货号", "sku"), ("料号", "sku"), ("编号", "sku"),
                 ("型号", "model"), ("model", "model"), ("model no", "model"),
                 ("品名", "name_cn"), ("名称", "name_cn"), ("产品名称", "name_cn"),
                 ("中文名称", "name_cn"), ("name", "name_cn"), ("description", "name_cn"),
                 ("英文名称", "name_en"), ("english name", "name_en"),
                 ("价格", "price_cny"), ("单价", "price_cny"), ("出厂价", "price_cny"),
                 ("fob价格usd", "price_usd"), ("usd", "price_usd"), ("usd price", "price_usd"),
                 ("fob price", "price_usd"), ("美元价", "price_usd"), ("出口价", "price_usd"),
                 ("包装尺寸", "packing_size"), ("packing size", "packing_size"),
                 ("moq", "moq"), ("起订量", "moq"), ("交期", "lead_time"),
                 ("交货期", "lead_time"), ("lead time", "lead_time"),
                 ("hs code", "hs_code"), ("hs编码", "hs_code"), ("海关编码", "hs_code"),
                 ("证书", "certs"), ("认证", "certs"), ("certificate", "certs"),
                 ("图片", "image_path"), ("image", "image_path"), ("photo", "image_path"),
                 ("关键词", "keywords")]:
        m.setdefault(a, k)
    return m