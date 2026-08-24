'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ReactNode } from 'react';
import { Activity, ArrowLeft, BrainCircuit, Crosshair, Database, FlaskConical, Gauge, History, Layers3, Network, Radar, ShieldAlert, Swords, Target, TimerReset, Workflow } from 'lucide-react';

export const ROCI_NAV = [
  { href: '/roci', label: '战场驾驶舱', icon: Gauge },
  { href: '/roci/skills', label: 'Skill 技能中心', icon: BrainCircuit },
  { href: '/roci/battlefield', label: '敌友力量地图', icon: Swords },
  { href: '/roci/contradiction', label: '主要矛盾', icon: Crosshair },
  { href: '/roci/risk-pricing', label: '风险定价', icon: ShieldAlert },
  { href: '/roci/stress-test', label: '压力测试', icon: Activity },
  { href: '/roci/opportunities', label: '机会战术库', icon: Target },
  { href: '/roci/replay', label: '历史 Replay', icon: History },
  { href: '/roci/lab', label: 'Shadow Lab', icon: FlaskConical },
];

export function RociFrame({ title, subtitle, children, refresh, onRefresh, compact = false }: { title: string; subtitle?: string; children: ReactNode; refresh?: boolean; onRefresh?: () => void; compact?: boolean }) {
  const pathname = usePathname();
  return (
    <div className="roci-page">
      <aside className="roci-sidebar">
        <div className="roci-brand">
          <div className="roci-brand-mark">ROCI</div>
          <div><div className="roci-brand-title">风险机会认知</div><div className="roci-brand-sub">Risk & Opportunity</div></div>
        </div>
        <nav className="roci-side-nav" aria-label="ROCI功能导航">
          {ROCI_NAV.map(({ href, label, icon: Icon }) => <Link key={href} href={href} className={`roci-side-link ${pathname === href || (href !== '/roci' && pathname.startsWith(href)) ? 'active' : ''}`}><Icon size={15} /> <span>{label}</span></Link>)}
        </nav>
        <div className="roci-sidebar-note"><Database size={14} /><span>只读接入 V4 / V5 / V5.1<br />缺失数据保持 UNKNOWN</span></div>
        <Link href="/market" className="roci-back-link"><ArrowLeft size={14} /> 返回主工作台</Link>
      </aside>
      <main className="roci-main">
        <header className="roci-topbar">
          <div><div className="roci-kicker">ROCI · SIDE-CAR MODULE</div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
          <div className="roci-top-actions"><span className="roci-live-state"><span className="roci-live-dot" />独立只读模式</span>{onRefresh && <button type="button" className="roci-button" onClick={onRefresh} disabled={refresh}>{refresh ? '读取中…' : '刷新快照'}</button>}</div>
        </header>
        <div className={compact ? 'roci-content roci-content-compact' : 'roci-content'}>{children}</div>
      </main>
    </div>
  );
}

export function RociStatusPill({ value, tone = 'neutral' }: { value: string | number | null | undefined; tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'blue' }) {
  return <span className={`roci-pill roci-pill-${tone}`}>{value ?? 'UNKNOWN'}</span>;
}

export function RociBar({ value, tone = 'blue' }: { value: number | null | undefined; tone?: 'blue' | 'good' | 'warn' | 'bad' }) {
  const safe = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  return <div className="roci-bar"><span className={`roci-bar-fill roci-bar-${tone}`} style={{ width: `${safe}%` }} /></div>;
}

export function RociEvidence({ items }: { items?: Array<{ type?: string; label?: string; value?: unknown; source?: string; supports?: boolean }> }) {
  if (!items?.length) return <div className="roci-empty">暂无可追溯证据</div>;
  return <div className="roci-evidence-list">{items.slice(0, 8).map((item, index) => <div className="roci-evidence" key={`${item.label}-${index}`}><span className={`roci-evidence-type ${String(item.type || 'FACT').toLowerCase()}`}>{item.type || 'FACT'}</span><span className="roci-evidence-label">{item.label || '证据'}</span><span className={item.supports === false ? 'roci-muted' : 'roci-evidence-value'}>{typeof item.value === 'object' ? JSON.stringify(item.value) : String(item.value ?? 'UNKNOWN')}</span><span className="roci-evidence-source">{item.source || 'unknown'}</span></div>)}</div>;
}

export function RociSectionTitle({ eyebrow, title, action }: { eyebrow?: string; title: string; action?: ReactNode }) {
  return <div className="roci-section-title"><div>{eyebrow && <div className="roci-eyebrow">{eyebrow}</div>}<h2>{title}</h2></div>{action}</div>;
}

export function toneForAction(action?: string): 'good' | 'warn' | 'bad' | 'blue' | 'neutral' {
  if (action === 'ATTACK' || action === 'HOLD') return 'good';
  if (action === 'PROBE' || action === 'WAIT') return 'warn';
  if (action === 'DEFEND' || action === 'REDUCE' || action === 'EXIT' || action === 'NO_TRADE') return 'bad';
  return 'blue';
}
