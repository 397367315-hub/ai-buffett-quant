import type { QuantPageData } from './types';

/**
 * 以下数字来自用户提供的两张原始截图，仅用于视觉/交互演示。
 * 未连接实时行情、真实策略服务或实际股票明细。
 * 将 src/App.tsx 中 initialData 替换为真实 API 数据即可。
 */
export const demoData: QuantPageData = {
  source: 'demo',
  stages: [
    {
      id: 'L1', title: '全市场', shortTitle: '初始市场样本', count: 4600,
      note: '沪深主板 + 创业板', logic: '以指定交易市场构建第一层股票全集。',
      conditions: ['沪深主板', '创业板', '科创板 688 / 北交所 / B 股不在样本内'],
    },
    {
      id: 'L2', title: '合格池', shortTitle: '基础资格过滤', count: 2363,
      note: '价格 · 流动性 · 上市时间', logic: '先排除不具备基础可交易性的股票。',
      conditions: ['剔除 ST / *ST', '股价 ≥ 2 元', '日成交额 ≥ 1 亿元', '上市满 60 个自然日（以原有规则为准）'],
    },
    {
      id: 'L3', title: '席位覆盖', shortTitle: '策略席位生成候选', count: 80,
      note: '任一席位给出候选', logic: '十三个独立席位分别运行；只要任一席位命中即进入本层候选集合。',
      conditions: ['保持十三个席位独立策略', '候选集合取并集', '此阶段不进行投票或共识折中'],
    },
    {
      id: 'L4', title: '环境闸门', shortTitle: '当前环境匹配', count: 75,
      note: '只留开启席位的候选', logic: '按各席位独立的交易环境判断，过滤当前不允许开仓的候选。',
      conditions: ['读取席位开 / 不开状态', '只保留允许参与的席位候选', '保留原始席位归属'],
    },
    {
      id: 'L5', title: '去重可执行', shortTitle: '最终重点跟踪池', count: 73,
      note: '同一股票多席位命中只算一次', logic: '按股票唯一代码去重，保留命中该股票的全部席位来源。',
      conditions: ['股票代码唯一去重', '保留命中席位列表', '生成最终候选池供下一步研究'],
    },
  ],
  seats: [
    {
      id: 'wildman', name: '野人哥', subtitle: '保住本金', version: 'trading-wildman-v2',
      gate: 'CLOSED', runState: 'SUCCESS', count: 0, configured: true,
      keywords: ['周期与主线', '本金防守', '风控优先'],
      reasoning: '周期 → 主线 → 角色 → 次日预期 → 买点。当前示例结果：不开。',
      portrait: '/avatars/wildman.webp',
    },
    {
      id: 'jianmen', name: '剑门', subtitle: '纪律择时', version: 'trading-jianmen-v1',
      gate: 'WEAK', runState: 'SUCCESS', count: 10, configured: true,
      keywords: ['生态三档', '地位排序', '龙空龙'],
      reasoning: '生态三档 + 地位排序。最高 4 板，大肉数 43.0 家，连板 10.8 家；判弱生态，龙空龙路径下空仓。',
      portrait: '/avatars/jianmen.webp',
    },
    {
      id: 'yangnan', name: '杨楠', subtitle: '情绪流龙头', version: 'trading-yangnan-v1',
      gate: 'NO_SIGNAL', runState: 'SUCCESS', count: 0, configured: true,
      keywords: ['情绪周期', '龙头挖掘', '冰点判断'],
      reasoning: '四指标 + 两次冰点。60 日内冰点计数 0 < 2，首次不买。',
      portrait: '/avatars/yangnan.webp',
    },
    {
      id: 'chaogu', name: '炒股养家', subtitle: '市场理解', version: 'trading-chaoguyangjia-v1',
      gate: 'MIXED', runState: 'SUCCESS', count: 0, configured: true,
      keywords: ['赚钱效应', '仓位管理', '知行合一'],
      reasoning: '赚钱效应四指标，四指标 2 项向下。混合状态，只允许试错仓且不确认不加仓。',
      portrait: '/avatars/chaogu.webp',
    },
    ...Array.from({ length: 6 }, (_, i) => ({
      id: `seat-${String(i + 5).padStart(2, '0')}`,
      name: `席位 ${String(i + 5).padStart(2, '0')}`,
      subtitle: '等待读取真实策略配置', version: '待接入策略标识',
      gate: 'UNCONFIGURED' as const, runState: 'IDLE' as const, count: null, configured: false,
      keywords: ['独立席位', '策略待接入'], reasoning: '此处只提供第 05～10 席位的 UI 插槽，不虚构角色名称、信号或候选数。',
    })),
  ],
};
