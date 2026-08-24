"""ROCI source and Skill registry.

The registry is deliberately data-first.  A source claim is stored as a
claim, while the engineered rule is kept separate and is never treated as a
guaranteed trading result.  This makes the UI able to show provenance and
lets the lab promote a Skill only after an auditable validation run.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ROCI_VERSION = "roci-v1.1.2"
SKILL_STATES = (
    "KNOWLEDGE_ONLY",
    "HYPOTHESIS",
    "DETECT_ONLY",
    "SHADOW",
    "ACTIVE",
    "DEGRADED",
    "DISABLED",
)


SOURCE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"key": "retail_yi", "name": "散户乙历年发言精选与合集", "type": "knowledge"},
    {"key": "pro_dealer", "name": "职业做盘手册内容", "type": "knowledge"},
    {"key": "open_notes", "name": "打开", "type": "knowledge"},
    {"key": "wuge_notes", "name": "教学笔记-五哥", "type": "knowledge"},
    {"key": "weiran_finance", "name": "蔚然财话物料", "type": "knowledge"},
    {"key": "jiajia_finance", "name": "佳佳财经资料", "type": "knowledge"},
    {"key": "kline_atlas", "name": "神光金融研究院 K线形态大全", "type": "knowledge"},
    {"key": "jianmen_essence", "name": "剑门：股市的本质", "type": "knowledge"},
    {"key": "jianmen_models", "name": "剑门：思维模型的重要性", "type": "knowledge"},
    {"key": "jianmen_cycle", "name": "剑门：周期思维", "type": "knowledge"},
    {"key": "jianmen_leader", "name": "剑门：龙头思维", "type": "knowledge"},
    {"key": "jianmen_data", "name": "剑门：数据化思维", "type": "knowledge"},
    {"key": "jianmen_anti_fragile", "name": "剑门：反脆弱思维", "type": "knowledge"},
    {"key": "jianmen_super_short", "name": "剑门：超短及超短手法分类", "type": "knowledge"},
    {"key": "jianmen_board", "name": "剑门：何为打板及如何打板", "type": "knowledge"},
    {"key": "jianmen_limit", "name": "剑门：涨停板种类及优良筹码形态", "type": "knowledge"},
    {"key": "jianmen_monster", "name": "剑门：妖股启动的五大形态", "type": "knowledge"},
    {"key": "jianmen_topics", "name": "剑门：超短常见的五大炒作方向", "type": "knowledge"},
    {"key": "jianmen_position", "name": "剑门：个股地位、周期、题材地位判断", "type": "knowledge"},
    {"key": "jianmen_ecology", "name": "剑门：超短生态篇", "type": "knowledge"},
    {"key": "jianmen_discipline", "name": "剑门：短线操作通识与买卖纪律", "type": "knowledge"},
    {"key": "jianmen_inner", "name": "剑门：短线内功剑门悟道心法篇", "type": "knowledge"},
    {"key": "jianmen_guide", "name": "剑门：学习指引", "type": "knowledge"},
    {"key": "jianmen_rules", "name": "剑门：体系及直播间学习操作须知", "type": "knowledge"},
    {"key": "caopan_zhishen", "name": "提取自操盘之神-彩色珍藏版", "type": "knowledge"},
    {"key": "existing_v5", "name": "现有 A股 AI 前瞻预测中枢 V5.1", "type": "existing_engine"},
    {"key": "existing_v4_v5", "name": "现有 V4/V5 市场之道与多因子共振", "type": "existing_engine"},
)


def _skill(
    skill_id: str,
    name: str,
    category: str,
    source_key: str,
    source_section: str,
    claim: str,
    definition: str,
    *,
    status: str = "DETECT_ONLY",
    requirements: tuple[str, ...] = (),
    regimes: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
    weight: float | None = None,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "name": name,
        "category": category,
        "source_key": source_key,
        "source_name": next((item["name"] for item in SOURCE_DEFINITIONS if item["key"] == source_key), source_key),
        "source_section": source_section,
        "source_pages": None,
        "source_claim": claim,
        "engineered_definition": definition,
        "status": status,
        "version": ROCI_VERSION,
        "data_requirements": list(requirements),
        "applicable_regimes": list(regimes),
        "forbidden_regimes": list(forbidden),
        "default_weight": weight,
        "validation_status": "INTEGRATED_RULE" if status == "ACTIVE" else "NOT_TESTED",
    }


_ACTIVE = {"ROCI-S002", "ROCI-S003", "ROCI-S020", "ROCI-S023", "ROCI-S027", "ROCI-S028", "ROCI-S030", "ROCI-S035", "ROCI-S037", "ROCI-S038", "ROCI-S045", "ROCI-S046", "ROCI-S059", "ROCI-S063", "ROCI-S064"}
_KNOWLEDGE = {"ROCI-S005", "ROCI-S006", "ROCI-S010", "ROCI-S011", "ROCI-S013", "ROCI-S014"}


SKILL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _skill("ROCI-S001", "波动不等于风险", "认知边界", "retail_yi", "风险认知", "波动只有在无法定义失效或流动性不足时才转化为风险。", "将波动与失效距离、流动性和时间成本分开观察，不用波动本身做动作。", requirements=("daily_bars", "liquidity")),
    _skill("ROCI-S002", "前置风险识别", "风险管理", "retail_yi", "前置风险", "风险应在进入前被定义，而不是只靠事后止损。", "要求每个动作同时记录失效条件、流动性风险和数据完整度。", status="ACTIVE", requirements=("daily_bars", "liquidity", "data_quality")),
    _skill("ROCI-S003", "战略与战术分离", "决策结构", "retail_yi", "战略战术", "市场生态判断不能被单个盘中信号越级覆盖。", "先判战场和主要矛盾，再判断个股或短线战术。", status="ACTIVE", requirements=("market_regime", "contradiction")),
    _skill("ROCI-S004", "认知边界", "认知边界", "retail_yi", "不懂不做", "数据不足时应承认未知。", "任一关键输入缺失时输出 UNKNOWN，并降低置信度，不以中性数字填充。", requirements=("data_quality",)),
    _skill("ROCI-S005", "运气与幸存者偏差", "认知边界", "retail_yi", "偏差", "单个成功案例不能代表可复制优势。", "要求样本量、样本外、成本和市场状态分层验证。", status="KNOWLEDGE_ONLY"),
    _skill("ROCI-S006", "过度交易抑制", "认知边界", "retail_yi", "纪律", "没有优势时等待本身是动作。", "在无风险补偿、数据不足或无法定义失效时给出 WAIT/NO_TRADE。", status="KNOWLEDGE_ONLY"),
    _skill("ROCI-S007", "不确定性过滤", "生态", "pro_dealer", "不确定性", "不确定性过高时降低暴露。", "用证据覆盖、来源时效和冲突记录形成不确定性门槛。", requirements=("data_quality", "source_freshness")),
    _skill("ROCI-S008", "借势", "生态", "pro_dealer", "借势", "个股强度需要得到板块和市场环境支持。", "比较个股、板块和市场的相对变化，标注独立强度或 Beta 依赖。", requirements=("daily_bars", "sector_breadth")),
    _skill("ROCI-S009", "擒龙", "领导力", "pro_dealer", "龙头", "重点研究具备领导力的标的。", "以板块排名、宽度、连续性和相对强度识别领导力，不断言参与者意图。", requirements=("sector_leadership", "relative_strength")),
    _skill("ROCI-S010", "人性敌人", "认知风险", "pro_dealer", "人性", "贪婪、恐惧、侥幸和执念会改变执行质量。", "将行为风险作为独立风险类别，不把它混进市场方向分数。", status="KNOWLEDGE_ONLY"),
    _skill("ROCI-S011", "等待能力", "认知边界", "pro_dealer", "等待", "等待确认可以避免无补偿风险。", "当方向与时机、赔率或验证条件不一致时输出 WAIT。", status="KNOWLEDGE_ONLY"),
    _skill("ROCI-S012", "机会集中", "机会结构", "open_notes", "机会集中", "注意力应集中于少数高优势结构。", "按主要矛盾相关性和证据独立性排序机会，不平均分配关注度。", requirements=("contradiction", "opportunity_evidence")),
    _skill("ROCI-S013", "失败信息化", "复盘", "open_notes", "失败复盘", "失败结果应更新假设而不是只归因运气。", "保存行动、证据、失效条件和结果，形成可复盘记录。", status="KNOWLEDGE_ONLY"),
    _skill("ROCI-S014", "正循环与负循环", "风险转换", "open_notes", "循环", "风险暴露后的价格和承接反应决定循环方向。", "跟踪事件、价格、供应、相对强度和后续确认，区分吸收与恶化。", status="KNOWLEDGE_ONLY"),
    _skill("ROCI-S015", "供应测试", "供应承接", "wuge_notes", "探搓", "压力测试的结果比单次放量更重要。", "观察异常成交后的回撤深度、承接和恢复速度。", requirements=("daily_bars", "volume")),
    _skill("ROCI-S016", "量价效率", "供应承接", "wuge_notes", "量价", "价格推进与成交投入的效率可以辅助识别结构。", "计算收益变化相对成交量变化，明确标注为可观测代理。", requirements=("daily_bars", "volume")),
    _skill("ROCI-S017", "龙头抗压", "领导力", "wuge_notes", "龙头抗压", "同一板块压力下的相对抗跌具有信息价值。", "比较个股与板块收益、恢复速度和宽度。", requirements=("daily_bars", "sector_context")),
    _skill("ROCI-S018", "题材级别", "生态", "wuge_notes", "题材", "题材强度需要结合扩散、持续和事件证据。", "不以单日涨幅代替题材质量，联合宽度、资金和事件来源。", requirements=("sector_breadth", "fund_flow", "events")),
    _skill("ROCI-S019", "盘口攻防", "供应承接", "wuge_notes", "盘口", "盘口数据只说明可观察的订单行为。", "若没有完整盘口数据则 UNKNOWN，不推断主力意图。", requirements=("intraday_evidence",)),
    _skill("ROCI-S020", "竞价到开盘验证", "预期差", "weiran_finance", "竞价验证", "竞价是预期信息，必须由开盘后响应确认。", "将竞价强弱、开盘响应和板块宽度分开记录，不直接产生买入动作。", status="ACTIVE", requirements=("auction", "intraday_evidence")),
    _skill("ROCI-S021", "筹码压力", "供应承接", "wuge_notes", "筹码", "上方供应和换手结构决定突破的可持续性。", "以历史高点、成交密集代理和回撤响应估计供应压力。", requirements=("daily_bars", "volume")),
    _skill("ROCI-S022", "Auction Intelligence", "预期差", "weiran_finance", "Auction Intelligence", "竞价的价格、量和未匹配变化需要联合观察。", "仅在有明确竞价快照时计算；缺失字段保持 UNKNOWN。", requirements=("auction")),
    _skill("ROCI-S023", "弱转强与强转弱", "状态机", "weiran_finance", "强弱转换", "方向变化需要连续响应确认。", "比较前一状态、当前相对强度和后续宽度，输出状态迁移。", status="ACTIVE", requirements=("daily_bars", "sector_breadth")),
    _skill("ROCI-S024", "首板与连板条件概率", "状态机", "weiran_finance", "条件概率", "固定总体胜率不能替代状态条件概率。", "只记录可验证样本，未完成 PIT 回测时标记未验证。", requirements=("limit_board_history", "pit_backtest")),
    _skill("ROCI-S025", "盘中机会风险扫描", "盘中", "weiran_finance", "9元素扫描", "盘中信号要同时观察价格、量、板块和风险。", "把盘中证据分组展示，不合并成未经验证的推荐分数。", requirements=("intraday_evidence", "sector_context")),
    _skill("ROCI-S026", "盈亏比优先", "赔率", "weiran_finance", "盈亏比", "可定义的赔率比单一命中率更重要。", "计算上行、下行、失效距离、流动性和时间成本。", requirements=("daily_bars", "risk_levels")),
    _skill("ROCI-S027", "赚钱与亏钱效应", "生态", "jiajia_finance", "赚钱效应", "市场奖励和惩罚结构影响策略适用性。", "用涨跌停、宽度、连续性和高位负反馈描述环境。", status="ACTIVE", requirements=("breadth", "limit_board_history")),
    _skill("ROCI-S028", "分时承接", "供应承接", "jiajia_finance", "分时", "价格在均价线附近的响应可观察承接。", "有分钟线时计算 VWAP 距离和回踩响应，没有则 UNKNOWN。", status="ACTIVE", requirements=("minute_bars", "vwap")),
    _skill("ROCI-S029", "竞争假设", "认知边界", "jiajia_finance", "竞争假设", "同一机会存在替代方向和反方证据。", "为主要矛盾同时保存支持和反对证据。", requirements=("contradiction",)),
    _skill("ROCI-S030", "保护线", "风险管理", "jiajia_finance", "保护线", "风险控制应提前定义保护条件。", "把结构失效、流动性和时间窗口写入行动失效条件。", status="ACTIVE", requirements=("daily_bars", "risk_levels")),
    _skill("ROCI-S031", "涨停角色分类", "生态", "jiajia_finance", "涨停角色", "涨停行为需要结合位置和板块角色理解。", "只做角色标签和证据展示，不将涨停直接定义为买点。", requirements=("limit_board_history", "sector_context")),
    _skill("ROCI-S032", "K线原子语义", "形态", "kline_atlas", "K线原子语义", "K线是现象，不是独立交易结论。", "提取实体、影线、位置和成交变化，并等待上下文确认。", requirements=("daily_bars",)),
    _skill("ROCI-S033", "游戏规则识别", "生态", "jianmen_essence", "规则", "先识别当前市场的奖励和惩罚规则。", "用市场宽度、情绪和流动性识别生态状态。", requirements=("market_regime", "reward_punishment")),
    _skill("ROCI-S034", "条件概率思维", "验证", "jianmen_models", "条件概率", "P(Result|State)不能被总体概率替代。", "所有性能报告按生态、时间、样本外和成本分层。", requirements=("pit_backtest",)),
    _skill("ROCI-S035", "多周期环境", "周期", "jianmen_cycle", "多周期", "同一形态在不同周期含义不同。", "将 1-3日、1周、1月、季度结论分开，不外推短线情绪。", status="ACTIVE", requirements=("forecast_v5", "daily_bars")),
    _skill("ROCI-S036", "龙头思维", "领导力", "jianmen_leader", "龙头", "重点关注主线中的领导和承接。", "以板块排名、相对强度和持续性定义领导力代理。", requirements=("sector_leadership", "relative_strength")),
    _skill("ROCI-S037", "数据证据引擎", "数据质量", "jianmen_data", "数据化思维", "每个结论必须能回到数据和时间截点。", "分离 FACT、INFERENCE、SOURCE_CLAIM，并记录来源和截止时间。", status="ACTIVE", requirements=("data_quality", "source_registry")),
    _skill("ROCI-S038", "反脆弱", "压力测试", "jianmen_anti_fragile", "反脆弱思维", "压力后的恢复和强化比压力前的强弱更有信息。", "记录压力事件、相对响应、恢复速度和后续跟随。", status="ACTIVE", requirements=("stress_events", "daily_bars")),
    _skill("ROCI-S039", "确定性与赔率选择", "赔率", "jianmen_models", "确定性赔率", "确定性和赔率需要共同约束风险预算。", "不以高置信度替代可定义的下行和流动性检查。", requirements=("asymmetry",)),
    _skill("ROCI-S040", "涨停执行风险", "执行", "jianmen_board", "打板执行", "订单可成交性是策略结果的一部分。", "记录流动性、跳空、排队和不可成交风险。", requirements=("auction", "liquidity")),
    _skill("ROCI-S041", "Board Quality", "生态", "jianmen_limit", "筹码形态", "板块质量来自核心、中军和后排的共同响应。", "计算板块宽度、核心/中军确认和后排跟随，不只看龙头。", requirements=("sector_breadth", "sector_leadership")),
    _skill("ROCI-S042", "妖股机会扫描", "机会", "jianmen_monster", "妖股五形态", "高弹性形态只能作为检测对象，不能直接作为买点。", "检测龙头二波、趋势加速、多重反包、超跌启动和低阻力路径，默认 SHADOW。", status="SHADOW", requirements=("daily_bars", "sector_context")),
    _skill("ROCI-S043", "Opportunity Migration", "机会", "jianmen_topics", "机会迁徙", "机会可能从老龙、核心、补涨或低位方向迁徙。", "跟踪相对强度、资金和板块宽度的迁移，输出观察链。", requirements=("sector_leadership", "fund_flow")),
    _skill("ROCI-S044", "Identity Engine", "身份", "jianmen_position", "个股身份", "个股身份由题材、周期和板块地位共同决定。", "分离身份事实和解释，不以名称或单日涨幅替代地位。", requirements=("sector_context", "daily_bars")),
    _skill("ROCI-S045", "Topic Hierarchy", "题材", "jianmen_position", "题材层级", "题材层级影响资金和注意力的持续性。", "按主题、分支、核心和后排分层展示证据。", status="ACTIVE", requirements=("sector_leadership", "events")),
    _skill("ROCI-S046", "Ecology Regime", "生态", "jianmen_ecology", "超短生态", "策略有效性取决于生态阶段。", "将市场分为进攻、混合、防御、恐慌和修复，作为上层约束。", status="ACTIVE", requirements=("breadth", "emotion", "liquidity")),
    _skill("ROCI-S047", "Opportunity Rotation", "机会", "jianmen_ecology", "机会轮动", "机会会在生态和板块之间迁移。", "比较板块强度、资金方向和核心确认的时间序列。", requirements=("sector_history", "fund_flow")),
    _skill("ROCI-S048", "Execution Risk", "执行", "jianmen_discipline", "执行风险", "执行偏差可能让正确判断变成错误结果。", "记录时机、滑点、跳空、容量和行动偏离。", requirements=("auction", "liquidity", "user_feedback")),
    _skill("ROCI-S049", "真技术四以", "操盘之神", "caopan_zhishen", "真技术四以", "用可观察的价格、量、时、空关系描述结构。", "将资料概念拆成时间、空间、量价和位置证据。", requirements=("daily_bars", "volume")),
    _skill("ROCI-S050", "风险性与确定性二元辨证", "操盘之神", "caopan_zhishen", "风险性×确定性", "高风险不等于必然 NO_TRADE，需看补偿和可控性。", "并列展示风险、确定性、赔率和失效，不做单一风险分数决策。", requirements=("risk_pricing", "asymmetry")),
    _skill("ROCI-S051", "三类交易机会", "操盘之神", "caopan_zhishen", "交易机会", "机会分为高优势、不确定和负期望。", "按证据、赔率和失效可定义性分层。", requirements=("asymmetry", "data_quality")),
    _skill("ROCI-S052", "交易系统三层结构", "操盘之神", "caopan_zhishen", "三层结构", "信号、风险和仓位属于不同层。", "SignalLayer、RiskLayer、ManagementLayer 分开存储和解释。", requirements=("action", "risk_budget")),
    _skill("ROCI-S053", "成本解锚", "操盘之神", "caopan_zhishen", "成本解锚", "历史成本不是当前决策依据。", "用当前结构和失效条件替代买入成本锚定。", requirements=("daily_bars", "user_feedback")),
    _skill("ROCI-S054", "时空交换", "操盘之神", "caopan_zhishen", "时空交换", "等待时间和价格空间都构成机会成本。", "把验证窗口和预期空间同时纳入风险预算。", requirements=("daily_bars", "asymmetry")),
    _skill("ROCI-S055", "选时优先", "操盘之神", "caopan_zhishen", "选时", "方向正确但时机错误仍可能产生不可接受风险。", "以阶段、确认和流动性窗口约束执行时点。", requirements=("market_regime", "timing")),
    _skill("ROCI-S056", "风控三法", "操盘之神", "caopan_zhishen", "风控", "风控应覆盖买前、持有和退出。", "记录前置风险、动态保护和最终退出条件。", requirements=("risk_pricing", "action")),
    _skill("ROCI-S057", "概率与仓位双保险", "操盘之神", "caopan_zhishen", "概率仓位", "风险预算应随证据质量和赔率变化。", "用数据完整度、确定性、失效距离和流动性计算建议风险预算。", requirements=("asymmetry", "data_quality")),
    _skill("ROCI-S058", "人格与行为分离", "操盘之神", "caopan_zhishen", "人格行为", "用户行为风险不能冒充市场事实。", "单独记录 FOMO、报复交易、成本锚定等用户反馈信号。", requirements=("user_feedback",)),
    _skill("ROCI-S059", "时空趋势惯性", "操盘之神", "caopan_zhishen", "趋势惯性", "趋势需要在时间和空间上保持连续。", "计算多日方向、斜率和回撤后的延续性。", status="ACTIVE", requirements=("daily_bars",)),
    _skill("ROCI-S060", "涨跌速度与斜率", "操盘之神", "caopan_zhishen", "速度斜率", "价格变化速度会改变风险结构。", "以多窗口收益和变化率描述加速/减速，不直接预测。", requirements=("daily_bars",)),
    _skill("ROCI-S061", "量价十大原则抽象", "操盘之神", "caopan_zhishen", "量价原则", "量价组合需放回位置和阶段解释。", "记录量能异常、价格响应和位置，禁止单项定性。", requirements=("daily_bars", "volume")),
    _skill("ROCI-S062", "探搓：供应释放与压力测试", "操盘之神", "caopan_zhishen", "探搓", "供应释放后的承接结果决定结构。", "检测异常成交后的回撤、恢复和相对表现。", requirements=("daily_bars", "volume")),
    _skill("ROCI-S063", "异动：横向与纵向异常", "操盘之神", "caopan_zhishen", "异动", "横向相对异常与纵向历史异常应同时观察。", "比较个股/板块相对收益和自身成交/波动基线。", status="ACTIVE", requirements=("daily_bars", "sector_context")),
    _skill("ROCI-S064", "主动性", "操盘之神", "caopan_zhishen", "主动性", "主动价格响应需要成交和跟随确认。", "观察价格、资金、宽度和后续跟随是否共同确认。", status="ACTIVE", requirements=("daily_bars", "fund_flow", "sector_breadth")),
    _skill("ROCI-S065", "三反：共识与拥挤反身性", "操盘之神", "caopan_zhishen", "三反", "一致性过高会积累边际风险，但不等于机械反向。", "用拥挤、宽度、资金持续性和 Alpha 变化识别行为失衡。", requirements=("crowding", "breadth", "relative_strength")),
    _skill("ROCI-S066", "开悟：延续与转折状态机", "操盘之神", "caopan_zhishen", "开悟", "延续和转折需要连续状态证据。", "把价格、供给、承接和跟随组合成可回放状态机。", requirements=("daily_bars", "stress_events")),
    _skill("ROCI-S067", "事不过三", "十全武功", "caopan_zhishen", "十全武功", "重复测试结构需要等待可计算的失败/确认证据。", "检测相近阻力位的第三次测试及响应；仅 Shadow，不进入 ACTION。", status="SHADOW", requirements=("daily_bars", "resistance_tests")),
    _skill("ROCI-S068", "举重若轻", "十全武功", "caopan_zhishen", "十全武功", "更大价格推进使用相对更小成交投入可能意味着供给变化。", "比较收益推进与成交变化；仅 Shadow，不推断意图。", status="SHADOW", requirements=("daily_bars", "volume")),
    _skill("ROCI-S069", "断层回补", "十全武功", "caopan_zhishen", "十全武功", "缺口回补是结构观察对象而非自动买点。", "识别缺口、回补和回补后的跟随；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "gap")),
    _skill("ROCI-S070", "脱胎换骨", "十全武功", "caopan_zhishen", "十全武功", "位置和成交结构发生长期变化时需要重新识别身份。", "检测波动、成交、趋势和板块地位的联合转变；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "sector_context")),
    _skill("ROCI-S071", "云淡风轻", "十全武功", "caopan_zhishen", "十全武功", "波动收敛后的方向选择需要后续确认。", "检测低波动压缩和突破响应；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "volatility")),
    _skill("ROCI-S072", "涨停回吐", "十全武功", "caopan_zhishen", "十全武功", "强势后的回吐反应可用于压力测试。", "检测涨停后回吐、承接和相对恢复；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "limit_board_history")),
    _skill("ROCI-S073", "七星龙珠", "十全武功", "caopan_zhishen", "十全武功", "多条件共振只能作为待验证形态。", "检测七类独立证据是否同时出现；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "sector_context", "fund_flow")),
    _skill("ROCI-S074", "星星点灯", "十全武功", "caopan_zhishen", "十全武功", "低位逐步改善需要连续观察。", "检测低位相对强度、成交和宽度渐进改善；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "sector_breadth")),
    _skill("ROCI-S075", "对称攻击", "十全武功", "caopan_zhishen", "十全武功", "对称结构是图形假设，不是买点。", "检测上攻与回撤的几何相似性；仅 Shadow。", status="SHADOW", requirements=("daily_bars", "geometry")),
    _skill("ROCI-S076", "完美风暴", "十全武功", "caopan_zhishen", "十全武功", "多个外部和内部条件同时变化时需严格验证。", "检测事件、资金、板块、价格和波动的联合异常；仅 Shadow。", status="SHADOW", requirements=("events", "fund_flow", "daily_bars")),
    _skill("ROCI-S090", "竞价偏差识别", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "竞价实际相对昨日预期和周度剧本的偏差需要开盘响应确认。", "比较昨日收盘预期、盘前剧本和竞价实际，输出正向、负向或中性偏差；仅 Shadow。", status="SHADOW", requirements=("auction", "weekly_scenario", "intraday_evidence")),
    _skill("ROCI-S091", "开盘15分钟资金方向", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "开盘前15分钟的成交结构需要结合板块、风格和后续承接观察。", "区分开盘攻击、防御、分配、轮动、恐慌和噪声，不把主动买卖代理直接归因于账户身份；仅 Shadow。", status="SHADOW", requirements=("minute_bars", "intraday_evidence", "sector_context")),
    _skill("ROCI-S092", "盘中广度变化", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "盘中广度的速度和分歧比单点涨跌家数更有信息。", "跟踪上涨占比速度、中位数收益速度、新低和跌停加速度及与指数的背离；仅 Shadow。", status="SHADOW", requirements=("intraday_evidence", "breadth", "equal_weight")),
    _skill("ROCI-S093", "盘中领导力", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "领涨方向必须由持续性、核心和板块内部宽度共同确认。", "识别强领导、窄领导、假领导、领导轮动或无领导，不用单个涨幅替代持续性；仅 Shadow。", status="SHADOW", requirements=("sector_leadership", "intraday_evidence", "sector_breadth")),
    _skill("ROCI-S094", "盘中承接与抛压", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "承接和抛压要结合跌速、量能、低点回收和板块同步判断。", "区分卖压占优、买方吸收占优和平衡，禁止只用内外盘推断参与者意图；仅 Shadow。", status="SHADOW", requirements=("minute_bars", "volume", "equal_weight", "intraday_evidence")),
    _skill("ROCI-S095", "盘中搬家识别", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "板块迁移需要比较多个时间切片的来源、目的、强度和持续性。", "对比09:45、10:30、11:30、13:30、14:30和收盘窗口，记录资金迁移代理；仅 Shadow。", status="SHADOW", requirements=("intraday_evidence", "sector_history", "fund_flow")),
    _skill("ROCI-S096", "盘中剧本验证", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "盘中事实可以支持或反对周度剧本，但不能直接改写正式概率。", "输出基准、向上、向下和混合剧本的支持事实、矛盾证据及建议变化，收盘后再正式更新；仅 Shadow。", status="SHADOW", requirements=("weekly_scenario", "intraday_evidence", "validation")),
    _skill("ROCI-S097", "盘中异常转折", "盘中", "existing_v5", "V1.1.2 盘中实时分析", "指数、广度、领导力和成交性质的同步切换需要被记录并复核。", "识别指数广度反转、领导力失败、防御破坏、成长收复、恐慌反转、缩量和尾盘风险切换；仅 Shadow。", status="SHADOW", requirements=("intraday_evidence", "state_history", "volume")),
)


MONSTER_PATTERNS: tuple[dict[str, Any], ...] = (
    {"id": "ROCI-P-MONSTER-01", "name": "龙头二波", "category": "妖股", "source": "jianmen_monster", "definition": "前期领导标的在分歧后重新获得相对强度和板块确认。", "rule": {"needs": ["prior_leader", "relative_strength_recovery", "sector_confirmation"]}},
    {"id": "ROCI-P-MONSTER-02", "name": "趋势加速", "category": "妖股", "source": "jianmen_monster", "definition": "趋势斜率和成交响应同步增强，需排除高位拥挤。", "rule": {"needs": ["slope_acceleration", "volume_response", "crowding_check"]}},
    {"id": "ROCI-P-MONSTER-03", "name": "多重反包", "category": "妖股", "source": "jianmen_monster", "definition": "回撤后重新收复关键结构并得到后续跟随。", "rule": {"needs": ["reclaim", "follow_through", "supply_check"]}},
    {"id": "ROCI-P-MONSTER-04", "name": "超跌启动", "category": "妖股", "source": "jianmen_monster", "definition": "超跌后的相对强度和成交改善，不将低位直接当作机会。", "rule": {"needs": ["drawdown", "relative_strength", "fundamental_or_event_check"]}},
    {"id": "ROCI-P-MONSTER-05", "name": "低阻力路径（聚宝盆）", "category": "妖股", "source": "jianmen_monster", "definition": "上方供应较轻且价格响应连续的路径观察。", "rule": {"needs": ["overhead_supply", "continuation", "liquidity"]}},
)

ANTI_FRAGILE_PATTERNS: tuple[dict[str, Any], ...] = (
    {"id": "ROCI-P-ANTI-01", "name": "弱转强", "category": "反脆弱", "source": "existing_v5", "definition": "预期偏弱后实际响应改善并持续得到确认。", "rule": {"needs": ["expectation_gap_positive", "follow_through"]}},
    {"id": "ROCI-P-ANTI-02", "name": "反核", "category": "反脆弱", "source": "jianmen_anti_fragile", "definition": "极端压力后相对响应和承接改善。", "rule": {"needs": ["stress_event", "absorption", "relative_strength"]}},
    {"id": "ROCI-P-ANTI-03", "name": "炸板修复", "category": "反脆弱", "source": "existing_v5", "definition": "强势结构受压后恢复，但需排除消息和流动性缺陷。", "rule": {"needs": ["failed_limit", "recovery", "sector_confirmation"]}},
    {"id": "ROCI-P-ANTI-04", "name": "大分歧后转一致", "category": "反脆弱", "source": "jianmen_ecology", "definition": "分歧后的宽度、核心和资金重新同步。", "rule": {"needs": ["disagreement", "breadth_recovery", "core_confirmation"]}},
    {"id": "ROCI-P-ANTI-05", "name": "逆板块抗跌", "category": "反脆弱", "source": "wuge_notes", "definition": "板块承压时个股相对强度保持，但不推断原因。", "rule": {"needs": ["sector_down", "stock_relative_strength", "volume"]}},
)

BREAKOUT_PATTERNS: tuple[dict[str, Any], ...] = (
    {"id": "ROCI-P-BREAK-01", "name": "平台突破", "category": "突破", "source": "kline_atlas", "definition": "区间上沿突破并有成交和板块确认。", "rule": {"needs": ["range", "breakout", "volume_confirmation"]}},
    {"id": "ROCI-P-BREAK-02", "name": "突破回踩", "category": "突破", "source": "kline_atlas", "definition": "突破后回踩关键位不破并重新获得相对强度。", "rule": {"needs": ["breakout", "retest", "support_response"]}},
    {"id": "ROCI-P-BREAK-03", "name": "双响炮", "category": "突破", "source": "kline_atlas", "definition": "两次有效推进之间存在可验证的结构承接。", "rule": {"needs": ["two_impulses", "volume_structure", "invalidation"]}},
    {"id": "ROCI-P-BREAK-04", "name": "N波", "category": "突破", "source": "kline_atlas", "definition": "多段推进和回撤形成连续路径，不作为自动交易信号。", "rule": {"needs": ["swing_sequence", "higher_lows", "follow_through"]}},
    {"id": "ROCI-P-BREAK-05", "name": "试盘供应消化", "category": "突破", "source": "caopan_zhishen", "definition": "测试压力后的供应释放和承接改善。", "rule": {"needs": ["supply_test", "absorption", "relative_strength"]}},
)

MIGRATION_PATTERNS: tuple[dict[str, Any], ...] = (
    {"id": "ROCI-P-MIGRATE-01", "name": "老龙反弹", "category": "机会迁徙", "source": "jianmen_topics", "definition": "旧领导标的在新环境中重新获得关注的观察结构。", "rule": {"needs": ["prior_leader", "new_flow", "sector_context"]}},
    {"id": "ROCI-P-MIGRATE-02", "name": "低位迁徙", "category": "机会迁徙", "source": "jianmen_ecology", "definition": "高位拥挤后资金和相对强度向低位方向迁移。", "rule": {"needs": ["crowding_decay", "low_position_strength", "flow_shift"]}},
    {"id": "ROCI-P-MIGRATE-03", "name": "超跌低价观察", "category": "机会迁徙", "source": "jianmen_monster", "definition": "低价超跌只作为筛查条件，必须有承接和风险边界。", "rule": {"needs": ["drawdown", "absorption", "liquidity"]}},
    {"id": "ROCI-P-MIGRATE-04", "name": "次新迁徙", "category": "机会迁徙", "source": "jianmen_topics", "definition": "次新方向的相对强度和供给结构观察。", "rule": {"needs": ["listing_age", "relative_strength", "supply"]}},
    {"id": "ROCI-P-MIGRATE-05", "name": "重组叙事迁徙", "category": "机会迁徙", "source": "jianmen_topics", "definition": "事件叙事需要公开证据和价格确认，不能只凭传闻。", "rule": {"needs": ["public_event", "price_confirmation", "risk_pricing"]}},
)


def all_skill_definitions() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in SKILL_DEFINITIONS]


def all_pattern_definitions() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in (*MONSTER_PATTERNS, *ANTI_FRAGILE_PATTERNS, *BREAKOUT_PATTERNS, *MIGRATION_PATTERNS):
        result.append({**deepcopy(item), "status": "DETECT_ONLY", "version": ROCI_VERSION})
    for skill in SKILL_DEFINITIONS:
        skill_number = int(skill["skill_id"].split("S")[-1])
        if 67 <= skill_number <= 76:
            result.append({
                "id": skill["skill_id"],
                "name": skill["name"],
                "category": "十全武功",
                "source": skill["source_key"],
                "definition": skill["engineered_definition"],
                "rule": {"skill_id": skill["skill_id"], "needs": skill["data_requirements"]},
                "status": "SHADOW",
                "version": ROCI_VERSION,
            })
    return result


def skill_by_id(skill_id: str) -> dict[str, Any] | None:
    return next((deepcopy(item) for item in SKILL_DEFINITIONS if item["skill_id"] == skill_id), None)
