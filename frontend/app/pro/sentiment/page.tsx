'use client';

import { useEffect, useState } from 'react';
import { apiFetch, formatYi, getChangeColor } from '@/lib/api';
import {
  Gauge, TrendingUp, TrendingDown, ArrowRightLeft, BarChart3,
  HelpCircle, Activity, Flame, Snowflake
} from 'lucide-react';

interface BreadthEntry {
  up: number;
  down: number;
  total: number;
  ratio: number;
}

interface SentimentData {
  available?: boolean;
  score: number;
  label: string;
  details: string[];
  breadth: Record<string, BreadthEntry>;
  turnover: Record<string, any>;
  limit_counts: { up: number; down: number };
  main_flow_trend: string;
  main_flow_amount: number;
}

function getScoreColor(score: number): string {
  if (score >= 75) return '#EF5350';
  if (score >= 60) return '#58A6FF';
  if (score >= 45) return '#D29922';
  if (score >= 30) return '#E0823D';
  return '#26A69A';
}

function getScoreBgColor(score: number): string {
  if (score >= 75) return 'rgba(38, 166, 154, 0.15)';
  if (score >= 60) return 'rgba(88, 166, 255, 0.15)';
  if (score >= 45) return 'rgba(210, 153, 34, 0.15)';
  if (score >= 30) return 'rgba(224, 130, 61, 0.15)';
  return 'rgba(239, 83, 80, 0.15)';
}

export default function SentimentPage() {
  const [data, setData] = useState<SentimentData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const res = await apiFetch<any>('/market/sentiment');
      setData(res.data);
    } catch (err) {
      console.error('Failed to fetch sentiment data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          <span>数据加载中...</span>
        </div>
      </div>
    );
  }

  if (!data || !data.available) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-text-secondary text-center">
          <Activity size={24} className="mx-auto mb-2 opacity-50" />
          <span>实时情绪数据暂不可用</span>
        </div>
      </div>
    );
  }

  const scoreColor = getScoreColor(data.score);
  const scoreBgColor = getScoreBgColor(data.score);

  const gaugeRadius = 65;
  const gaugeCircumference = Math.PI * gaugeRadius;
  const gaugeOffset = gaugeCircumference - ((data.score / 100) * gaugeCircumference * 0.75);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Gauge size={22} className="text-accent" />
          市场情绪仪表盘
        </h1>
        <p className="text-text-secondary text-sm mt-1">
          综合涨跌比、涨跌停数量、资金流向等多维度，量化市场情绪
        </p>
      </div>

      {/* 情绪仪表盘卡片 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        <div className="bg-card border border-border rounded-lg p-6 flex flex-col items-center justify-center">
          <div className="text-xs text-text-secondary mb-4 uppercase tracking-wider">市场情绪指数</div>
          <div className="relative w-40 h-40 mb-3">
            <svg viewBox="0 0 160 100" className="w-full h-full -rotate-90">
              <path
                d="M20 90 A 60 60 0 0 1 140 90"
                fill="none"
                stroke="#21262D"
                strokeWidth="12"
                strokeLinecap="round"
              />
              <path
                d="M20 90 A 60 60 0 0 1 140 90"
                fill="none"
                stroke={scoreColor}
                strokeWidth="12"
                strokeLinecap="round"
                strokeDasharray={`${(data.score / 100) * Math.PI * 60} ${Math.PI * 60}`}
                style={{ transition: 'stroke-dasharray 0.5s ease' }}
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center" style={{ paddingTop: '12px' }}>
              <span className="text-4xl font-bold font-mono" style={{ color: scoreColor }}>
                {data.score}
              </span>
              <span className="text-xs text-text-secondary mt-0.5">/ 100</span>
            </div>
          </div>
          <span className="text-lg font-bold" style={{ color: scoreColor }}>
            {data.label.replace(/[🟢🟡🟠🔴]\s*/, '')}
          </span>
          <div className="flex items-center gap-6 mt-3 text-xs text-text-secondary">
            <span>{data.main_flow_trend === '流入' ? <TrendingUp size={12} className="inline text-up mr-0.5" /> : <TrendingDown size={12} className="inline text-down mr-0.5" />}</span>
          </div>
        </div>

        <div className="lg:col-span-2 grid grid-cols-2 gap-4">
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
              <Flame size={14} className="text-up" />
              涨停数
            </div>
            <div className="text-2xl font-mono font-bold text-up">{data.limit_counts.up}只</div>
            <div className="text-xs text-text-secondary mt-0.5">
              {data.limit_counts.up > 100 ? '市场情绪高涨' : data.limit_counts.up > 50 ? '情绪偏暖' : '正常'}
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
              <Snowflake size={14} className="text-down" />
              跌停数
            </div>
            <div className="text-2xl font-mono font-bold text-down">{data.limit_counts.down}只</div>
            <div className="text-xs text-text-secondary mt-0.5">
              {data.limit_counts.down > 50 ? '恐慌情绪明显' : data.limit_counts.down > 10 ? '情绪偏冷' : '正常'}
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
              <ArrowRightLeft size={14} className="text-accent" />
              主力资金方向
            </div>
            <div className={`text-2xl font-mono font-bold ${data.main_flow_trend === '流入' ? 'text-up' : 'text-down'}`}>
              {data.main_flow_trend === '流入' ? '净流入' : '净流出'}
            </div>
            <div className={`text-xs mt-0.5 ${getChangeColor(data.main_flow_amount)}`}>
              {formatYi(data.main_flow_amount)}
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center gap-1.5 text-xs text-text-secondary mb-1">
              <BarChart3 size={14} className="text-warn" />
              涨跌比
            </div>
            {data.breadth && Object.entries(data.breadth).length > 0 ? (
              <div className="text-2xl font-mono font-bold text-text">
                {Object.entries(data.breadth)[0]?.[1]?.ratio || '--'}%
              </div>
            ) : (
              <div className="text-2xl font-mono font-bold text-text-secondary">--</div>
            )}
            <div className="text-xs text-text-secondary mt-0.5">上涨家数占比</div>
          </div>
        </div>
      </div>

      {/* 各市场涨跌明细 */}
      {data.breadth && Object.keys(data.breadth).length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-4">各市场涨跌分布</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {Object.entries(data.breadth).map(([market, b]) => (
              <div key={market} className="bg-[#0D1117] border border-border rounded-lg p-4">
                <div className="text-sm font-medium text-text mb-2">{market}</div>
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-up text-sm font-mono">{b.up}↑</span>
                  <div className="flex-1 h-1.5 bg-[#21262D] rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${b.ratio}%`, backgroundColor: '#EF5350' }}
                    />
                  </div>
                  <span className="text-down text-sm font-mono">{b.down}↓</span>
                </div>
                <div className="text-xs text-text-secondary">
                  涨跌比 <span className="text-text font-medium">{b.ratio}%</span> · 总计{b.total}只
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 情绪详情列表 */}
      {data.details && data.details.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-4 flex items-center gap-2">
            <Activity size={18} className="text-accent" />
            情绪分析详情
          </h3>
          <div className="space-y-2">
            {data.details.map((detail, i) => (
              <div
                key={i}
                className="flex items-start gap-2 text-sm text-text-secondary bg-[#0D1117] rounded-lg px-4 py-2.5 border border-border/50"
              >
                <span className="text-accent mt-0.5">▸</span>
                <span>{detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 小白解读 */}
      <div className="bg-card border border-border rounded-lg p-6">
        <h3 className="text-lg font-bold text-text mb-3 flex items-center gap-2">
          <HelpCircle size={18} className="text-accent" />
          小白解读
        </h3>
        <div className="text-sm text-text-secondary leading-relaxed space-y-2">
          <p>
            <strong className="text-text">市场情绪指数</strong>是一个 0-100 的综合评分，
            用来衡量当前市场参与者的<strong className="text-text">乐观或悲观程度</strong>。
          </p>
          <p>
            <span className="text-up">75分以上（极度乐观）</span>：市场亢奋，上涨家数远超下跌家数，涨停数量多，主力资金大幅流入。
            此时要注意<strong className="text-warn">追高风险</strong>。
          </p>
          <p>
            <span className="text-down">30分以下（极度悲观）</span>：市场恐慌，跌停潮出现，主力出逃。
            市场极度悲观时，反而是<strong className="text-up">逆向思考</strong>的时机。
          </p>
          <p>
            <span className="text-accent">45-60分（中性区间）</span>：市场多空均衡，适合按正常策略交易。
          </p>
          <p className="text-xs text-text-secondary mt-2 pt-2 border-t border-border">
            💡 提示：极端情绪往往是市场的反向指标——"在别人恐惧时贪婪，在别人贪婪时恐惧"。
          </p>
        </div>
      </div>
    </div>
  );
}
