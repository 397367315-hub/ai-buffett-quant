import type { MarketGate } from '../types';
import { Icon } from './Icons';

export function MarketGateBar({ gate }: { gate: MarketGate }) {
  const p = gate.primary;
  const cls = p.open ? 'is-open' : 'is-closed';
  const metrics = Object.entries(p.metrics).filter(([, v]) => v != null);
  return (
    <div className={`mgate ${cls}`}>
      <div className="mgate-main">
        <span className="mgate-tag"><Icon name="shield" size={14}/> 剑门席位参考</span>
        <strong className="mgate-state">{p.state}{p.open ? ' · 本席开放' : ' · 本席不参与'}</strong>
        <span className="mgate-src">{p.source}</span>
        {metrics.length > 0 && (
          <span className="mgate-metrics">
            {metrics.map(([k, v]) => <span key={k}>{k} <b>{v}</b></span>)}
          </span>
        )}
      </div>
      <div className="mgate-side">
        <span className="mgate-alt">参照 · {gate.secondary.source}：<b>{gate.secondary.state}</b></span>
        <span className="mgate-bt">各席独立研究；本参考仅约束剑门席位</span>
      </div>
      <p className="mgate-reason">{p.reason}</p>
    </div>
  );
}
