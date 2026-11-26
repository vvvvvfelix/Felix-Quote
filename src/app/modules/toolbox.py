"""模块层 — 岗位工具箱。

把「每个岗位在生产过程中会用到的外部软件/系统」结构化沉淀下来，
作为集成规划的单一事实来源，前端只读展示。

集成状态取值：
- integrated  本系统已内置对应能力（如报价、订单、审批）
- bridge      可通过导入/导出/对接与本系统互通（如金蝶凭证、CAD 图纸）
- planned     路线图已规划，待实现（如与税务 UKey、携程商旅对接）
"""
from fastapi import APIRouter, Depends

from ..auth import current_user

router = APIRouter(tags=["toolbox"])

# 岗位 → 常用外部软件 / 系统，以及本系统的对应关系
POSITIONS = [
    {
        "role": "董事长 / 总经理", "scope": "集团决策",
        "tools": [
            {"name": "经营看板", "cat": "内部", "status": "integrated",
             "note": "多组织合并报表、利润中心、现金流总览"},
            {"name": "审批中心", "cat": "内部", "status": "integrated",
             "note": "跨公司重大合同/付款/投资一键审批"},
            {"name": "Office / WPS", "cat": "办公", "status": "bridge",
             "note": "董事会决议、战略 PPT 可导入资料库协同"},
        ],
    },
    {
        "role": "厂长 / 生产经理", "scope": "工厂运营",
        "tools": [
            {"name": "生产工单(MES)", "cat": "内部", "status": "planned",
             "note": "工序、报工、设备 OEE；当前用「生产看板」过渡"},
            {"name": "CAD / SolidWorks / AutoCAD", "cat": "研发设计", "status": "bridge",
             "note": "图纸作为产品资料导入，关联 BOM 与工艺"},
            {"name": "物料需求", "cat": "内部", "status": "integrated",
             "note": "订单→采购→生产进度联动"},
        ],
    },
    {
        "role": "研发 / 技术工程师", "scope": "产品与技术",
        "tools": [
            {"name": "CAD / EDA", "cat": "设计", "status": "bridge",
             "note": "图纸/原理图导入资料库，带版本与水印"},
            {"name": "产品主数据", "cat": "内部", "status": "integrated",
             "note": "参数化产品库，支持品类专属参数模板"},
            {"name": "Office / WPS", "cat": "办公", "status": "bridge",
             "note": "规格书、说明书模板可一键套用"},
        ],
    },
    {
        "role": "财务", "scope": "财务与税务",
        "tools": [
            {"name": "金蝶 / 用友", "cat": "财务软件", "status": "bridge",
             "note": "应收台账可导出凭证模板，后续规划直连接口"},
            {"name": "电子税务局 / 税务 UKey", "cat": "税务", "status": "planned",
             "note": "发票抬头、税号复用；全电发票查验规划中"},
            {"name": "发票打印", "cat": "内部", "status": "integrated",
             "note": "A4 双联(A5×2)排版，支持无票水单报销"},
            {"name": "Excel / WPS 表格", "cat": "办公", "status": "bridge",
             "note": "应收、对账、报表一键导出 xlsx"},
        ],
    },
    {
        "role": "行政 / 人事", "scope": "后勤与组织",
        "tools": [
            {"name": "携程商旅 / 滴滴企业版", "cat": "差旅", "status": "planned",
             "note": "行程单/水单导入后自动排版报销，减少手工贴票"},
            {"name": "人事档案", "cat": "内部", "status": "integrated",
             "note": "员工生命周期、合同、证件到期提醒"},
            {"name": "Office / WPS", "cat": "办公", "status": "bridge",
             "note": "制度、通知、合同模板统一归档"},
        ],
    },
    {
        "role": "销售（国内/国际事业部）", "scope": "市场与履约",
        "tools": [
            {"name": "报价单生成", "cat": "内部", "status": "integrated",
             "note": "多语言报价、客户模板、利润核算"},
            {"name": "CRM / 邮件", "cat": "内部", "status": "integrated",
             "note": "询盘、客户跟进、邮件留痕"},
            {"name": "WhatsApp / 企业微信", "cat": "沟通", "status": "planned",
             "note": "消息对接规划中，当前用导出跟进表"},
        ],
    },
    {
        "role": "物流 / 关务", "scope": "出货与合规",
        "tools": [
            {"name": "关务系统 / 单一窗口", "cat": "报关", "status": "planned",
             "note": "报关要素复用产品主数据，减少重复录入"},
            {"name": "船公司 / 货代平台", "cat": "运输", "status": "bridge",
             "note": "运单、箱单发票导出对接"},
            {"name": "送货单打印", "cat": "内部", "status": "integrated",
             "note": "订单直接生成带签收栏的送货单"},
        ],
    },
    {
        "role": "采购", "scope": "供应链",
        "tools": [
            {"name": "供应商协同(SRM)", "cat": "内部", "status": "planned",
             "note": "当前用供应商主数据 + 采购单过渡"},
            {"name": "Excel / WPS", "cat": "办公", "status": "bridge",
             "note": "比价表、询价单导入导出"},
        ],
    },
]


@router.get("/api/toolbox")
def api_toolbox(u: dict = Depends(current_user)):
    """岗位工具箱映射 —— 前端只读展示。"""
    return {"positions": POSITIONS,
            "legend": {"integrated": "已内置", "bridge": "可互通", "planned": "规划中"}}
