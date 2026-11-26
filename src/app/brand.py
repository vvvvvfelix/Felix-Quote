"""软件归属与法律声明 — 唯一事实来源。

所有对外可见的署名、版本、构建指纹、免责声明都从这里取值，
保证前端、关于页、导出文件、授权书、版本说明中的归属信息完全一致。
"""
import hashlib
import os
import platform
import sys
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ==========================================================================
# 一、著作权归属
# ==========================================================================
OWNER = "Felix"                     # 软件著作权人 / 作者
OWNER_ID = "WV-2026-FELIX-0001"     # 权属登记号（用于文书、授权书、对账）
COPYRIGHT_YEAR = "2026"
COPYRIGHT_HOLDER = f"Copyright (C) {COPYRIGHT_YEAR} {OWNER}. All Rights Reserved."
OWNER_ROLE = "软件著作权人 / 独立开发者 / 系统设计者"
OWNER_CONTACT = "Felix（本人）"

PRODUCT_NAME_CN = "Felix 企业管理平台"
PRODUCT_NAME_EN = "Felix Enterprise Management Platform"
PRODUCT_ABBR = "Felix 企业管理平台"
COMPANY_CN = "菲力克斯科技（广州）有限公司"
COMPANY_EN = "Felix Technology (Guangzhou) Co., Ltd."
BRAND = "Felixv"

VERSION = "2.0.0"
APP_TITLE = f"{PRODUCT_ABBR} v{VERSION}"

# ==========================================================================
# 一之二、可配置署名（M006）
# ==========================================================================
# 仅这 5 个键开放给管理员配置（见 docs/09 §0 / §2.2）：交付不同客户时产品名与公司名
# 要能改成客户公司名。owner / owner_id 是著作权人信息（L3 权属署名），**不开放**——
# 允许客户把权属号改掉会让归属举证失效。
#
# 取值规则：优先读 settings（管理员可改），读不到 / 库未就绪则回落常量默认值，
# 绝不把系统起成空壳（沿用 registry.is_enabled 的口径）。
_BRAND_KEYS = {
    "product_name_cn": ("brand:product_name_cn", PRODUCT_NAME_CN),
    "product_name_en": ("brand:product_name_en", PRODUCT_NAME_EN),
    "company_cn":      ("brand:company_cn", COMPANY_CN),
    "company_en":      ("brand:company_en", COMPANY_EN),
    "brand_token":     ("brand:brand_token", BRAND),
}


def effective_brand() -> dict:
    """返回当前生效的署名/品牌值：优先读 settings（管理员可改），缺省回落常量默认值。

    getter 绝不在 import 期碰数据库——装配期连接尚未建立，且 brand → database 会引入
    循环依赖。运行期（关于页 / 设置页 / 导出文件）调用时库已就绪，读不到则回落常量，
    绝不抛异常（否则启动 / 关于页都崩）。
    """
    from .database import get_setting
    out = {}
    for k, (sk, default) in _BRAND_KEYS.items():
        out[k] = get_setting(sk, default)
    return out


def brand_defaults() -> dict:
    """出厂默认值（恒为常量，**绝不参与指纹**）。

    设置页用它告知每个输入框「默认：Felix 企业管理平台」，让用户改完知道怎么改回。
    若把它也算进 build_fingerprint 的哈希，指纹就退化成「只对源文件取值」，客户改了
    署名指纹仍不变 —— §2.2 ⑥ 的改造当场作废。故 defaults 与生效值严格分离。
    """
    return {k: default for k, (_sk, default) in _BRAND_KEYS.items()}

# ==========================================================================
# 二、责任声明（免责）
# ==========================================================================
DISCLAIMER_ITEMS = [
    ("软件性质", "本软件为报价单制作工具，所有计算、报价、模板填充均由使用者自行录入与确认，"
                "软件不参与任何商务决策，也不代替使用者做出价格承诺。"),
    ("数据安全", "本软件的数据全部保存在使用者本机磁盘（data 目录），不上传云端。"
                "使用者应自行定期备份；因磁盘故障、误删、病毒、断电造成的数据丢失，作者不承担赔偿责任。"),
    ("报价责任", "本软件输出的报价单、PDF、Excel、Word 文件仅供业务参考。"
                "价格、汇率、交期、认证、条款的最终确认权在使用者与客户之间，"
                "由此产生的任何纠纷与索赔，作者不承担责任。"),
    ("合规责任", "使用者须确保所录入的产品参数、认证信息、图片、报关要素真实合规，"
                "并遵守所在国家/地区及客户所在地的法律法规；违法使用产生的后果由使用者自行承担。"),
    ("知识产权", "使用者保证上传的客户模板、产品资料、图片不侵犯第三方知识产权；"
                "因上传内容侵权引发的索赔由上传者承担。"),
    ("担保免责", "本软件按「现状」提供。作者已尽合理注意义务，但不对软件的绝对可用性、"
                "无中断运行或绝对无错误作出任何明示或默示担保。"),
    ("使用许可", "授权人本人及经授权的使用者享有使用权。未经著作权人书面许可，"
                "不得复制、二次分发、转售、破解、去除署名或用于任何商业再授权行为。"),
    ("争议解决", "因本软件产生的争议，授权人保留全部追责权利，并可要求停止侵权、赔偿损失。"),
]

LICENSE_NOTICE = (
    f"本软件著作权归{OWNER}（{OWNER_ID}）所有，{COPYRIGHT_HOLDER}\n"
    f"未经书面授权，任何单位或个人不得复制、传播、转售或用于商业用途。"
)


# ==========================================================================
# 三、构建指纹 —— 用于证明"这个文件确实出自 Felix"
# ==========================================================================
def _file_sha(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def build_fingerprint() -> dict:
    """对核心程序文件计算 SHA256，并聚合当前生效的署名/品牌值，可对外公示作为归属举证。

    M006 改造要点：品牌/公司名改成了可配置（settings），若只聚合源文件 SHA，客户改了
    署名指纹仍不变，这个指纹对外公示就失去意义。故额外把**生效值**并入哈希。

    硬约束（docs/09 §2.2 ⑥）：读 settings 失败不得抛异常导致启动失败——这里通过
    effective_brand() 内的 get_setting 兜底回落常量实现，读不到也照样算出指纹。
    **brand_defaults（常量）绝不参与**，否则指纹退化、改造白做。
    """
    cores = [
        os.path.join(BASE_DIR, "app", "main.py"),
        os.path.join(BASE_DIR, "app", "brand.py"),
        os.path.join(BASE_DIR, "app", "client_tpl.py"),
        os.path.join(BASE_DIR, "app", "quote.py"),
        os.path.join(BASE_DIR, "app", "static", "js", "app.js"),
    ]
    files = []
    agg = hashlib.sha256()
    for p in cores:
        if not os.path.exists(p):
            continue
        h = _file_sha(p)
        files.append({"name": os.path.basename(p), "sha256": h})
        agg.update(h.encode("ascii", "ignore"))
    # 聚合当前生效的署名/品牌值（让「改署名 → 指纹变」成立）
    for v in effective_brand().values():
        agg.update(v.encode("utf-8", "ignore"))
    return {
        "core_sha256": agg.hexdigest(),
        "files": files,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.machine()}",
    }


def install_id() -> str:
    """单机安装指纹：首次启动生成后固定，用于授权追溯（不是用户身份采集，仅本机随机数）。"""
    from .database import get_conn, now
    db = get_conn()
    r = db.execute("SELECT value FROM settings WHERE key='install_id'").fetchone()
    if r and r["value"]:
        return r["value"]
    val = "INST-" + uuid.uuid4().hex[:16].upper()
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('install_id',?)", (val,))
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('installed_at',?)", (now(),))
    db.commit()
    return val


def about_payload() -> dict:
    fp = build_fingerprint()
    eb = effective_brand()
    return {
        "owner": OWNER,
        "owner_id": OWNER_ID,
        "owner_role": OWNER_ROLE,
        "copyright": COPYRIGHT_HOLDER,
        "year": COPYRIGHT_YEAR,
        "product_cn": eb["product_name_cn"],
        "product_en": eb["product_name_en"],
        "abbr": PRODUCT_ABBR,
        "version": VERSION,
        "title": f"{eb['product_name_cn']} v{VERSION}",
        "company_cn": eb["company_cn"],
        "company_en": eb["company_en"],
        "brand": eb["brand_token"],
        "build_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "install_id": install_id(),
        "fingerprint": fp,
        "license": LICENSE_NOTICE,
        "disclaimer": [{"title": t, "text": c} for t, c in DISCLAIMER_ITEMS],
        # 出厂默认（恒为常量，不参与指纹）：设置页据此显示「默认：…」让人改回。
        # 这是公开接口、默认值是出厂公开信息，挂在此处不新增泄露面（见 docs/09 §2.2 ⑥）。
        "brand_defaults": brand_defaults(),
    }
