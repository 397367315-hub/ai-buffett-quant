'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, Gauge, ShieldAlert } from 'lucide-react';
import { apiFetch } from '@/lib/api';

type AnyMap = Record<string, any>;

function text(value: unknown, fallback = '待核验'): string {
  const valueText = String(value ?? '').trim();
  return valueText || fallback;
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function first(...values: unknown[]): unknown {
  return values.find((value) => value !== null && value !== undefined && String(value).trim() !== '');
}

export default function WildmanAuthorityCard({
  fallback,
  compact = false,
  allowFallbackPrimary = Boolean(fallback),
  refreshKey,
  scopeComparable = true,
}: {
  fallback?: AnyMap;
  compact?: boolean;
  allowFallbackPrimary?: boolean;
  refreshKey?: string | number;
  scopeComparable?: boolean;
}) {
  const [authority, setAuthority] = useState<AnyMap | null>(null);
  const [loading, setLoading] = useState(true);
  const effectiveRefreshKey = refreshKey ?? fallback?.trade_date;

  useEffect(() => {
    let active = true;
    apiFetch<{ data?: AnyMap }>('/decision-authority', { cache: 'no-store', timeoutMs: 15000 })
      .then((response) => { if (active) setAuthority(response.data || null); })
      .catch(() => { if (active) setAuthority(null); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [effectiveRefreshKey]);

  const data = authority || fallback || {};
  const primary = scopeComparable && authority ? (data.primary || {}) : {};
  const fallbackPrimary = fallback || {};
  const secondary = data.secondary || {};
  const effective = data.effective || {};
  const evidence = data.data_quality || {};
  const conflictItems = Array.isArray(data.conflicts) ? data.conflicts : [];
  const riskReasons = [...(Array.isArray(effective.reasons) ? effective.reasons : []), ...(Array.isArray(evidence.reasons) ? evidence.reasons : [])];
  const sourceDates = evidence.source_dates && typeof evidence.source_dates === 'object' ? evidence.source_dates : {};
  const wildmanDate = first(primary.trade_date, allowFallbackPrimary ? fallbackPrimary.trade_date : undefined);
  const v4Date = first(sourceDates.v4, secondary.trade_date);
  const date = wildmanDate;
  const horizon = first(primary.cycle, allowFallbackPrimary ? fallbackPrimary.horizon : undefined);
  const cycle = first(primary.cycle, primary.cycle_node, allowFallbackPrimary ? fallbackPrimary.cycle : undefined);
  const mainline = Array.isArray(primary.mainline_summary)
    ? primary.mainline_summary.map((item: AnyMap) => typeof item === 'object' ? [item.theme_name, item.state].filter(Boolean).join(' · ') : String(item)).filter(Boolean).join('、')
    : first(primary.mainline_summary, allowFallbackPrimary ? fallbackPrimary.mainline : undefined);
  const numericPosition = scopeComparable ? number(effective.max_position_pct) : null;
  const comparable = evidence.comparable === true;
  const permission = text(first(effective.label, effective.status), '辅助判断');
  const conflict = conflictItems.length > 0;
  const v4Reason = text(first(secondary.reason, conflictItems[0]?.reason), conflict ? '存在辅助模型分歧，按日期与周期核对' : '暂无可比的其它意见');
  const v4StatusLabel: Record<string, string> = { aligned: '一致', disagreement: '分歧', execute: '辅助建议可研究', no_trade: '辅助建议暂不参与', stale: '过期快照', unavailable: '暂不可用', caution: '谨慎观察', observe: '观察等待', watch: '观察等待', unknown: '待核验' };
  const v4Status = v4StatusLabel[String(secondary.status || '').toLowerCase()] || text(secondary.status, '待核验');
  const v4Action = v4StatusLabel[String(secondary.action || '').toLowerCase()] || text(secondary.action, '待核验');

  return <section className={`rounded-md border border-accent/40 bg-card ${compact ? 'p-3' : 'p-4'}`} aria-label="野人哥主观点">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm font-semibold text-text"><Gauge size={15} className="shrink-0 text-accent" />野人哥主观点</div>
        <div className="mt-1 text-[10px] text-text-secondary">系统仅作辅助判断，不接真实交易</div>
      </div>
      <span className={`rounded border px-2 py-1 text-[10px] ${conflict ? 'border-warn/40 text-warn' : 'border-accent/40 text-accent'}`}>{conflict ? '观点分歧，暂缓积极建议' : permission}</span>
    </div>
    <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-3 lg:grid-cols-6">
      <div><span className="block text-text-secondary">野人哥决策日</span><b className="mt-1 block text-text">{text(date, '野人哥快照待核验')}</b></div>
      <div><span className="block text-text-secondary">V4决策日</span><b className="mt-1 block text-text">{text(v4Date)}</b></div>
      <div><span className="block text-text-secondary">周期</span><b className="mt-1 block text-text">{text(horizon || cycle)}</b></div>
      <div><span className="block text-text-secondary">主线</span><b className="mt-1 block truncate text-text" title={text(mainline)}>{text(mainline)}</b></div>
      <div><span className="block text-text-secondary">有效参考上限</span><b className={`mt-1 block ${numericPosition === 0 ? 'text-down' : 'text-accent'}`}>{numericPosition !== null ? `${numericPosition}%` : '暂无法统一核验'}</b></div>
      <div><span className="block text-text-secondary">数据状态</span><b className="mt-1 block text-text">{!scopeComparable ? '范围不可比' : comparable ? '同日可比' : '日期或口径待核对'}</b></div>
    </div>
    {(riskReasons.length > 0 || conflict || loading || numericPosition === null) && <div className="mt-3 border-t border-border pt-2 text-[10px] leading-4 text-warn">
      <div className="flex items-start gap-1.5"><ShieldAlert size={12} className="mt-0.5 shrink-0" />{loading ? '正在读取同日主观点与有效仓位参考' : !scopeComparable ? `主观点比较仅默认股票范围；当前非默认范围，暂不可合并。野人哥周期原参考：${text(fallbackPrimary.position_range || primary.position_range, '待核验')}` : riskReasons.slice(0, 2).join('；') || (numericPosition === null ? `野人哥周期原参考：${text(primary.position_range || fallbackPrimary.position_range, '待核验')}` : v4Reason)}</div>
    </div>}
    {!loading && authority && <div className="mt-2 text-[10px] text-text-secondary">V4补充意见：{!scopeComparable ? '股票范围非默认 S1/G1，暂不可与主观点合并' : comparable ? `${v4Status} · ${v4Action} · ${v4Reason}` : '暂无可比的其它意见（日期/周期未对齐）'}</div>}
    {!loading && !authority && <div className="mt-2 flex items-start gap-1.5 text-[10px] text-warn"><AlertTriangle size={12} className="mt-0.5 shrink-0" />主观点接口暂不可用，保留页面原始数据；日期或周期未知时不合并判断。</div>}
  </section>;
}
