"""十三席注册表与能力边界（SEAT-001 / SEAT-004）。

注册表固定 13 条，seat_id 用 skills/ 下的完整技能目录 ID；legacy_seat_id 只用于旧接口映射。
- SeatDefinition（契约形状）由 registry_payload() 生成，进入不可变的 seat_registry_versions；
- CAPABILITIES 是每席的「能做什么 / 需要什么 / 哪些条件不可判定」，供编排、适用性判断、
  能力表文档与前端席位库使用；它是对技能源码与原书核对后的工程结论，不是作者原话。
人物名只是方法标签，不表示作者本人参与或授权背书；来源存在不能证明实现忠实，
实现可运行也不能证明有收益优势。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .canonical import content_hash

REGISTRY_SCHEMA_VERSION = "1.0.0"

# 规则版本：席位规则/阈值变化必须升版本，旧结果不重写
RULE_VERSIONS = {
    "trading-wildman-v2": "wildman-v2-20261017",
    "trading-jianmen-v1": "jianmen-v1-20261019",
    "trading-yangnan-v1": "yangnan-v1-20261019",
    "trading-yangjia-v1": "yangjia-v1-20261019",
    "trading-tuixue-v1": "tuixue-v1-20261019",
    "trading-gushiguifei-v1": "gushiguifei-v3-20261019",
    "trading-wyckoff-v1": "wyckoff-v1-20261019",
    "trading-candle-v1": "candle-v1-20261019",
    "trading-livermore-v1": "livermore-v2-20261019",
    "trading-darwin-v1": "darwin-v1-20261019",
    "trading-boardtech-v1": "boardtech-v1-20261019",
    "trading-chenkai-v1": "chenkai-v2-20261019",     # P0：4 日线≠小时线，三周期候选默认阻断
    "trading-lidaxiao-v1": "lidaxiao-v1-20261019",
}

# (seat_id, legacy_seat_id, 名称, role, horizons, capabilities, required_inputs)
_SEATS = [
    ("trading-wildman-v2", "wildman", "野人哥", "mixed", ["short", "swing"],
     ["context", "review", "challenge"], ["daily_history_70", "limit_pool_session", "industry_map"]),
    ("trading-jianmen-v1", "jianmen", "剑门", "selector", ["short"],
     ["screen", "context", "challenge"], ["daily_history_70", "limit_pool_session", "industry_map"]),
    ("trading-yangnan-v1", "yangnan", "杨楠", "selector", ["short"],
     ["screen", "context", "challenge"], ["daily_history_70", "limit_pool_session", "industry_map"]),
    ("trading-yangjia-v1", "yangjia", "炒股养家", "context", ["short"],
     ["context", "challenge"], ["daily_history_70"]),
    ("trading-tuixue-v1", "tuixue", "退学炒股", "reviewer", ["short", "swing"],
     ["review", "challenge"], ["decision_plan", "journal_events"]),
    ("trading-gushiguifei-v1", "gushiguifei", "一路奔行", "selector", ["swing"],
     ["screen", "confirm", "challenge"], ["daily_history_70", "industry_map"]),
    ("trading-wyckoff-v1", "wyckoff", "威科夫", "mixed", ["swing"],
     ["screen", "confirm", "challenge"], ["daily_history_70"]),
    ("trading-candle-v1", "candle", "蜡烛图", "confirmer", ["short", "swing"],
     ["confirm", "challenge"], ["daily_history_70"]),
    ("trading-livermore-v1", "livermore", "利弗莫尔", "selector", ["short", "swing"],
     ["screen", "challenge"], ["daily_history_70", "industry_map"]),
    ("trading-darwin-v1", "darwin", "达尔文", "selector", ["long"],
     ["screen", "challenge"], ["valuation_session", "dividend_history", "industry_map"]),
    ("trading-boardtech-v1", "boardtech", "明发·阿荣", "selector", ["short"],
     ["screen", "challenge"], ["daily_history_70", "limit_pool_session", "limit_events_session", "industry_map"]),
    ("trading-chenkai-v1", "chenkai", "陈凯", "context", ["swing"],
     ["context", "challenge"], ["daily_history_70", "hourly_bars"]),
    ("trading-lidaxiao-v1", "lidaxiao", "李大霄", "selector", ["long"],
     ["screen", "context", "challenge"], ["valuation_session", "industry_map"]),
]

# 每席能力边界（SEAT-004 的机器可读版本）。data_levels：A=日线/日终，B=分钟，C=Level2/逐笔
CAPABILITIES: dict[str, dict] = {
    "trading-wildman-v2": {
        "job": "周期定位、主线和个股角色（空间龙/先锋/中军/补涨/穿越）、次日预期与退出",
        "script": "scripts/build_features.py + scripts/screen.py",
        "outputs": "周期阶段与角色标注；不直接出买入候选",
        "a_level": ["情绪周期阶段（日线宽度/连板高度）", "主线行业聚集度", "个股角色（按连板与涨幅排序）"],
        "b_level": ["分时承接、分歧转一致的盘中过程", "盘中弱转强确认"],
        "c_level": ["竞价抢筹意图", "封单撤单节奏"],
        "undecidable": ["盘口与分时条件一律待核", "编者口述的统计数字不作已验证事实"],
        "deviations": ["周期阶段阈值为本技能工程设定，原书只有定性描述"],
        "source": "野人哥《保住本金是第一原则，慢就是快》完整超短体系（48 页笔录 + 新版资料）",
    },
    "trading-jianmen-v1": {
        "job": "连板生态三档定位（强/中/弱）、个股地位排序、接力条件",
        "script": "scripts/screen.py",
        "outputs": "生态闸门 + 候选（地位、买点、取消、防守）",
        "a_level": ["最高板高度", "连板家数（5 日均）", "封板家数", "地位排序（连板高度）"],
        "b_level": ["打板四手法（扫板/排板/打回封）", "分时卖点"],
        "c_level": ["封单倍数与撤单"],
        "undecidable": ["打板手法与盘口细节待核", "封单倍数不可由日线推得"],
        "deviations": ["生态三档阈值来自公开课件的流通部分，非完整付费课程"],
        "source": "剑门超短体系 20 份课件公开流通部分",
    },
    "trading-yangnan-v1": {
        "job": "情绪周期、共振冰点识别、板块运动模式与龙头确认",
        "script": "scripts/screen.py",
        "outputs": "冰点闸门 + 龙头候选（冰点时）",
        "a_level": ["四个情绪指标（涨停数、连板、炸板率、昨日涨停表现）", "冰点识别"],
        "b_level": ["分时承接", "龙头分时确认"],
        "c_level": [],
        "undecidable": ["书中胜率数字无样本，一律不采纳"],
        "deviations": ["两次冰点间隔窗口书中未定义，本技能取 60 个交易日"],
        "source": "杨楠《情绪流龙头战法》（企业管理出版社 2020）",
    },
    "trading-yangjia-v1": {
        "job": "赚钱/亏钱效应传导、试错环境与仓位递进",
        "script": "scripts/screen.py",
        "outputs": "赚钱效应闸门（环境），不出票",
        "a_level": ["晋级率", "昨日涨停今日表现", "亏钱效应扩散"],
        "b_level": ["分时承接"],
        "c_level": [],
        "undecidable": ["「赢面」「人气」原文无数值定义，不能完全量化"],
        "deviations": ["四指标阈值为本技能设定，原文未给出"],
        "source": "炒股养家《干货合集》（62 页网络整理稿）",
    },
    "trading-tuixue-v1": {
        "job": "计划完整性与行为偏离审查（模式四要件 + 执行纪律），不产出行情方向",
        "script": "scripts/review.py（审查，不筛选）",
        "outputs": "对计划/事件的审查结论；不产候选",
        "a_level": ["计划四要件是否齐全", "动作是否在事前定义的模式内"],
        "b_level": [],
        "c_level": [],
        "undecidable": ["没有事前规则时合规性为 unknown，不能判违规", "不诊断心理状态"],
        "deviations": ["原文为编者重构稿，凡具体手法均标编者转述"],
        "source": "《退学炒股超短打板的心法与精髓》（171 页，编者重构稿）",
    },
    "trading-gushiguifei-v1": {
        "job": "强势股 A/B/C 区域、量能是建仓还是派发、均线归位后的再机",
        "script": "scripts/screen.py",
        "outputs": "区域闸门 + 候选（回踩/突破双规则、止损）",
        "a_level": ["A/B/C 区域划分", "量能倍数", "均线归位"],
        "b_level": ["异动真假、封板过程、承接"],
        "c_level": [],
        "undecidable": ["分时盘口不判定", "题材地位需要概念成员与新闻（未接入）"],
        "deviations": ["「量时空」大压区间只用均线归位近似", "三阳控三阴单一规则已被实测否定，不作独立信号"],
        "source": "一路奔行《股是股非》三册（735 页）",
    },
    "trading-wyckoff-v1": {
        "job": "供需事件（SOS/SOW/Spring）、区间、突破与测试反证",
        "script": "scripts/screen.py",
        "outputs": "事件统计闸门 + 候选（全部检查通过才出）",
        "a_level": ["SOS/SOW 事件", "努力与结局量价背离", "回调缩量"],
        "b_level": [],
        "c_level": [],
        "undecidable": ["点数图目标不可得，不输出目标价", "区间内的实时判定易错，只在事件发生后动手"],
        "deviations": ["缩量不能直接等同吸筹完成，需事件确认"],
        "source": "孟洪涛《威科夫操盘法》",
    },
    "trading-candle-v1": {
        "job": "K 线位置、组合与后续确认（确认器，不独立出票）",
        "script": "scripts/screen.py",
        "outputs": "形态统计与确认；不出独立买入候选",
        "a_level": ["单根/双根/三根形态", "位置（高位/低位）"],
        "b_level": [],
        "c_level": [],
        "undecidable": ["形态只作确认，不作单独买入依据"],
        "deviations": ["形态阈值为本技能设定，非原书给出；作者依据按当前书源校准为丁圣元译本"],
        "source": "丁圣元《日本蜡烛图技术》",
    },
    "trading-livermore-v1": {
        "job": "关键点、领头羊、六栏趋势记录与顺势验证（时机问题，不是选股问题）",
        "script": "scripts/screen.py（+ record.py 六栏记录）",
        "outputs": "关键点闸门 + 候选（关键点入场、跌破即错、不设目标价）",
        "a_level": ["近 60 日高点/20 日箱体上沿作关键点", "板块最强者（领头羊）", "六栏状态"],
        "b_level": ["盘中突破的即时确认"],
        "c_level": [],
        "undecidable": ["盘中突破确认待核"],
        "deviations": ["关键点为定性描述，用近 60 日高点与 20 日箱体实现", "原书点数换算成百分比属工程重标定"],
        "source": "利弗莫尔《股票大作手操盘术》《股票大作手回忆录》",
    },
    "trading-darwin-v1": {
        "job": "企业质量、长期风险与估值（长期分支，不参加短线情绪评分）",
        "script": "scripts/screen.py",
        "outputs": "长期持有池候选（排除法 + 质量代理）",
        "a_level": ["估值（PE/PB/股息率）", "连续分红年数（当前表）"],
        "b_level": [],
        "c_level": [],
        "undecidable": ["ROCE 需 EBIT 与投入资本，以 ROE/分红代理", "竞争优势、客户集中度、管理层稳定性无法自动计算"],
        "deviations": ["连续分红年数只有当前一份：用于历史回放会前视，严格回放中不可用"],
        "source": "Pulak Prasad《我从达尔文那里学到的投资知识》（2023）",
    },
    "trading-boardtech-v1": {
        "job": "板股候选、封板质量与可成交性（断板即卖，不设价格止损）",
        "script": "scripts/screen.py",
        "outputs": "涨停候选（确定性、封板时间、开板次数、封单额）",
        "a_level": ["连板梯队", "换手", "首封时间/开板次数/封单额（来自供应商异动事件）"],
        "b_level": ["分时封板路径（一字/秒封/流畅/多波次/烂板）"],
        "c_level": ["排板与撤单"],
        "undecidable": ["分时封板路径只能按首封时间与开板次数粗分类", "龙虎榜为盘后信息，不用于当日决策"],
        "deviations": ["题材级别与新鲜度用同行业涨停家数近似"],
        "source": "明发、阿荣《图解打板技术》（中国经济出版社 2025）",
    },
    "trading-chenkai-v1": {
        "job": "多周期、背离与计划一致性；三周期信号在 P0 校准后默认阻断",
        "script": "scripts/screen.py（TRIPLE_CYCLE_BLOCKED=True）",
        "outputs": "周/日线方向与 DIF 背离的日线级解释；不出三周期候选",
        "a_level": ["周线方向", "日线回踩", "DIF 连续两日走低的顶背离"],
        "b_level": ["小时线级别的小周期确认（原书要求）"],
        "c_level": ["分时稳住确认（第六章）"],
        "undecidable": ["小时线共振不可判定——4 日线比日线更粗，不能冒充小时线", "交易计划与单笔风险预算不是行情数据"],
        "deviations": ["旧版用 4 日线替代小时线的结果标为旧版、不可比"],
        "source": "陈凯《交易之路：如何构建属于自己的投资体系》（2019）",
    },
    "trading-lidaxiao-v1": {
        "job": "估值环境（钻石底/婴儿底/常态/过热/地球顶）与蓝筹龙头研究",
        "script": "scripts/screen.py",
        "outputs": "估值闸门 + 蓝筹候选（底部区域才开）",
        "a_level": ["整体法 PE/PB", "破净率", "大市值 + 低估值 + 高股息 + 行业龙头"],
        "b_level": [],
        "c_level": [],
        "undecidable": ["四大风向标（增减持、中石油、汇金、社保）无数据接口", "股债差需要国债收益率（未接入）"],
        "deviations": ["市场估值分位阈值为本技能设定；几日涨跌不能证明估值拐点"],
        "source": "李大霄《李大霄投资战略》第三版（经济日报出版社 2025）",
    },
}


def seat_ids() -> list[str]:
    return [s[0] for s in _SEATS]


LEGACY_MAP = {s[1]: s[0] for s in _SEATS}            # wildman → trading-wildman-v2
CANONICAL_TO_LEGACY = {s[0]: s[1] for s in _SEATS}
SEAT_NAMES = {s[0]: s[2] for s in _SEATS}


_HASH_CACHE: dict[str, tuple[tuple, str]] = {}


def skill_dir_sha256(skills_dir: Path, seat_id: str) -> str:
    """技能目录摘要：对目录内全部文件（排除 __pycache__）的 (相对路径, sha256) 清单做 canonical 哈希。
    以 (路径, 大小, mtime) 签名缓存；文件一变就重算。"""
    root = skills_dir / seat_id
    if not root.is_dir():
        return "0" * 64
    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc")]
    sig = tuple((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    hit = _HASH_CACHE.get(str(root))
    if hit and hit[0] == sig:
        return hit[1]
    items = [[p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()] for p in files]
    h = content_hash(items)
    _HASH_CACHE[str(root)] = (sig, h)
    return h


def definitions(skills_dir: Path) -> list[dict]:
    out = []
    for order, (sid, legacy, name, role, horizons, caps, req) in enumerate(_SEATS, start=1):
        out.append({
            "seat_id": sid,
            "legacy_seat_id": legacy,
            "name": name,
            "skill_path": f"skills/{sid}",
            "skill_sha256": skill_dir_sha256(skills_dir, sid),
            "rule_version": RULE_VERSIONS[sid],
            "role": role,
            "horizons": list(horizons),
            "capabilities": list(caps),
            "required_inputs": list(req),
            "unavailable_behavior": "unknown",
            "enabled": (skills_dir / sid).is_dir(),
            "registry_order": order,
        })
    return out


def registry_payload(skills_dir: Path) -> dict:
    seats = definitions(skills_dir)
    if len(seats) != 13 or len({s["seat_id"] for s in seats}) != 13:
        raise AssertionError("注册表必须恰好 13 个不重复席位")
    fp = content_hash(seats)
    return {"schema_version": REGISTRY_SCHEMA_VERSION,
            "registry_version": f"registry_v1_{fp[:16]}",
            "fingerprint": fp, "seats": seats}


def capability(seat_id: str) -> dict:
    return CAPABILITIES[seat_id]


def produces_candidates(seat_id: str, caps: list[str] | None = None) -> bool:
    caps = caps if caps is not None else next(s[5] for s in _SEATS if s[0] == seat_id)
    return "screen" in caps


# 适用性判定用的「硬」必需输入：缺了结论只能是 unknown；其余 required_inputs 缺失只降级说明
_HARD = {
    "trading-wildman-v2": ("daily_history_70", "limit_pool_session"),
    "trading-jianmen-v1": ("daily_history_70",),
    "trading-yangnan-v1": ("daily_history_70",),
    "trading-yangjia-v1": ("daily_history_70",),
    "trading-tuixue-v1": ("decision_plan",),
    "trading-gushiguifei-v1": ("daily_history_70",),
    "trading-wyckoff-v1": ("daily_history_70",),
    "trading-candle-v1": ("daily_history_70",),
    "trading-livermore-v1": ("daily_history_70",),
    "trading-darwin-v1": ("valuation_session", "dividend_history"),
    "trading-boardtech-v1": ("daily_history_70", "limit_pool_session"),
    "trading-chenkai-v1": ("daily_history_70", "hourly_bars"),
    "trading-lidaxiao-v1": ("valuation_session",),
}


def hard_required(seat_id: str) -> tuple[str, ...]:
    return _HARD[seat_id]
