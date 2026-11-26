"""菲力克斯科技（广州）有限公司 — 种子数据（产品系列 / 供应商 / 分类 / 证书 / 默认报价单模板）。

数据来源：菲力克斯机械官网产品中心 example.com 公开产品系列与型号，以及公开企业资料。
重量/尺寸为工程经验值，使用者可在后台直接编辑覆盖。
"""
from .database import effective_company, get_conn, jdump, now, release_conn

# --------------------------------------------------------------------------
# 产品系列分类
# --------------------------------------------------------------------------
CATEGORIES = [
    # ========== 菲力克斯自有：烘焙设备 ==========
    ("OVEN-DECK", "层炉烤箱系列", "Deck Oven Series", 1),
    ("OVEN-ROTARY", "热风旋转炉系列", "Hot Air Rotary Oven Series", 2),
    ("OVEN-CONV", "热风循环炉系列", "Convection Oven Series", 3),
    ("OVEN-COMBI", "万能蒸烤箱系列", "Combi Oven Series", 4),
    ("OVEN-PIZZA", "披萨炉系列", "Pizza Oven Series", 5),
    ("OVEN-CHICKEN", "烤鸡炉系列", "Chicken Roast Oven Series", 6),
    ("OVEN-TUNNEL", "隧道炉系列", "Tunnel Oven Series", 7),
    ("MIXER", "搅拌机系列", "Mixer Series", 8),
    ("SPINETTABLE", "起酥机/压面机系列", "Spiral Sheeter / Dough Sheeter", 9),
    ("PROOFER", "醒发箱系列", "Proofer Cabinet Series", 10),
    ("DISPLAY", "展示保温柜系列", "Display & Food Warmer", 11),
    # ========== 同行 / 供应商扩展品类（品类不同 → 参数体系不同）============
    ("FREEZER", "冷冻冷藏设备系列", "Refrigeration & Freezer", 12),
    ("FREEZER-WORK", "冷藏工作台系列", "Refrigerated Prep Table", 13),
    ("DISHWASHER", "洗碗机系列", "Dishwasher Series", 14),
    ("GLASSWASHER", "洗杯机系列", "Glass Washer", 15),
    ("DISINFECTION", "消毒烘干设备系列", "Disinfection & Drying Cabinet", 16),
    ("COOKER", "炒炉系列", "Wok Burner Series", 17),
    ("STOVE", "汤炉/灶具系列", "Soup Stove & Burner", 18),
    ("FRYER", "炸炉系列", "Fryer Series", 19),
    ("GRILL", "烧烤炉系列", "Grill Series", 20),
    ("HOTPLATE", "电磁炉/扒炉系列", "Induction & Griddle", 21),
    ("SLICER", "切肉机系列", "Meat Slicer", 22),
    ("CUTTER", "斩切/绞肉机系列", "Cutter & Meat Grinder", 23),
    ("SAUSAGE", "灌肠机系列", "Sausage Stuffer", 24),
    ("VEGETABLE", "切菜机系列", "Vegetable Cutter", 25),
    ("WARMER", "保温售卖系列", "Food Holding & Warming", 26),
    ("OTHER", "其他设备", "Other Equipment", 27),
]

SUPPLIERS = [
    ("SELF", "菲力克斯科技（广州）有限公司(自有工厂)", "Felix Technology (Guangzhou) Co., Ltd.", "sales@example.com", "+86 20 3667 7767", "自有产线，非标定制能力"),
    ("SUP-A", "佛山金厨不锈钢制品厂", "Foshan Jinkitchen Stainless Steel Works", "sales@jinkitchen.cn", "+86 757 8888 1234", "不锈钢台面/货架/水槽/工程定制"),
    ("SUP-B", "广州恒信制冷设备有限公司", "Guangzhou Hengxin Refrigeration Co., Ltd.", "info@hengxin-refr.com", "+86 20 3222 8899", "冷藏展示柜/冷库机组/工作台"),
    ("SUP-C", "上海菱科电气有限公司", "Shanghai Lingke Electric Co., Ltd.", "contact@lingke-elec.com", "+86 21 6688 1234", "温控器/加热管/变频器/电气配件"),
    ("SUP-D", "意大利 SIRIO 配件代理", "SIRIO Italy Parts (Agent)", "agent@sirio-parts.it", "+39 030 2123456", "披萨炉/烤箱原装配件代理"),
    ("SUP-E", "山东鲁泰铸造件厂", "Shandong Lutai Casting Factory", "sales@lutaicast.com", "+86 539 777 8888", "铸铁炉盘/炉膛/燃烧器部件"),
    ("SUP-F", "宁波华联厨具制造有限公司", "Ningbo Hualian Kitchenware Mfg. Co., Ltd.", "sales@hualiankitchen.com", "+86 574 8766 5432", "炒炉/汤炉/灶具/炉具工程定制"),
    ("SUP-G", "中山泰达厨具设备有限公司", "Zhongshan Taida Kitchen Equipment Co., Ltd.", "info@taidakitchen.com", "+86 760 2288 9900", "蒸饭柜/保温柜/货架/工作台"),
    ("SUP-H", "青岛海盛食品机械有限公司", "Qingdao Haisheng Food Machinery Co., Ltd.", "sales@haishengfm.com", "+86 532 8899 7766", "切肉机/绞肉机/灌肠机/真空包装机"),
    ("SUP-I", "宁波爱德清洗设备有限公司", "Ningbo Aide Cleaning Equipment Co., Ltd.", "info@aideclean.com", "+86 574 6688 3322", "洗碗机/洗杯机/消毒烘干设备"),
    ("SUP-J", "深圳华冷制冷设备有限公司", "Shenzhen Hualeng Refrigeration Co., Ltd.", "sales@hualengfr.com", "+86 755 3322 7766", "超低温速冻/的工作台冷柜"),
]

# --------------------------------------------------------------------------
# 同行/供应商扩展品类样例 — 展示「不同品类的参数体系不同」
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 产品：字段 = (sku, 名称cn, 名称en, 分类, 型号, 规格cn, 规格en, 材质, 功率, 容量,
#               尺寸mm, 包装尺寸, 净重kg, 毛重kg, 人民币价, USD价, MOQ, 交期, HS, 证书, 英文详情, 关键词)
# --------------------------------------------------------------------------
C_STD = ["CVC", "SC", "ISO9001"]
C_ELEC = ["CVC", "SC", "ISO9001", "CE"]
C_GAS = ["CVC", "SC", "ISO9001", "CE"]
C_FULL = ["CVC", "SC", "ISO9001", "CE", "FDA"]

PEER_PRODUCTS = [
    ("HX-2T-600", "六盘抽油烟型蒸饭柜", "6-Tray Electric Rice Steamer with Hood", "OTHER", "HX-2T-600",
     "SUS304 电热蒸汽，600×600盘，抽油烟罩一体", "SS304 steam heated, 600x600 tray, integrated exhaust hood",
     "SUS304", "220V 6kW", "6盘 600x600mm",
     "1100x800x1950", "1250x900x2050", 195, 230, 9800, 1320, 1, "25天", "8419.81", C_ELEC,
     "Commercial rice steamer with integrated smoke exhaust hood. Stainless steel construction, uniform steam distribution, suitable for large-scale canteens.",
     "蒸饭柜,rice steamer,食堂"),
    ("YX-2B-400", "双头燃气炒炉", "2-Burner Gas Wok Range", "COOKER", "YX-2B-400",
     "双头铸铁炉圈，重压铸造炉体，摇摆水龙", "2 cast iron burner rings, heavy duty cast body, water faucet",
     "铸铁炉圈 + 冷轧板炉体", "天然气 2×22kW", "2炉头 Φ400mm",
     "1400x700x800", "1550x820x900", 180, 215, 4200, 560, 1, "20天", "8415.11", C_GAS,
     "Two burner gas wok range with heavy duty cast iron rings and swing water faucet. Popular in Chinese restaurant kitchens.",
     "炒炉,wok range,炒炉系列"),
    ("TL-3T-120", "三头汤炉/卤水炉", "3-Burner Soup Stove", "STOVE", "TL-3T-120",
     "三头汤池，环形燃烧器，保温汤锅", "3 soup wells, ring burners, thermal retention pots",
     "SUS304 汤池", "天然气 3×7kW", "3汤池 Φ400mm",
     "1500x700x850", "1650x820x950", 210, 248, 5600, 750, 1, "25天", "8415.11", C_GAS,
     "Three burner soup stove with independent ring burners for soup and braised food. Stainless steel soup wells for easy cleaning.",
     "汤炉,soup stove,卤水炉"),
    ("ZL-30L", "30L电热油炸炉", "30L Electric Fryer", "FRYER", "ZL-30L",
     "双篮电加热，控温 90-190℃，自动断电保护", "Twin basket electric heating, 90-190°C control, auto shut-off",
     "SUS304 油缸", "220V 5.5kW", "30L",
     "550x600x900", "700x700x1000", 68, 82, 3200, 430, 1, "20天", "8419.81", C_ELEC,
     "Twin basket electric deep fryer with digital temperature control from 90 to 190 degrees. Automatic power cut-off for safety.",
     "炸炉,fryer,油炸炉"),
    ("QC-360E", "全自动切肉机", "Automatic Electric Meat Slicer", "SLICER", "QC-360E",
     "360mm 刀片，变频调速，切厚可调", "360mm blade, variable speed, adjustable slice thickness",
     "SUS304 刀盘 + 铝合金机身", "220V 1.5kW", "切厚 0-15mm",
     "620x520x480", "700x600x560", 82, 98, 6800, 920, 1, "25天", "8418.89", C_ELEC,
     "Automatic meat slicer with 360mm stainless blade, variable speed drive and adjustable slice thickness. Ideal for deli and butcher shops.",
     "切肉机,slicer,切丝机"),
    ("JR-20", "20型绞肉机", "Meat Grinder Type 20", "CUTTER", "JR-20",
     "筛孔可换，出肉量高，齿轮箱结构", "Interchangeable grinding plates, high output, gear box driven",
     "SUS304 接触部件", "220V 1.5kW", "出肉 80kg/h",
     "480x250x450", "560x330x530", 45, 56, 2200, 300, 1, "20天", "8418.89", C_ELEC,
     "Compact meat grinder with interchangeable grinding plates. High throughput for small butchery and restaurant operations.",
     "绞肉机,meat grinder,绞肉"),
    ("GC-15", "15型灌肠机", "Sausage Stuffer Type 15", "SAUSAGE", "GC-15",
     "15L 料斗，三种灌装嘴", "15L hopper, three stuffing nozzles",
     "SUS304 料斗", "220V 1.1kW", "15L",
     "500x300x600", "600x380x680", 52, 64, 2800, 380, 1, "25天", "8418.89", C_ELEC,
     "Compact sausage stuffer with 15 liter hopper and three interchangeable stuffing nozzles for various sausage sizes.",
     "灌肠机,sausage stuffer"),
    ("QC-2B", "双缸洗杯机", "Twin Tank Glass Washer", "GLASSWASHER", "QC-2B",
     "双圆盘清洗，60-85℃ 蒸汽热水冲洗", "Twin rotating brushes, 60-85°C steam heated rinse",
     "SUS304 内胆", "380V 6kW", "清洗 600杯/h",
     "1100x800x850", "1250x900x950", 190, 225, 12800, 1720, 1, "35天", "8422.23", C_ELEC,
     "Twin tank glasswasher with rotating brushes and steam heated rinse cycle. Ideal for bars, cafes and hotels.",
     "洗杯机,glass washer"),
    ("XJ-1", "单门洗碗机", "Single Door Dishwasher", "DISHWASHER", "XJ-1",
     "碗篮式，热风烘干，60-85℃", "Basket type, hot air drying, 60-85°C wash",
     "SUS304 内腔", "380V 5.5kW", "60筐/h",
     "650x750x850", "750x850x950", 135, 160, 9600, 1290, 1, "30天", "8422.23", C_ELEC,
     "Single door commercial dishwasher with basket conveyor and hot air drying. 60 racks per hour for restaurant kitchens.",
     "洗碗机,dishwasher"),
    ("LC-500", "500L 卧式冷柜", "500L Chest Freezer", "FREEZER", "LC-500",
     "500L 容积，-25℃ 深冷，节能压缩机", "500L capacity, -25°C deep freeze, energy saving compressor",
     "镀锌板喷漆 + SUS304 内胆", "220V 0.6kW", "500L",
     "1500x700x850", "1650x800x950", 105, 128, 4800, 650, 1, "30天", "8418.10", C_ELEC,
     "500 liter chest freezer with -25 degree cold deep freezing, energy efficient compressor and thick insulation.",
     "冷柜,freezer,冷冻柜"),
    ("LCG-1200", "1200mm 四门冷藏柜", "1200mm 4-Door Refrigerator", "FREEZER", "LCG-1200",
     "四门，0-10℃ 温控，风冷无霜", "4 doors, 0-10°C control, fan cooling no frost",
     "SUS304 内胆", "220V 0.75kW", "1200L",
     "1200x700x2000", "1350x780x2100", 285, 340, 14800, 2000, 1, "35天", "8418.10", C_ELEC,
     "Four door upright refrigerator with fan cooling and no frost design. Precise temperature control and energy saving LED lighting.",
     "冷藏柜,refrigerator,四门柜"),
]

PRODUCTS = [
    # ---- 层炉烤箱系列 ----
    ("WV-YJ-2", "二层二盘电热层炉烤箱", "2-Tray Electric Deck Oven", "OVEN-DECK", "YJ-2",
     "304不锈钢内胆，上下调温独立控制，50-300℃，带观察窗", "304 SS chamber, independent top/bottom heat control 50-300°C, glass window",
     "外壳1.0mm冷轧板喷漆/内胆SUS304", "220V 3.6kW", "2盘 600x400mm",
     "1100x780x1400", "1250x880x1500", 165, 195, 5600, 780, 1, "25-30天", "8419.81",
     C_ELEC,
     "Heavy duty deck oven with 304 stainless steel baking chamber. Independent top and bottom heating control for precise baking. Built-in timer and temperature gauge. Ideal for bakeries, hotels and cake shops.",
     "层炉,deck oven,电烤箱,烘焙"),

    ("WV-YJ-3", "三层三盘电热层炉烤箱", "3-Tray Electric Deck Oven", "OVEN-DECK", "YJ-3",
     "三层三盘，304内胆，数字温控", "3 decks, 304 SS liner, digital temperature controller",
     "外壳1.0mm冷轧板喷漆/内胆SUS304", "220V 5.4kW", "3盘 600x400mm",
     "1100x780x1750", "1250x880x1850", 215, 248, 7200, 980, 1, "25-30天", "8419.81",
     C_ELEC,
     "Three deck baking oven for medium-scale bakery production. Even heat distribution, independent deck control, energy saving heating tubes.",
     "层炉,三层烤箱,deck oven"),

    ("WV-YJ-4", "四层四盘电热层炉烤箱", "4-Tray Electric Deck Oven", "OVEN-DECK", "YJ-4",
     "四层四盘大容量，304内胆", "4 decks large capacity, 304 SS liner",
     "外壳1.0mm冷轧板喷漆/内胆SUS304", "380V 8.6kW", "4盘 600x400mm",
     "1250x850x1850", "1400x950x1950", 285, 325, 9800, 1320, 1, "30-35天", "8419.81",
     C_ELEC,
     "Four deck oven with large chamber capacity for high-volume bread and pastry production. Robust steel frame, long service life.",
     "层炉,四层烤箱,大容量"),

    ("WV-HGA-20Q", "皇冠A款燃气烤炉(8盘)", "Crown-A Gas Deck Oven 8-Tray", "OVEN-DECK", "HGA-20Q",
     "燃气加热，全幅黑色耐高温玻璃炉门", "Gas heated, full black heat-resistant glass door",
     "炉门黑晶玻璃/内胆SUS304", "天然气 12kW", "8盘 600x400mm",
     "1900x1150x2100", "2050x1250x2200", 520, 590, 26800, 3650, 1, "35-45天", "8419.81",
     C_GAS,
     "Crown series gas deck oven with premium black heat-resistant glass door. Classic European style, high throughput, ideal for high-volume commercial bakeries.",
     "燃气层炉,燃气烤炉,Crown,皇冠"),

    ("WV-WXRC-20D", "黑钻智能电烤箱(三层六盘)", "Black Diamond Smart Electric Oven 6-Tray", "OVEN-DECK", "WXRC-20D",
     "触控操作，菜单记忆功能，欧式设计", "Touch panel with recipe memory, European design",
     "全不锈钢SUS304", "380V 12kW", "6盘 600x400mm",
     "1400x1200x2000", "1550x1320x2120", 480, 545, 19800, 2680, 1, "40-50天", "8419.81",
     C_FULL,
     "Black Diamond series smart electric oven with touch control panel and programmable recipe memory. Sleek European styling, stainless steel construction, multi-function cooking modes.",
     "黑钻烤箱,智能烤箱,触控烤箱"),

    ("WV-WXRC-60Q", "黑钛智能燃气烤炉", "Black Titanium Smart Gas Oven", "OVEN-DECK", "WXRC-60Q",
     "黑钛智能系列，燃气加热，大容量", "Black titanium series, gas heated, high capacity",
     "全不锈钢SUS304", "天然气 24kW", "12盘 600x400mm",
     "2200x1400x2200", "2350x1520x2320", 780, 860, 42000, 5680, 1, "45-60天", "8419.81",
     C_FULL,
     "Black Titanium smart gas oven, flagship model with large capacity and precise temperature control. Premium finish for hotel and central bakery projects.",
     "黑钛烤炉,燃气烤炉,大容量"),

    # ---- 热风旋转炉 ----
    ("WV-XYJ-2R", "双层热风旋转炉(燃气)", "2-Deck Rotary Oven (Gas)", "OVEN-ROTARY", "XYJ-2R",
     "2盘热风循环，炉盘可旋转，天然气加热", "2 decks hot air circulation, rotating trays, gas heated",
     "外壳1.2mm/内胆SUS304", "天然气 12kW", "2盘 600x400mm",
     "1600x1300x2100", "1750x1420x2200", 620, 700, 32800, 4450, 1, "40-50天", "8419.81",
     C_GAS,
     "Two-deck rotary oven with uniform hot air circulation and rotating tray carriage. Excellent bread color consistency, gas heated for energy efficiency.",
     "旋转炉,rotary oven,热风炉"),

    ("WV-XYJ-4R", "四层热风旋转炉(电热)", "4-Deck Rotary Oven (Electric)", "OVEN-ROTARY", "XYJ-4R",
     "4盘旋转，触控面板，记忆程序", "4 decks rotary, touch panel, program memory",
     "全不锈钢SUS304", "380V 18kW", "4盘 600x400mm",
     "1900x1500x2300", "2050x1620x2400", 980, 1080, 56000, 7600, 1, "50-60天", "8419.81",
     C_FULL,
     "Four-deck rotary convection oven with touch control and program memory. Uniform heat circulation guarantees consistent baking quality batch after batch.",
     "旋转炉,四层旋转炉"),

    # ---- 热风循环炉 ----
    ("WV-RFJ-3D", "三盘热风循环电烤箱", "3-Tray Electric Convection Oven", "OVEN-CONV", "RFJ-3D",
     "独立风道设计，风力均匀，蒸汽功能", "Independent air duct, uniform airflow, steam function",
     "内胆SUS304", "220V 5.2kW", "3盘 600x400mm",
     "900x760x1150", "1020x860x1250", 145, 175, 9500, 1280, 1, "20-25天", "8419.81",
     C_ELEC,
     "Convection oven with independent air channel design for uniform airflow and precise temperature control. Space-saving compact design with steam function for versatile baking.",
     "热风循环炉,convection oven,风炉"),

    ("WV-RFJ-5D", "五盘热风循环燃气烤炉", "5-Tray Gas Convection Oven", "OVEN-CONV", "RFJ-5D",
     "5盘，燃气加热，省气30%", "5 trays, gas heated, 30% energy saving",
     "内胆SUS304", "天然气 9kW", "5盘 600x400mm",
     "1150x900x1500", "1280x1000x1600", 235, 275, 18500, 2500, 1, "30-35天", "8419.81",
     C_GAS,
     "Gas convection oven with 5 trays, energy efficient burners. Uniform hot air circulation reduces baking time and fuel consumption significantly.",
     "燃气循环炉,convection oven"),

    ("WV-LHY-1", "层炉烤箱(单层单盘经济型)", "Single Deck Economical Oven", "OVEN-CONV", "LHY-1",
     "经济型单层设计，结构简单实用", "Economical single deck, simple and practical",
     "冷轧板喷漆", "220V 2.6kW", "1盘 600x400mm",
     "700x600x900", "820x720x1000", 62, 78, 3200, 430, 1, "20天", "8419.81",
     C_STD,
     "Economical single deck oven for small bakeries and cafes. Simple structure, easy operation, cost effective.",
     "经济烤箱,单层烤箱"),

    # ---- 万能蒸烤箱 ----
    ("WV-ZKX-4", "四盘万能蒸烤箱(电热)", "4-Tray Electric Combi Oven", "OVEN-COMBI", "ZKX-4",
     "360度热风+蒸汽功能，温度更均匀", "360° hot air + steam function, even temperature",
     "全不锈钢SUS304", "380V 10.8kW", "4盘 GN1/1",
     "880x780x1080", "1000x880x1180", 168, 196, 26000, 3520, 1, "35-45天", "8419.81",
     C_FULL,
     "Multi-function combi oven combining hot air, steam and combination modes. Precise steam control, 360° heat circulation, ideal for hotels and multi-function kitchens.",
     "蒸烤箱,combi oven,万能烤箱"),

    ("WV-ZKX-6", "六盘万能蒸烤箱(电热)", "6-Tray Electric Combi Oven", "OVEN-COMBI", "ZKX-6",
     "6盘容量，触控操作，菜谱记忆", "6 trays, touch operation, recipe memory",
     "全不锈钢SUS304", "380V 15.6kW", "6盘 GN1/1",
     "1050x800x1180", "1180x900x1280", 235, 268, 36000, 4880, 1, "40-50天", "8419.81",
     C_FULL,
     "Six-tray combi oven with touch panel and recipe memory. Suitable for central kitchens, hotels and catering groups requiring high capacity.",
     "蒸烤箱,六盘,combi oven"),

    ("WV-WGOA-10-AC", "十盘万能蒸烤箱", "10-Tray Universal Combi Oven", "OVEN-COMBI", "WGOA-10-AC",
     "10盘超大容量，中央厨房级", "10 trays, central kitchen grade capacity",
     "全不锈钢SUS304", "380V 24kW", "10盘 GN1/1",
     "1350x850x1250", "1480x950x1350", 385, 430, 58000, 7850, 1, "50-60天", "8419.81",
     C_FULL,
     "Ten-tray combi oven for central kitchen and institutional catering. High throughput, low energy consumption, easy cleaning with rounded corners.",
     "中央厨房,蒸烤箱,十盘"),

    # ---- 披萨炉 ----
    ("WV-PZ-2D", "双层电热披萨烤箱", "2-Deck Electric Pizza Oven", "OVEN-PIZZA", "PZ-2D",
     "德国恒温器，双层披萨炉，含石板", "German thermostat, 2 decks, stone deck included",
     "炉体耐热钢/炉盘铸铁", "220V 5.4kW", "2层 400x400mm",
     "1000x750x900", "1140x850x1000", 138, 165, 6500, 880, 1, "25-30天", "8419.81",
     C_ELEC,
     "Two-deck pizza oven with German thermostat for precise temperature control. Includes refractory stone decks for authentic crispy crust pizzas.",
     "披萨炉,pizza oven,双层披萨炉"),

    ("WV-PZ-1G", "单层燃气披萨炉", "1-Deck Gas Pizza Oven", "OVEN-PIZZA", "PZ-1G",
     "单层大容量，燃气加热，精准控温", "Single large deck, gas heated, precise control",
     "耐热钢炉体/铸铁炉盘", "天然气 6kW", "1层 500x500mm",
     "800x750x1050", "920x850x1150", 112, 138, 5900, 790, 1, "25-30天", "8419.81",
     C_GAS,
     "Gas-fired single deck pizza oven, simple and efficient. Precise temperature control for consistent pizza quality. Popular in pizza restaurants.",
     "燃气披萨炉,pizza oven"),

    ("WV-PZ-2D-KW", "意式燃煤披萨炉(双层)", "Italian Wood-Fired Pizza Oven 2-Deck", "OVEN-PIZZA", "PZ-2D-KW",
     "意式传统设计，耐火砖炉膛，可视炉窗", "Italian traditional design, refractory brick chamber, viewing window",
     "耐火砖+不锈钢外壳", "燃气/木柴 可选", "2层 800x800mm",
     "1400x850x1900", "1540x950x2000", 780, 890, 42000, 5680, 1, "50-60天", "8419.81",
     C_FULL,
     "Traditional Italian wood-fired pizza oven with refractory brick chamber. Authentic Neapolitan pizza capability, suitable for Italian restaurants and hotels.",
     "燃煤披萨炉,意式披萨炉,wood fired"),

    # ---- 烤鸡炉 ----
    ("WV-KJL-8Q", "八盘燃气烤鸡炉", "8-Tray Gas Chicken Roast Oven", "OVEN-CHICKEN", "KJL-8Q",
     "8盘挂车式，一次可烤多只鸡", "8-tray trolley type, roast multiple chickens at once",
     "外壳不锈钢/内胆SUS304", "天然气 14kW", "8只鸡容量",
     "1600x1100x2200", "1750x1220x2300", 480, 550, 29800, 4020, 1, "40-50天", "8419.81",
     C_GAS,
     "Eight-tray gas chicken roast oven with trolley system. High output roasting for fried chicken, roast chicken and duck businesses. Equipped with rotisserie hooks and drip trays.",
     "烤鸡炉,chicken oven,挂炉"),

    ("WV-KJL-12Q", "十二盘大型烤鸡炉", "12-Tray Large Chicken Roast Oven", "OVEN-CHICKEN", "KJL-12Q",
     "12盘大型挂车，适合连锁餐饮中央厨房", "12-tray large trolley, suitable for chain catering central kitchen",
     "全不锈钢SUS304", "天然气 20kW", "12只鸡容量",
     "2000x1300x2300", "2150x1420x2400", 690, 780, 42000, 5680, 1, "50-60天", "8419.81",
     C_GAS,
     "Twelve-tray commercial chicken oven for large-scale fried chicken and roast duck operations. Even heat, easy cleaning, robust construction for continuous operation.",
     "大型烤鸡炉,chicken oven"),

    # ---- 隧道炉 ----
    ("WV-SDL-6", "六米热风隧道炉(电热)", "6m Electric Hot Air Tunnel Oven", "OVEN-TUNNEL", "SDL-6",
     "连续式生产，适用吐司/面包/月饼", "Continuous production, suitable for toast, bread, mooncake",
     "全不锈钢SUS304", "380V 60kW", "连续式 600mm带宽",
     "6000x1500x2100", "6200x1620x2200", 2600, 2950, 168000, 22600, 1, "75-90天", "8419.81",
     C_FULL,
     "Six-meter continuous hot air tunnel oven for large-scale bread production. Adjustable conveyor speed and zone temperature control for maximum throughput.",
     "隧道炉,tunnel oven,连续烤炉"),

    ("WV-SDL-9", "九米燃气隧道炉", "9m Gas Tunnel Oven", "OVEN-TUNNEL", "SDL-9",
     "9米超长炉体，多区独立控温，燃气节能", "9m long chamber, multi-zone independent control, gas energy saving",
     "全不锈钢SUS304", "天然气 90kW", "连续式 800mm带宽",
     "9000x1800x2400", "9200x1920x2500", 4200, 4750, 285000, 38500, 1, "90-120天", "8419.81",
     C_FULL,
     "Nine-meter gas tunnel oven with multi-zone independent temperature control for large centralized baking lines. Suitable for bread factories and industrial food processing.",
     "隧道炉,tunnel oven,生产线"),

    # ---- 搅拌机系列 ----
    ("WV-JBJ-5L", "5升蛋奶搅拌机", "5L Egg Beater Mixer", "MIXER", "JBJ-5L",
     "5升缸容量，双速，台式设计", "5L bowl, two-speed, tabletop design",
     "SUS304缸体", "220V 0.75kW", "5L",
     "450x400x680", "550x480x760", 38, 46, 2200, 300, 1, "20天", "8418.81",
     C_ELEC,
     "Compact 5L egg beater mixer for cakes, creams and pastries. Stainless steel bowl, two-speed motor, ideal for small bakeries and dessert shops.",
     "搅拌机,打蛋机,egg mixer"),

    ("WV-JBJ-20L", "20升蛋奶搅拌机", "20L Egg Beater Mixer", "MIXER", "JBJ-20L",
     "20升容量，变频调速，可拆洗", "20L capacity, variable speed, detachable bowl",
     "SUS304缸体", "220V 1.5kW", "20L",
     "600x600x1100", "700x680x1200", 78, 92, 4800, 650, 1, "25天", "8418.81",
     C_ELEC,
     "20L planetary egg beater mixer with variable speed control and detachable stainless steel bowl. Widely used in pastry kitchens and dessert factories.",
     "搅拌机,20升,行星搅拌"),

    ("WV-HMJ-25", "25升螺旋和面机", "25L Spiral Dough Mixer", "MIXER", "HMJ-25",
     "螺旋搅拌，出面效率高，PLC控制", "Spiral hook, high output, PLC control",
     "SUS304缸体", "380V 3kW", "25L 一次25kg面团",
     "700x900x1250", "820x1000x1350", 235, 268, 12800, 1720, 1, "35-45天", "8418.81",
     C_ELEC,
     "25L spiral dough mixer with high efficiency spiral hook design and PLC touch control. Suitable for medium-size bakeries producing bread, buns and pastries.",
     "和面机,spiral mixer,面团搅拌"),

    ("WV-HMJ-50", "50升螺旋和面机", "50L Spiral Dough Mixer", "MIXER", "HMJ-50",
     "50升大容量，双变速，变频可选", "50L large capacity, dual variable speed",
     "SUS304缸体", "380V 5.5kW", "50L 一次50kg面团",
     "950x1100x1450", "1070x1220x1550", 395, 440, 21800, 2950, 1, "40-50天", "8418.81",
     C_ELEC,
     "Fifty-liter spiral dough mixer with dual speed system for bread and heavy dough. Stainless steel bowl, sturdy construction for continuous commercial use.",
     "和面机,50升,spiral mixer"),

    ("WV-HMJ-100", "100升大功率和面机", "100L Heavy Duty Dough Mixer", "MIXER", "HMJ-100",
     "100升，工厂级，配备压面机接口", "100L industrial grade, interfaces with dough sheeter",
     "SUS304缸体", "380V 11kW", "100L 一次100kg面团",
     "1250x1300x1650", "1380x1420x1750", 680, 760, 42000, 5680, 1, "50-60天", "8418.81",
     C_ELEC,
     "Hundred-liter industrial spiral mixer for large-scale bread production. Powerful motor, automatic bowl lift, designed to integrate with dough sheeter lines.",
     "和面机,100升,工业和面机"),

    # ---- 起酥机 / 压片机 ----
    ("WV-QSJ-500", "500型桌面起酥机", "WV-500U Table Top Spiral Sheeter", "SPINETTABLE", "QSJ-500",
     "桌面型起酥压面，双档厚度调节", "Tabletop dough sheeter, two-stage thickness adjustment",
     "SUS304压面轴", "220V 1.5kW", "宽500mm",
     "600x550x600", "700x650x700", 95, 112, 9800, 1320, 1, "30-35天", "8418.81",
     C_ELEC,
     "Table top spiral dough sheeter with 500mm roller width and seamless stainless steel rollers. Adjustable thickness for pastry and cracker production.",
     "起酥机,压面机,sheeter"),

    ("WV-QSJ-600B", "600型不粘压面机", "600mm Non-stick Dough Sheeter", "SPINETTABLE", "QSJ-600B",
     "不粘涂层辊轴，宽度600mm，双速", "Non-stick coated rollers, 600mm width, two-speed",
     "不粘辊轴SUS304", "220V 2.2kW", "宽600mm",
     "800x700x1250", "900x780x1350", 168, 195, 12800, 1720, 1, "35-45天", "8418.81",
     C_ELEC,
     "Non-stick coated dough sheeter with 600mm width, easy cleaning and dough release. Two-speed motor for flexible lamination processes.",
     "压面机,不粘压面机"),

    # ---- 醒发箱 ----
    ("WV-XFB-12", "十二盘醒发箱", "12-Tray Proofer Cabinet", "PROOFER", "XFB-12",
     "12盘容量，温湿度控制，发酵均匀", "12 trays, temperature & humidity control, uniform proofing",
     "内胆SUS304", "220V 2.2kW", "12盘 600x400mm",
     "800x750x1950", "920x850x2050", 210, 245, 14800, 2000, 1, "35-45天", "8419.81",
     C_ELEC,
     "Twelve-tray proofer cabinet with precise temperature and humidity control. Ensures uniform fermentation for consistent bread and pastry volume.",
     "醒发箱,proofer,发酵箱"),

    ("WV-XFB-18", "十八盘大容量醒发箱", "18-Tray Large Proofer Cabinet", "PROOFER", "XFB-18",
     "18盘超大容量，节能保温", "18 trays extra large capacity, energy-saving insulation",
     "全不锈钢SUS304", "380V 4.5kW", "18盘 600x400mm",
     "1050x850x2050", "1180x950x2150", 320, 368, 22800, 3080, 1, "40-50天", "8419.81",
     C_ELEC,
     "Eighteen-tray proofer for high-volume bread production. Thick insulation and digital control for stable fermentation environment.",
     "醒发箱,18盘,大容量"),

    # ---- 展示保温柜 ----
    ("WV-BWV-60", "60升食品保温柜", "60L Food Warmer Cabinet", "DISPLAY", "BWV-60",
     "玻璃门展示，恒温保温60-90℃", "Glass door display, constant temperature 60-90°C",
     "SUS304内胆", "220V 1.2kW", "60L",
     "700x600x1300", "800x680x1400", 68, 82, 4200, 570, 1, "25天", "8419.81",
     C_ELEC,
     "Food warmer display cabinet with glass door and constant temperature control, keeping cooked food fresh and warm for service.",
     "保温柜,food warmer,展示柜"),

    ("WV-ZSG-120", "冷藏工作台(1200mm)", "Refrigerated Work Table 1200mm", "DISPLAY", "ZSG-120",
     "冷藏柜工作台，节能压缩机，温控数显", "Refrigerated prep table, energy-saving compressor, digital temp control",
     "SUS304台面及内胆", "220V 0.75kW", "容积300L",
     "1200x650x850", "1340x730x930", 128, 148, 7800, 1050, 1, "30天", "8418.10",
     C_ELEC,
     "Refrigerated work table with stainless steel work surface, energy-efficient compressor and digital temperature display. Ideal for restaurant prep areas.",
     "冷藏工作台,冷柜,操作台"),

    # ---- 其他 ----
    ("WV-BMP-6", "6升爆米花机", "6L Popcorn Machine", "OTHER", "BMP-6",
     "6L锅体，恒温控制，移动轮设计", "6L kettle, constant temperature, casters",
     "SUS304锅体", "220V 1.8kW", "6L/次",
     "500x550x900", "600x630x980", 58, 70, 3800, 520, 1, "25天", "8419.81",
     C_ELEC,
     "Six-liter popcorn machine with constant temperature control and mobile caster base. Popular in cinemas, malls and snack bars.",
     "爆米花机,popcorn"),
]

# --------------------------------------------------------------------------
# 报价单模板
# --------------------------------------------------------------------------
TPL_STANDARD_EN = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{{css}}</style></head>
<body>
<div class="page">
  <div class="head">
    <div class="head-left">
      <div class="brand">{{company.brand}}</div>
      <div class="cname">{{company.name_en}}</div>
      <div class="cmeta">
        {{company.address_en or company.address_cn}}<br>
        Tel: {{company.phone}} | Hotline: {{company.hotline}}<br>
        Email: sales@example.com | Web: {{company.website}}
      </div>
    </div>
    <div class="head-right">
      <div class="qtitle">QUOTATION</div>
      <table class="qmeta">
        <tr><td>Quo. No.</td><td>{{quote.quote_no}}</td></tr>
        <tr><td>Date</td><td>{{quote.date}}</td></tr>
        <tr><td>Valid Until</td><td>{{quote.valid_until}}</td></tr>
        <tr><td>Currency</td><td>{{quote.currency}}</td></tr>
      </table>
    </div>
  </div>

  <div class="toblock">
    <div><b>TO:</b> {{customer.company_en}}</div>
    <div>{{customer.contact or ''}} {{customer.phone or ''}}</div>
    <div>{{customer.address or ''}}</div>
    <div>{{customer.country or ''}}</div>
  </div>

  <table class="items">
    <thead><tr>
      <th style="width:36px">No.</th><th style="width:76px">Model</th><th>Description</th>
      <th style="width:96px">Material</th><th style="width:76px">Dimension<br>(mm)</th>
      <th style="width:44px">N.W.<br>(kg)</th><th style="width:44px">G.W.<br>(kg)</th>
      <th style="width:46px">Carton<br>(m³)</th><th style="width:36px">Qty</th>
      <th style="width:62px">Unit Price<br>({{quote.currency}})</th><th style="width:74px">Amount</th>
    </tr></thead>
    <tbody>
    {% for it in items %}
      <tr>
        <td>{{loop.index}}</td>
        <td>{{it.model or ''}}<br><small>{{it.sku}}</small></td>
        <td><b>{{it.name_en}}</b><br><small>{{it.name_cn}}</small>{% if it.spec_en %}<br><small>{{it.spec_en}}</small>{% endif %}{% if it.certs %}<br><small>Cert: {{it.certs}}</small>{% endif %}</td>
        <td>{{it.material or ''}}</td>
        <td>{{it.dimension or ''}}</td>
        <td>{{it.net_weight or ''}}</td>
        <td>{{it.gross_weight or ''}}</td>
        <td>{{it.carton or ''}}</td>
        <td>{{it.qty}}</td>
        <td class="r">{{ "%.2f"|format(it.price) }}</td>
        <td class="r b">{{ "%.2f"|format(it.amount) }}</td>
      </tr>
    {% endfor %}
      {% if not items %}<tr><td colspan="11" class="ctr">No items</td></tr>{% endif %}
    </tbody>
  </table>

  <div class="foot-area">
    <div class="certbox">
      <b>Certification:</b> {{company.cert_text}}<br>
      <b>Packing:</b> Export standard wooden carton / bubble wrap, palletized.<br>
      <b>Warranty:</b> 12 months against manufacturing defects (wear parts excluded).
    </div>
    <table class="total">
      <tr><td>Subtotal</td><td class="r">{{ "%.2f"|format(totals.subtotal) }} {{quote.currency}}</td></tr>
      {% if totals.freight %}<tr><td>Freight</td><td class="r">{{ "%.2f"|format(totals.freight) }} {{quote.currency}}</td></tr>{% endif %}
      {% if totals.insurance %}<tr><td>Insurance</td><td class="r">{{ "%.2f"|format(totals.insurance) }} {{quote.currency}}</td></tr>{% endif %}
      {% if totals.discount %}<tr><td>Discount ({{ "%.1f"|format(quote.discount_rate) }}%)</td><td class="r">-{{ "%.2f"|format(totals.discount) }} {{quote.currency}}</td></tr>{% endif %}
      <tr class="grand"><td>TOTAL</td><td class="r">{{ "%.2f"|format(totals.total) }} {{quote.currency}}</td></tr>
    </table>
  </div>

  <div class="terms">
    <b>Terms &amp; Conditions</b>
    <ol>
      <li>Price basis: <b>{{quote.incoterm}}</b>, port of loading: <b>{{quote.port}}</b>. Freight / insurance quoted separately unless noted.</li>
      <li>Payment: {{quote.payment_terms}}</li>
      <li>Delivery: {{quote.delivery}} ; Lead time counted from receipt of deposit and artwork confirmation.</li>
      <li>Quotation validity: {{quote.valid_days}} days from the date above.</li>
      <li>All machines are new, brand new, never used, packed in export standard cartons on wooden pallets.</li>
      <li>Any change in specification, capacity or optional accessories will affect price accordingly.</li>
      <li>Bank details will not be changed by any email; please confirm banking details by phone if you receive any suspicious request.</li>
    </ol>
  </div>

  <div class="sign">
    <div><b>{{company.name_en}}</b></div>
    <div class="sigline">Authorized Signature / Company Stamp</div>
  </div>
</div>
</body></html>
"""

TPL_SIMPLE_EN = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{{css}}</style></head>
<body>
<div class="page">
  <div class="head">
    <div class="head-left">
      <div class="brand">{{company.brand}}</div>
      <div class="cname">{{company.name_cn}} / {{company.name_en}}</div>
      <div class="cmeta">{{company.address_cn}}<br>Tel: {{company.phone}} | Hotline: {{company.hotline}}<br>Email: sales@example.com | Web: {{company.website}}</div>
    </div>
    <div class="head-right"><div class="qtitle">PRICE LIST</div>
      <table class="qmeta"><tr><td>No.</td><td>{{quote.quote_no}}</td></tr><tr><td>Date</td><td>{{quote.date}}</td></tr></table>
    </div>
  </div>
  <div class="toblock"><b>TO:</b> {{customer.company_en}} — {{customer.country or ''}}</div>
  <table class="items">
    <thead><tr><th style="width:36px">No.</th><th style="width:80px">Model</th><th>Product</th>
    <th style="width:110px">Spec</th><th style="width:40px">N.W.</th><th style="width:40px">G.W.</th>
    <th style="width:60px">Pcs</th><th style="width:76px">Unit Price</th><th style="width:80px">Amount</th></tr></thead>
    <tbody>{% for it in items %}
      <tr><td>{{loop.index}}</td><td>{{it.model}}</td>
      <td><b>{{it.name_en}}</b><br><small>{{it.name_cn}}</small></td>
      <td><small>{{it.spec_en or it.spec}}</small></td>
      <td>{{it.net_weight or ''}}</td><td>{{it.gross_weight or ''}}</td>
      <td>{{it.qty}}</td>
      <td class="r">{{ "%.2f"|format(it.price) }}</td><td class="r b">{{ "%.2f"|format(it.amount) }}</td></tr>
    {% endfor %}</tbody>
  </table>
  <table class="total" style="margin-left:auto;width:300px">
    <tr><td>Subtotal</td><td class="r">{{ "%.2f"|format(totals.subtotal) }}</td></tr>
    <tr class="grand"><td>TOTAL {{quote.incoterm}}</td><td class="r">{{ "%.2f"|format(totals.total) }} {{quote.currency}}</td></tr>
  </table>
  <div class="terms"><b>Notes:</b> {{quote.payment_terms}} | Validity: {{quote.valid_days}} days | Delivery: {{quote.delivery}}</div>
</div></body></html>
"""

TPL_CN = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{{css}}</style></head>
<body>
<div class="page">
  <div class="head">
    <div class="head-left">
      <div class="brand">{{company.brand}}</div>
      <div class="cname">{{company.name_cn}}</div>
      <div class="cmeta">{{company.address_cn}}<br>电话：{{company.phone}}　服务热线：{{company.hotline}}<br>邮箱：sales@example.com　网址：{{company.website}}</div>
    </div>
    <div class="head-right"><div class="qtitle">报价单</div>
      <table class="qmeta"><tr><td>报价单号</td><td>{{quote.quote_no}}</td></tr><tr><td>日期</td><td>{{quote.date}}</td></tr>
      <tr><td>有效期</td><td>{{quote.valid_until}}</td></tr></table></div>
  </div>
  <div class="toblock"><b>客户：</b>{{customer.company_cn or customer.company_en}}　<b>联系人：</b>{{customer.contact or ''}}　<b>国家/地区：</b>{{customer.country or ''}}</div>
  <table class="items">
    <thead><tr><th style="width:36px">序号</th><th style="width:78px">型号</th><th>品名及规格</th>
    <th style="width:96px">材质</th><th style="width:76px">外形尺寸<br>(mm)</th>
    <th style="width:44px">净重<br>(kg)</th><th style="width:44px">毛重<br>(kg)</th>
    <th style="width:36px">数量</th><th style="width:62px">单价</th><th style="width:74px">金额</th></tr></thead>
    <tbody>{% for it in items %}
      <tr><td>{{loop.index}}</td><td>{{it.model}}</td>
      <td><b>{{it.name_cn}}</b><br><small>{{it.spec}}</small>{% if it.certs %}<br><small>证书：{{it.certs}}</small>{% endif %}</td>
      <td>{{it.material or ''}}</td><td>{{it.dimension or ''}}</td>
      <td>{{it.net_weight or ''}}</td><td>{{it.gross_weight or ''}}</td>
      <td>{{it.qty}}</td><td class="r">{{ "%.2f"|format(it.price) }}</td><td class="r b">{{ "%.2f"|format(it.amount) }}</td></tr>
    {% endfor %}</tbody>
  </table>
  <table class="total" style="margin-left:auto;width:320px">
    <tr><td>合计</td><td class="r">{{ "%.2f"|format(totals.subtotal) }} {{quote.currency}}</td></tr>
    {% if totals.freight %}<tr><td>运费</td><td class="r">{{ "%.2f"|format(totals.freight) }}</td></tr>{% endif %}
    <tr class="grand"><td>总计（{{quote.incoterm}}）</td><td class="r">{{ "%.2f"|format(totals.total) }} {{quote.currency}}</td></tr>
  </table>
  <div class="terms"><b>付款方式：</b>{{quote.payment_terms}}<br><b>交货期：</b>{{quote.delivery}}　<b>贸易条款：</b>{{quote.incoterm}} {{quote.port}}<br><b>备注：</b>{{quote.remark or '无'}}</div>
  <div class="sign"><b>{{company.name_cn}}</b><br>签章：____________________</div>
</div></body></html>
"""

CSS_DEFAULT = """
@page { size: A4; margin: 12mm 10mm; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI","Microsoft YaHei",Arial,sans-serif; font-size: 11px; color:#111; margin:0; }
.page { width:100%; }
.head { display:flex; justify-content:space-between; border-bottom:2.5px solid #B8860B; padding-bottom:8px; }
.brand { font-size:22px; font-weight:800; color:#B8860B; letter-spacing:3px; }
.cname { font-size:13px; font-weight:700; margin-top:2px; }
.cmeta { font-size:10px; color:#444; line-height:1.5; margin-top:4px; }
.qtitle { font-size:20px; font-weight:800; letter-spacing:2px; text-align:right; color:#B8860B; }
.qmeta { margin-left:auto; font-size:10px; border-collapse:collapse; margin-top:6px; }
.qmeta td { padding:2px 4px; border:1px solid #ccc; }
.qmeta td:first-child { background:#f3f1ea; font-weight:700; }
.toblock { margin:8px 0; font-size:11px; line-height:1.6; }
table.items { width:100%; border-collapse:collapse; }
table.items th { background:#B8860B; color:#fff; font-size:10px; padding:5px 3px; border:1px solid #8a6508; line-height:1.25; }
table.items td { border:1px solid #bbb; padding:4px 3px; font-size:10px; vertical-align:top; }
table.items small { font-size:9px; color:#555; }
.r { text-align:right; } .b { font-weight:700; } .ctr { text-align:center; color:#888; }
.foot-area { display:flex; justify-content:space-between; margin-top:8px; align-items:flex-start; }
.certbox { font-size:9px; line-height:1.55; color:#333; width:52%; }
table.total { border-collapse:collapse; font-size:10.5px; }
table.total td { border:1px solid #bbb; padding:4px 8px; }
table.total td:first-child { background:#f7f5ee; }
table.total tr.grand td { background:#B8860B; color:#fff; font-weight:800; font-size:12px; border-color:#8a6508; }
.terms { margin-top:10px; font-size:9px; color:#333; line-height:1.6; border-top:1px solid #ccc; padding-top:6px; }
.terms ol { margin:4px 0 0 16px; padding:0; }
.sign { margin-top:14px; text-align:right; font-size:11px; }
.sigline { margin-top:22px; font-size:9px; color:#666; }
"""


def seed_all() -> dict:
    db = get_conn()
    ts = now()

    db.executemany(
        "INSERT OR IGNORE INTO categories(code,name_cn,name_en,sort_order) VALUES(?,?,?,?)",
        CATEGORIES,
    )
    db.executemany(
        "INSERT OR IGNORE INTO suppliers(code,name_cn,name_en,email,phone,remark) VALUES(?,?,?,?,?,?)",
        SUPPLIERS,
    )

    for p in PRODUCTS:
        (sku, ncn, nen, cat, model, spec, spec_en, mat, power, cap, dim, pack,
         nw, gw, pcn, pusd, moq, lead, hs, certs, desc, kw) = p
        db.execute(
            """INSERT OR IGNORE INTO products
               (sku,name_cn,name_en,category_code,supplier_code,brand,model,spec,spec_en,material,
                power,capacity,dimension,packing_size,net_weight,gross_weight,price_cny,price_usd,
                moq,lead_time,hs_code,certs,image_path,images,desc_en,keywords,source,is_active,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (sku, ncn, nen, cat, "SELF", "Felixv", model, spec, spec_en, mat, power, cap,
             dim, pack, nw, gw, pcn, pusd, moq, lead, hs, jdump(certs), "", jdump([]),
             desc, kw, "seed", ts),
        )

    # 同行/供应商扩展品类：参数体系随品类变化
    peer_supplier = {
        "HX-2T-600": "SUP-G", "YX-2B-400": "SUP-F", "TL-3T-120": "SUP-F", "ZL-30L": "SUP-F",
        "QC-360E": "SUP-H", "JR-20": "SUP-H", "GC-15": "SUP-H",
        "QC-2B": "SUP-I", "XJ-1": "SUP-I", "LC-500": "SUP-B", "LCG-1200": "SUP-B",
    }
    for p in PEER_PRODUCTS:
        (sku, ncn, nen, cat, model, spec, spec_en, mat, power, cap, dim, pack,
         nw, gw, pcn, pusd, moq, lead, hs, certs, desc, kw) = p
        db.execute(
            """INSERT OR IGNORE INTO products
               (sku,name_cn,name_en,category_code,supplier_code,brand,model,spec,spec_en,material,
                power,capacity,dimension,packing_size,net_weight,gross_weight,price_cny,price_usd,
                moq,lead_time,hs_code,certs,image_path,images,desc_en,keywords,source,is_active,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (sku, ncn, nen, cat, peer_supplier.get(sku, "SUP-A"), "OEM", model, spec, spec_en,
             mat, power, cap, dim, pack, nw, gw, pcn, pusd, moq, lead, hs, jdump(certs),
             "", jdump([]), desc, kw, "seed-peer", ts),
        )

    db.execute(
        "INSERT OR IGNORE INTO templates(key,name,lang,body_html,css,is_builtin,created_at) VALUES(?,?,?,?,?,1,?)",
        ("standard_en", "标准外贸报价单 Standard Export Quotation (EN)", "en", TPL_STANDARD_EN, CSS_DEFAULT, ts),
    )
    db.execute(
        "INSERT OR IGNORE INTO templates(key,name,lang,body_html,css,is_builtin,created_at) VALUES(?,?,?,?,?,1,?)",
        ("simple_en", "简洁价格单 Simple Price List (EN)", "en", TPL_SIMPLE_EN, CSS_DEFAULT, ts),
    )
    db.execute(
        "INSERT OR IGNORE INTO templates(key,name,lang,body_html,css,is_builtin,created_at) VALUES(?,?,?,?,?,1,?)",
        ("cn", "中文报价单 Chinese Quotation", "cn", TPL_CN, CSS_DEFAULT, ts),
    )

    if not db.execute("SELECT 1 FROM settings WHERE key='company'").fetchone():
        company = effective_company()
        company["cert_text"] = "、".join(c["code"] for c in company["certs"])
        db.execute("INSERT INTO settings(key,value) VALUES('company',?)", (jdump(company),))

    db.commit()

    # M002 岗位职级编制：懒加载 + 吞异常 —— 这是一个可关闭的普通模块，
    # 它出任何问题都不该把整个系统的启动拖挂（挂载失败只记日志，种子同理）。
    try:
        from .modules.position import seed_positions
        counts_pos = seed_positions()
    except Exception as exc:                                   # pragma: no cover
        counts_pos = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[seed] 岗位职级种子失败：{exc}")
    db = get_conn()      # seed_positions 内部释放过连接，这里重新取（线程级长连接）

    # M003 员工档案与异动：复用 M002 的 demo_staff，挂档案 + 异动。
    # 幂等、不新建人员、绝不修改 position_assignments，保护 M002 的编制/在岗/超编演示数据。
    try:
        from .modules.employee import seed_employees
        counts_emp = seed_employees()
    except Exception as exc:                                   # pragma: no cover
        counts_emp = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[seed] 员工档案种子失败：{exc}")
    db = get_conn()      # seed_employees 内部释放过连接，这里重新取

    counts = {
        "categories": db.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"],
        "products": db.execute("SELECT COUNT(*) c FROM products").fetchone()["c"],
        "suppliers": db.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"],
        "templates": db.execute("SELECT COUNT(*) c FROM templates").fetchone()["c"],
    }
    if "error" not in counts_pos:
        counts.update(counts_pos)
    else:
        counts["position_error"] = counts_pos["error"]
    if "error" not in counts_emp:
        counts.update(counts_emp)
    else:
        counts["employee_error"] = counts_emp["error"]

    # M005 审批引擎：灌示例流程（请假审批），幂等、可关闭模块失败不影响启动。
    try:
        from .modules.workflow import seed_workflows
        counts_wf = seed_workflows()
    except Exception as exc:                                   # pragma: no cover
        counts_wf = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[seed] 审批引擎种子失败：{exc}")
    db = get_conn()
    if "error" not in counts_wf:
        counts.update(counts_wf)
    else:
        counts["workflow_error"] = counts_wf["error"]

    # M071 电子报销：EXPENSE 流程定义 + 演示数据，幂等、失败不影响启动。
    try:
        from .modules.expense import seed_expense
        counts_exp = seed_expense()
    except Exception as exc:                                   # pragma: no cover
        counts_exp = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"[seed] 电子报销种子失败：{exc}")
    db = get_conn()
    if "error" not in counts_exp:
        counts.update(counts_exp)
    else:
        counts["expense_error"] = counts_exp["error"]

    # H1 启动期体检：检查所有启用流程定义里「按角色配审批人」的节点是否还有在职用户。
    # 放在所有流程定义种子之后（覆盖请假 + 报销等全部定义）。只告警、不中断启动——
    # 岗位空缺是合理业务状态，硬拦会卡死流程；目标是让人看见，不是替人决定。
    try:
        from .core import workflow as WF
        wf_warns = WF.audit_definition_health()
        if wf_warns:
            print(f"[seed] 审批引擎体检告警 {len(wf_warns)} 条（见 data/service.log）")
    except Exception as exc:                                   # pragma: no cover
        print(f"[seed] 审批引擎体检失败：{exc}")
    return counts


if __name__ == "__main__":
    from .database import init_db

    init_db()
    print("seeded:", seed_all())