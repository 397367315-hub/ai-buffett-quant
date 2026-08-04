'use client';

import { AlertTriangle, ArrowUpRight, Clock3, Database, Loader2, RefreshCw, Send, Signal, Wifi } from 'lucide-react';
import type { BackgroundJob, SignalSnapshot, TradeSignal } from '../types';
import AddToPersonalPoolButton from '@/components/AddToPersonalPoolButton';

function formatTime(value?: string | null) {
  if (!value) return '尚未扫描';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false });
}

function priceColor(value: number) { return value > 0 ? 'text-up' : value < 0 ? 'text-down' : 'text-text-secondary'; }

export default function SignalList({ snapshot, job, onRefresh, onAddToPaper, history }: {
  snapshot: SignalSnapshot | null;
  job: BackgroundJob | null;
  onRefresh: () => void;
  onAddToPaper: (signal: TradeSignal) => void;
  history: SignalSnapshot[];
}) {
  const active = job && ['queued', 'running'].includes(job.status);
  const signals = snapshot?.signals || [];
  const featureDatasets = Object.entries(snapshot?.feature_coverage?.datasets || {});
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div>
          <h2 className="text-base font-bold text-text flex items-center gap-2"><Signal size={17} className="text-accent" />信号看板</h2>
          <div className="text-xs text-text-secondary mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="inline-flex items-center gap-1"><Clock3 size={12} />{formatTime(snapshot?.generated_at)}</span>
            {snapshot && <span className="inline-flex items-center gap-1"><Database size={12} />已扫描 {snapshot.scanned_stocks.toLocaleString()} 只</span>}
            {snapshot?.is_realtime && <span className="inline-flex items-center gap-1 text-down"><Wifi size={12} />盘中实时</span>}
          </div>
        </div>
        <button type="button" onClick={onRefresh} disabled={Boolean(active)} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white text-xs rounded-md hover:brightness-110 disabled:opacity-50"><RefreshCw size={14} className={active ? 'animate-spin' : ''} />{active ? '扫描中' : '全市场扫描'}</button>
      </div>

      {active && <div className="border border-accent/50 bg-[#1F6FEB22] rounded-md p-3">
        <div className="flex items-center justify-between gap-2 text-xs"><span className="text-text flex items-center gap-2"><Loader2 size={14} className="animate-spin text-accent" />{job.message}</span><span className="font-mono text-accent">{Math.round(job.progress)}%</span></div>
        <div className="mt-2 h-1.5 bg-[#0D1117] rounded-full overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${Math.max(2, job.progress)}%` }} /></div>
      </div>}
      {job?.status === 'failed' && <div className="border border-up/50 bg-[#EF535022] rounded-md p-3 text-xs text-up flex items-start gap-2"><AlertTriangle size={15} className="shrink-0" />{job.error || '扫描失败，请稍后重试。'}</div>}
      {snapshot?.warning && <div className="border border-warn/50 bg-[#D299221A] rounded-md p-3 text-xs text-warn flex items-start gap-2"><AlertTriangle size={15} className="shrink-0" />{snapshot.warning}</div>}

      {snapshot && <div className="grid grid-cols-2 sm:grid-cols-4 border border-border rounded-md divide-x divide-border">
        <div className="p-3"><div className="text-xs text-text-secondary">买入信号</div><div className="font-mono text-lg text-up mt-1">{signals.length}</div></div>
        <div className="p-3"><div className="text-xs text-text-secondary">启用策略</div><div className="font-mono text-lg text-text mt-1">{snapshot.strategy_count || 0}</div></div>
        <div className="p-3"><div className="text-xs text-text-secondary">技术指标覆盖</div><div className="font-mono text-lg text-text mt-1">{snapshot.technical_history_coverage || 0}</div></div>
        <div className="p-3"><div className="text-xs text-text-secondary">数据状态</div><div className={`text-sm mt-1 ${!snapshot.generated_at ? 'text-text-secondary' : snapshot.stale ? 'text-warn' : 'text-down'}`}>{!snapshot.generated_at ? '未扫描' : snapshot.stale ? '缓存降级' : '可用'}</div></div>
      </div>}

      {snapshot && featureDatasets.length > 0 && <section className="border-y border-border py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
          <span className="font-medium text-text">研究特征覆盖</span>
          {featureDatasets.map(([key, item]) => (
            <span key={key} className="text-text-secondary">
              {item.label}：{item.available != null
                ? (item.available ? '可用' : '不可用')
                : `${item.covered ?? 0}/${item.total ?? snapshot.scanned_stocks}`}
            </span>
          ))}
          {snapshot.feature_updated_at && <span className="text-text-secondary">更新 {formatTime(snapshot.feature_updated_at)}</span>}
        </div>
      </section>}

      <section className="border border-border rounded-md overflow-hidden">
        <div className="px-3 py-2 border-b border-border text-sm font-semibold text-text">当前扫描结果</div>
        {signals.length ? <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-xs">
          <thead className="bg-[#161B22] text-text-secondary"><tr><th className="text-left px-3 py-2">股票</th><th className="text-left px-3 py-2">所属板块</th><th className="text-right px-3 py-2">价格</th><th className="text-right px-3 py-2">涨跌幅</th><th className="text-right px-3 py-2">匹配度</th><th className="text-left px-3 py-2">命中策略</th><th className="text-left px-3 py-2">规则证据</th><th className="text-right px-3 py-2">操作</th></tr></thead>
          <tbody>{signals.map((signal) => <tr key={signal.signal_id} className="border-t border-border/70 hover:bg-[#161B22]">
            <td className="px-3 py-2.5"><div className="font-medium text-text">{signal.stock_name}</div><div className="font-mono text-text-secondary mt-0.5">{signal.stock_code}</div></td>
            <td className="px-3 py-2.5 text-text-secondary">{signal.sector || '未分类'}</td>
            <td className="px-3 py-2.5 text-right font-mono text-text">{Number(signal.price || 0).toFixed(2)}</td>
            <td className={`px-3 py-2.5 text-right font-mono ${priceColor(signal.change_pct || 0)}`}>{(signal.change_pct || 0) >= 0 ? '+' : ''}{Number(signal.change_pct || 0).toFixed(2)}%</td>
            <td className="px-3 py-2.5"><div className="flex items-center justify-end gap-2"><div className="w-16 h-1.5 rounded-full bg-[#30363D] overflow-hidden"><div className="h-full bg-accent" style={{ width: `${Math.min(100, signal.match_score)}%` }} /></div><span className="font-mono text-accent">{signal.match_score}</span></div></td>
            <td className="px-3 py-2.5 text-text-secondary">{(signal.strategy_matches || [{ strategy_name: signal.strategy_name }]).map((item) => item.strategy_name).join('、')}</td>
            <td className="px-3 py-2.5 text-text-secondary max-w-[260px]">
              <div className="line-clamp-2">{signal.matched_rules.join('；')}</div>
              {Boolean(signal.risk_flags?.hard_blocks.length) && <div className="mt-1 text-up line-clamp-1">否决：{signal.risk_flags?.hard_blocks.join('；')}</div>}
              {!signal.risk_flags?.hard_blocks.length && Boolean(signal.risk_flags?.warnings.length) && <div className="mt-1 text-warn line-clamp-1">风险：{signal.risk_flags?.warnings.join('；')}</div>}
              {Boolean(signal.unavailable_rules?.length) && <div className="mt-1 text-warn line-clamp-1">数据不足：{signal.unavailable_rules?.join('；')}</div>}
            </td>
            <td className="px-3 py-2.5 text-right"><div className="flex items-center justify-end gap-1.5"><AddToPersonalPoolButton code={signal.stock_code} name={signal.stock_name} industry={signal.sector} thesis={`量化策略：${(signal.strategy_matches || [{ strategy_name: signal.strategy_name }]).map((item) => item.strategy_name).join('、')}；命中规则：${signal.matched_rules.join('；')}`} source="quant_signal" compact /><button type="button" onClick={() => onAddToPaper(signal)} className="inline-flex items-center gap-1 px-2 py-1.5 text-accent hover:bg-[#1F6FEB22] rounded-md" title="带入模拟盘"><Send size={13} />模拟买入</button></div></td>
          </tr>)}</tbody>
        </table></div> : <div className="py-12 text-center text-sm text-text-secondary"><ArrowUpRight size={22} className="mx-auto mb-2 text-border" />暂无匹配信号。可调整策略规则或发起一次全市场扫描。</div>}
      </section>

      {history.length > 0 && <section className="border-t border-border pt-3"><div className="text-xs text-text-secondary mb-2">历史扫描</div><div className="flex flex-wrap gap-2">{history.slice(0, 8).map((item, index) => <div key={`${item.generated_at}-${index}`} className="border border-border rounded-md px-2.5 py-1.5 text-xs"><span className="text-text">{formatTime(item.generated_at)}</span><span className="ml-2 font-mono text-accent">{item.signals?.length || 0} 条</span></div>)}</div></section>}
    </div>
  );
}
