import { useMemo, useState } from 'react';
import type { Candidate, PoolKey } from '../types';
import { Icon } from './Icons';

const POOLS: { key: PoolKey | 'all'; label: string; hint: string }[] = [
  { key: 'all', label: '全部', hint: '当日所有开闸席位的候选' },
  { key: 'short', label: '短期', hint: '1–3 天：情绪与接力' },
  { key: 'swing', label: '波段', hint: '3–15 天：区域与事件' },
  { key: 'value', label: '长期价值', hint: '季度以上：质量与价格' },
];

/** 均线（收盘价的简单平均）。60 日线只有在样本 ≥60 根时才有值。 */
const MA_DEFS: { n: number; color: string }[] = [
  { n: 5, color: '#c8892f' }, { n: 10, color: '#2f6fd0' }, { n: 20, color: '#8e5cc7' }, { n: 60, color: '#3f7f5c' },
];

function movingAverage(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= n) sum -= closes[i - n];
    out.push(i + 1 >= n ? sum / n : null);
  }
  return out;
}

function CandleChart({ c }: { c: Candidate }) {
  const k = c.kline;
  const [hidden, setHidden] = useState<number[]>([]);
  const mas = useMemo(() => {
    const closes = k.map(b => b.c);
    return MA_DEFS.map(d => ({ ...d, values: movingAverage(closes, d.n) })).filter(m => m.values.some(v => v !== null));
  }, [k]);
  if (k.length < 2) return <div className="cand-nochart">无 K 线数据</div>;
  const W = 720, H = 250, L = 46, R = 12, T = 12, B = 22;
  const shown = mas.filter(m => !hidden.includes(m.n));
  const maVals = shown.flatMap(m => m.values.filter((v): v is number => v !== null));
  const hi = Math.max(...k.map(x => x.h), ...maVals);
  const lo = Math.min(...k.map(x => x.l), ...maVals);
  const pad = (hi - lo) * 0.08 || 1;
  const top = hi + pad, bot = lo - pad;
  const iw = W - L - R, ih = H - T - B;
  const step = iw / k.length;
  const bw = Math.max(1.6, step * 0.62);
  const y = (v: number) => T + (top - v) / (top - bot) * ih;
  const line = (values: (number | null)[]) => {
    let d = '';
    let pen = false;
    values.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      const x = L + i * step + step / 2;
      d += `${pen ? 'L' : 'M'}${x.toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const lastOf = (values: (number | null)[]) => {
    for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return values[i] as number;
    return null;
  };
  const toggle = (n: number) => setHidden(h => h.includes(n) ? h.filter(x => x !== n) : [...h, n]);
  return (
    <div className="cand-chart-box" data-testid="cand-chart" data-ma-count={shown.length}>
      <div className="cand-ma-legend" role="group" aria-label="均线（点一下隐藏或显示）">
        {mas.map(m => (
          <button key={m.n} type="button" className="ma-chip" style={{ color: m.color }}
                  aria-pressed={!hidden.includes(m.n)} onClick={() => toggle(m.n)}
                  title={`${m.n} 日均线（收盘价简单平均）${hidden.includes(m.n) ? '，已隐藏' : '，点一下隐藏'}`}>
            MA{m.n} {lastOf(m.values)?.toFixed(2) ?? '--'}
          </button>
        ))}
        <span className="cand-ma-hint">点均线可隐藏/显示</span>
      </div>
      <svg className="cand-chart" viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={`${c.name} 近 ${k.length} 日 K 线，含 ${shown.map(m => `MA${m.n}`).join('、') || '无'} 均线`}>
        {[0, 1, 2, 3, 4].map(i => {
          const v = top - (top - bot) * i / 4;
          return (
            <g key={i}>
              <line x1={L} y1={y(v)} x2={W - R} y2={y(v)} stroke="#e8e2d8" strokeWidth={1}/>
              <text x={L - 8} y={y(v) + 4} fontSize={10} fill="#a39a8e" textAnchor="end">{v.toFixed(2)}</text>
            </g>
          );
        })}
        {k.map((b, i) => {
          const x = L + i * step + step / 2;
          const up = b.c >= b.o;
          const col = up ? '#b8342a' : '#2f7d55';
          const y1 = y(Math.max(b.o, b.c)), y2 = y(Math.min(b.o, b.c));
          return (
            <g key={b.d}>
              <line x1={x} y1={y(b.h)} x2={x} y2={y(b.l)} stroke={col} strokeWidth={1}/>
              <rect x={x - bw / 2} y={y1} width={bw} height={Math.max(1, y2 - y1)} fill={col}/>
            </g>
          );
        })}
        {shown.map(m => (
          <path key={m.n} d={line(m.values)} fill="none" stroke={m.color} strokeWidth={1.5}
                strokeLinejoin="round" strokeLinecap="round" opacity={0.95}/>
        ))}
        <text x={L} y={H - 6} fontSize={10} fill="#a39a8e">{k[0].d.slice(4, 6)}-{k[0].d.slice(6, 8)}</text>
        <text x={W - R} y={H - 6} fontSize={10} fill="#a39a8e" textAnchor="end">{k[k.length - 1].d.slice(4, 6)}-{k[k.length - 1].d.slice(6, 8)}</text>
      </svg>
      <p className="cand-chart-note">K 线为最近 {k.length} 个交易日的原始收盘价（未复权）；均线是收盘价的简单平均，MA60 需要满 60 根才有线。</p>
    </div>
  );
}

function RuleBlock({ c }: { c: Candidate }) {
  const r = c.rules;
  if (!r) return null;
  const warn = r.risk_flag && r.risk_flag !== '正常';
  const pivot = r.entry_mode === 'pivot';
  const val = r.entry_mode === 'valuation';
  const st = r.entry_mode === 'structure';
  const bd = r.entry_mode === 'board';
  return (
    <div className="cand-rules">
      <div className="cand-rule-col">
        <h5 className="rule-in">入场规则</h5>
        {bd
          ? <ul>
              <li>{r.add_rule ?? '涨停价挂单打板；一字板只能次日集合竞价排队，不保证成交'}</li>
              <li>只看当日封板质量：封板越早越强，下午封板谨慎</li>
              <li>打板的前提是买得到，一字板与秒封板大概率成交不了</li>
            </ul>
          : st
          ? <ul>
              <li>{r.add_rule ?? '大小周期同向，日线回踩 20/30 日均线附近，建仓区间内分批'}</li>
              <li>大周期（周线）与小周期（4 日线）必须同向，逆向就等</li>
              <li><b>顶背离</b>（价新高、DIF 连续两天走低）直接放弃</li>
            </ul>
          : val
          ? <ul>
              <li>{r.add_rule ?? '底部区域分批买，不用等回调——书里不主张挑点位'}</li>
              <li>买之前先看闸门：全市场估值要处在底部区域，不是看这只票的图形</li>
              <li>排序先看是不是<b>行业龙头</b>，再看便宜不便宜</li>
            </ul>
          : pivot
          ? <ul>
              <li>关键点 <b>{r.pivot ?? '—'}</b>，<b>当日首次站上</b>才买——今收在关键点上方、昨收在其下方</li>
              <li>早已在上方的不买：收盘高出关键点 <b>5%</b> 以上算追高</li>
              <li>{r.add_rule ?? '突破下一个关键点才加，第二笔不超过首笔 100%'}</li>
            </ul>
          : <ul>
              <li>A 回踩买：回踩 <b>{r.entry_pull ?? '—'}</b> 附近，缩量止跌、收盘重新站上均线才买</li>
              <li>B 突破买：放量突破 <b>{r.entry_break ?? '—'}</b></li>
              <li>两条都要等次日及以后触发，当日不追</li>
            </ul>}
      </div>
      <div className="cand-rule-col">
        <h5 className="rule-out">离场规则</h5>
        <ul>
          <li>
            {bd ? '不设价格止损：'
              : st ? '建仓区间下沿 '
              : val ? '容错线（判断错了才用，不是技术止损）'
              : pivot ? '跌破关键点即错，止损 ' : '止损 '}
            {!bd && <><b>{r.stop ?? '—'}</b>（−{r.risk_pct?.toFixed(2) ?? '—'}%{!val && r.atr_mult ? `，${r.atr_mult}×ATR` : ''}）</>}
            {warn && <em className="rule-warn">［{r.risk_flag}］</em>}
          </li>
          {bd
            ? <li>{r.target_note ?? '断板即卖；高位出现反转征兆立刻出局'}</li>
            : st
            ? <li>{r.target_note ?? '跌破 MA20 且 MA20 走平，结构失效'}</li>
            : val
            ? <li>{r.target_note ?? '按估值退出：PE 回到 22 以上或股息率跌破 1%'}</li>
            : pivot
            ? <li>{r.target_note ?? '不设目标价（书里按关键点判断退出）'}</li>
            : <li>止盈一 <b>{r.tgt1 ?? '—'}</b> 减半；止盈二 <b>{r.tgt2 ?? '—'}</b></li>}
          <li>{r.time_exit ?? '5 个交易日内不创新高则退'}</li>
        </ul>
      </div>
    </div>
  );
}

export function CandidateDetail({ c, onClose, onAddToPool, inPool }: {
  c: Candidate; onClose: () => void;
  onAddToPool?: (c: Candidate) => void;
  inPool?: (code: string) => boolean;
}) {
  const s = c.stats;
  return (
    <div className="drawer-scrim" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="seat-drawer cand-drawer" role="dialog" aria-modal="true" aria-label={`${c.name}详情`}>
        <div className="drawer-top">
          <span>CANDIDATE / {c.code}</span>
          <button aria-label="关闭详情" className="icon-button" onClick={onClose}><Icon name="close" size={20}/></button>
        </div>
        <div className="drawer-profile">
          <div>
            <span className="drawer-sublabel">{c.poolLabel} · {(c.seats && c.seats.length ? c.seats : [c.seat]).join(' · ')}</span>
            <h2>{c.name}</h2>
            {c.stage && <span className="cand-stage">{c.stage}</span>}
          {c.tags && c.tags.length > 0 && (
            <div className="cand-tags-row">
              {c.tags.map(t => <span key={t} className="cand-tagchip">{t}</span>)}
              {(c.risks || []).map(t => <span key={t} className="cand-tagchip is-risk">{t}</span>)}
            </div>
          )}
          </div>
        </div>
        {s && (
          <div className="drawer-grid cand-stats">
            <div><label>近 5 日</label><strong>{s.ret5 ?? '—'}<small> %</small></strong></div>
            <div><label>近 20 日</label><strong>{s.ret20 ?? '—'}<small> %</small></strong></div>
            <div><label>距 20 日高</label><strong>{s.dist_hh}<small> %</small></strong></div>
            <div><label>5 日主力</label><strong>{s.flow5 > 0 ? '+' : ''}{s.flow5}<small> 亿</small></strong></div>
          </div>
        )}
        <div className="cand-chart-wrap"><CandleChart c={c}/></div>
        {c.state10.length > 0 && (
          <div className="cand-states">
            <label>量价状态 · 最近 10 日</label>
            <div className="cand-state-list">
              {c.state10.map((x, i) => (
                <span key={i} className={x.includes('上涨') ? 'st-up' : 'st-dn'}>{x}</span>
              ))}
            </div>
          </div>
        )}
        <RuleBlock c={c}/>
        {onAddToPool && (
          <div className="cand-addbar">
            <button className={`solid-btn cand-addbtn${inPool?.(c.code) ? ' is-added' : ''}`}
              onClick={() => onAddToPool(c)} disabled={!!inPool?.(c.code)}>
              <Icon name="wallet" size={15}/> {inPool?.(c.code) ? '已在个人池' : '加入个人池'}
            </button>
            <span>加入后到「个人池」按真实 A 股规则模拟买卖（T+1、一手、佣金与印花税）。</span>
          </div>
        )}
        <p className="drawer-update">
          {c.rules?.entry_mode === 'pivot'
            ? '入场价与止损都取自关键点本身（书中「价格抵达关键点并有效突破的那一刻」「从来没有跌到关键点以下」）；关键点与现价的距离不足 1×ATR 时已标出噪声风险。'
            : c.rules?.entry_mode === 'structure'
            ? '按书里第四章：建仓区间是现价 ±2%，止损位就是区间下沿，区间内不谈止损；退出用「硬性止损 + 浮动止盈」。这个席位还要求你自己有书面计划。'
            : c.rules?.entry_mode === 'valuation'
            ? '这个席位不按价格进出：容错线 −15% 是判断错误的认错线，退出看估值（PE 或股息率），持仓按季度算。'
            : '形态与阶段由日线规则自动判定；止损距离按 ATR 动态计算，2.5%–8% 之外会标注风险等级。'}
          所有价位为研究记录，不构成买卖建议。
        </p>
      </section>
    </div>
  );
}

export function CandidateBoard({ candidates, onAddToPool, inPool }: {
  candidates: Candidate[];
  onAddToPool?: (c: Candidate) => void;
  inPool?: (code: string) => boolean;
}) {
  const [pool, setPool] = useState<PoolKey | 'all'>('all');
  const [open, setOpen] = useState<Candidate | null>(null);
  const list = useMemo(
    () => (pool === 'all' ? candidates : candidates.filter(c => c.pool === pool)),
    [candidates, pool]);
  const counts = useMemo(() => {
    const m: Record<string, number> = { all: candidates.length, short: 0, swing: 0, value: 0 };
    candidates.forEach(c => { m[c.pool] += 1; });
    return m;
  }, [candidates]);

  return (
    <>
      <section className="heading-row">
        <div>
          <div className="eyebrow"><span>04</span> / CANDIDATE POOL</div>
          <h1>今日<span className="accent">研究候选</span></h1>
          <p className="section-summary">按持仓周期分池。点任意一只，查看 K 线、量价状态与入场 / 离场规则。</p>
        </div>
      </section>

      <section className="metrics metrics-seats">
        {POOLS.map(p => (
          <div className={`metric${p.key === 'value' ? ' metric-last' : ''}`} key={p.key}>
            <div className="metric-label">
              <span className={`metric-icon${p.key === 'short' ? ' orange' : p.key === 'swing' ? ' green' : p.key === 'value' ? ' purple' : ''}`}>
                <Icon name="target" size={17}/>
              </span>
              {p.label}
            </div>
            <div className="metric-bottom"><strong>{counts[p.key] ?? 0}</strong><small>只</small></div>
            <div className="metric-foot">{p.hint}</div>
          </div>
        ))}
      </section>

      <div className="cand-toolbar">
        {POOLS.map(p => (
          <button key={p.key} className={`cand-tab ${pool === p.key ? 'is-on' : ''}`} onClick={() => setPool(p.key)}>
            {p.label}<span>{counts[p.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {list.length === 0
        ? <div className="seat-notice"><Icon name="info" size={18}/><span>这个池子今天没有候选。闸门关着不代表系统失灵——多数交易日只有一两个席位会开闸。</span></div>
        : <div className="cand-grid">
            {list.map(c => (
              <button className="cand-card" key={c.code} onClick={() => setOpen(c)}
                title={c.rules?.risk_flag && c.rules.risk_flag !== '正常' ? c.rules.risk_flag : undefined}>
                <div className="cand-card-top">
                  <span className="cand-name">{c.name}<small>{c.code}</small></span>
                  <span className="cand-tags">
                    {inPool?.(c.code) && <span className="cand-inpool">已加入</span>}
                    <span className={`cand-pool pool-${c.pool}`}>{c.poolLabel}</span>
                  </span>
                </div>
                <div className="cand-card-mid">
                  {c.stage && <span className="cand-stage sm">{c.stage}</span>}
                  <span className="cand-seat">{c.seat}</span>
                </div>
                <div className="cand-card-foot">
                  {c.rules?.stop != null
                    ? <>止损 <b>{c.rules.stop}</b>（−{c.rules.risk_pct?.toFixed(2)}%）</>
                    : '无规则数据'}
                  <Icon name="chevron" size={13}/>
                </div>
                {c.rules?.risk_flag && c.rules.risk_flag !== '正常' && (
                  <span className="cand-flag">{c.rules.risk_flag}</span>
                )}
              </button>
            ))}
          </div>}

      <div className="disclosure" style={{ marginTop: 22 }}>
        <Icon name="shield" size={16}/>
        <span>候选来自各席位独立判断，席位之间不投票、不折中。止盈止损以席位自己的规则为准（利弗莫尔用关键点本身），席位没给时按个股 ATR 兜底；所有价位为研究记录，不构成买卖建议。</span>
      </div>

      {open && <CandidateDetail c={open} onClose={() => setOpen(null)} onAddToPool={onAddToPool} inPool={inPool}/>}
    </>
  );
}
