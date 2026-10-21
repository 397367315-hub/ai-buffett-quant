import type { StrategySeat } from '../types';
import type { CSSProperties } from 'react';
import { Icon } from './Icons';
import { assetUrl } from '../../lib/assets';

export const badgeText: Record<StrategySeat['gate'],string> = {
  OPEN: '可开', CLOSED: '不开', MIXED: '混合', WEAK: '弱', NO_SIGNAL: '非冰点', UNCONFIGURED: '待配置',
};

export function SeatCard({ seat, index, onSelect, animating = false }: { seat: StrategySeat; index: number; onSelect: () => void; animating?: boolean }) {
  return (
    <button type="button" className={`seat-card ${seat.configured ? '' : 'seat-card-empty'} ${animating || seat.runState === 'RUNNING' ? 'is-animating' : ''}`} style={{ '--delay': `${index * 35}ms` } as CSSProperties} onClick={onSelect}>
      <div className={`seat-portrait ${seat.portrait ? '' : 'is-placeholder'}`}>
        {seat.portrait ? <img src={assetUrl(seat.portrait)} alt={`${seat.name}的迷你交易员场景`} loading={index < 2 ? 'eager' : 'lazy'} /> : <div className="placeholder-portrait"><div className="ghost-person"><span className="ghost-head"/><span className="ghost-body"/></div><div className="ghost-monitor"><Icon name="chart" size={24}/></div></div>}
        <span className="seat-index">SEAT {String(index + 1).padStart(2, '0')}</span>
        {seat.configured && <span className="seat-monitor-dots"><i/><i/><i/></span>}
      </div>
      <div className="seat-card-info"><span className="agent-overline">INDEPENDENT STRATEGY AGENT</span>
        <div className="seat-card-top"><span className="seat-name">{seat.name}<small>{seat.subtitle && ` · ${seat.subtitle}`}</small></span><span className={`seat-status status-${seat.gate.toLowerCase()}`}>{badgeText[seat.gate]}</span></div>
        <div className="seat-tags">{seat.keywords.map(k => <span key={k}>{k}</span>)}</div>
        <p className="seat-explanation">{seat.reasoning}</p>
        <div className="seat-card-bottom"><span className="seat-version" title={seat.version}>{seat.version}</span><span className="seat-result"><span>候选</span><strong>{seat.count === null ? '—' : seat.count}</strong><Icon name="chevron" size={17}/></span></div>
      </div>
    </button>
  );
}
