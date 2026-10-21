import type { FunnelStage } from '../types';

type Props = {
  stages: FunnelStage[];
  selected: number;
  visible: number;
  playing: boolean;
  paused: boolean;
  onSelect: (index: number) => void;
};

const cx = 430;
const starts = [94, 217, 340, 463, 586];
const height = 105;
const formatter = new Intl.NumberFormat('en-US');

export function FunnelDiagram({ stages, selected, visible, playing, paused, onSelect }: Props) {
  // 数据驱动的对数压缩：最终层保证最小视觉宽度，不会因 80 / 4600 的比例缩成针尖。
  const counts = stages.map(s => Math.max(0, s.count));
  const maxLog = Math.log1p(Math.max(1, ...counts));
  const minLog = Math.log1p((counts.length ? Math.min(...counts) : 0));
  const span = maxLog - minLog;
  const stageWidths = stages.map((s, i) => {
    const ratio = span > 0 ? (Math.log1p(Math.max(0, s.count)) - minLog) / span : 1;
    return Math.max(372, Math.round(418 + ratio * 320 - i * 5));
  });
  return (
    <div className={`funnel-stage-wrap ${playing && !paused ? 'is-running' : ''}`}>
      <div className="funnel-topline">
        <span className="micro-label"><span className="signal-ping"/> QUANT FILTER ENGINE</span>
        <span className="funnel-topline-right">LOGARITHMIC VIEW <span className="line-sep">/</span> 05 LAYERS</span>
      </div>
      <svg className="funnel-svg" viewBox="0 0 860 800" role="img" aria-label="五层可交互动态筛选漏斗，点击层级查看规则">
        <defs>
          <linearGradient id="layerBlue" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0%" stopColor="#28486c"/><stop offset="30%" stopColor="#162e4e"/>
            <stop offset="63%" stopColor="#12253e"/><stop offset="100%" stopColor="#284a70"/>
          </linearGradient>
          <linearGradient id="layerMid" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0%" stopColor="#3d5672"/><stop offset="50%" stopColor="#21364d"/><stop offset="100%" stopColor="#4a6680"/>
          </linearGradient>
          <linearGradient id="layerGold" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0%" stopColor="#d38d43"/><stop offset="48%" stopColor="#84501c"/><stop offset="100%" stopColor="#f0b15c"/>
          </linearGradient>
          <linearGradient id="rimBlue" x1="0" x2="1"><stop offset="0" stopColor="#4b8cbd" stopOpacity=".17"/><stop offset=".5" stopColor="#c9ecff"/><stop offset="1" stopColor="#4b8cbd" stopOpacity=".17"/></linearGradient>
          <linearGradient id="rimGold" x1="0" x2="1"><stop offset="0" stopColor="#ffcb81" stopOpacity=".1"/><stop offset=".5" stopColor="#ffe0a0"/><stop offset="1" stopColor="#ffc16a" stopOpacity=".15"/></linearGradient>
          <linearGradient id="verticalBeam" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#75c5fa" stopOpacity="0"/><stop offset=".4" stopColor="#78cbfd" stopOpacity=".5"/><stop offset=".8" stopColor="#e5b76c" stopOpacity=".7"/><stop offset="1" stopColor="#ffb65d" stopOpacity="0"/></linearGradient>
          <filter id="softGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="8"/></filter>
          <filter id="warmGlow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="13"/></filter>
          <pattern id="finGrid" width="42" height="42" patternUnits="userSpaceOnUse"><path d="M42 0H0V42" stroke="#9dc1d8" strokeOpacity=".055" fill="none"/></pattern>
        </defs>
        <rect x="0" y="0" width="860" height="800" fill="url(#finGrid)"/>
        <g className="funnel-deco" stroke="#5c7489" fill="none" strokeOpacity=".18">
          <circle cx="430" cy="414" r="334" strokeDasharray="3 12"/>
          <circle cx="430" cy="414" r="258" strokeDasharray="2 16"/>
          <path d="M430 28v730M50 414h760" strokeDasharray="3 14"/>
        </g>
        <rect x="392" y="75" width="76" height="621" rx="38" fill="url(#verticalBeam)" opacity=".26" filter="url(#warmGlow)" className="funnel-beam"/>
        {stages.map((stage, index) => {
          const top = stageWidths[index] ?? 400;
          const bottom = top - (index === 0 ? 75 : index === 1 ? 77 : 30);
          const y = starts[index];
          const x1 = cx - top / 2, x2 = cx + top / 2;
          const xb1 = cx - bottom / 2, xb2 = cx + bottom / 2;
          const path = `M ${x1} ${y} L ${x2} ${y} L ${xb2} ${y + height} Q ${cx} ${y + height + 17} ${xb1} ${y + height} Z`;
          const hot = index === selected;
          const final = index === stages.length - 1;
          const isShown = index < visible;
          return (
            <g key={stage.id} className={`funnel-layer ${hot ? 'selected' : ''} ${isShown ? 'has-entered' : 'not-entered'} ${final ? 'is-final' : ''}`} role="button" tabIndex={0} aria-label={`${stage.id} ${stage.title} ${stage.count} 只，点击查看`} onClick={() => onSelect(index)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(index); } }}>
              <path d={path} fill={final ? 'url(#layerGold)' : index >= 2 ? 'url(#layerMid)' : 'url(#layerBlue)'} stroke={hot ? '#ffd48a' : final ? '#b8854c' : '#557899'} strokeWidth={hot ? 3 : 1.5} opacity={isShown ? 1 : .20}/>
              {hot && <path d={path} fill="none" stroke={final ? '#ffb654' : '#82cdfa'} strokeWidth="5" opacity=".42" filter="url(#softGlow)"/>}
              <ellipse cx={cx} cy={y + 1} rx={top / 2} ry="13" fill={final ? '#5b391b' : '#213d59'} stroke={final ? 'url(#rimGold)' : 'url(#rimBlue)'} strokeWidth="3.2" opacity={isShown ? 1 : .2}/>
              <ellipse cx={cx} cy={y + 3} rx={top / 2 - 18} ry="5" fill="none" stroke={final ? '#ffcc76' : '#9cdcff'} strokeWidth="1" opacity=".45"/>
              <path d={`M${xb1 + 12} ${y + height - 3} Q${cx} ${y + height + 12} ${xb2 - 12} ${y + height - 3}`} stroke={final ? '#f5c377' : '#91b7d4'} opacity=".38" fill="none" strokeWidth="2"/>
              <text x={x1 + 42} y={y + 62} fontSize="15" fontWeight="800" fill={final ? '#ffe4b0' : '#95bcd7'} letterSpacing="1.2">{stage.id}</text>
              <text x={cx} y={y + 63} fontSize="46" fontWeight="750" fill="#fff" textAnchor="middle" className="funnel-count-text">{isShown ? formatter.format(stage.count) : '—'}</text>
              <text x={cx} y={y + 92} fontSize="15" fontWeight="500" fill={final ? '#ffe3b8' : '#c0d0dd'} textAnchor="middle" letterSpacing="2">{stage.title} <tspan fillOpacity=".65">· 只股票</tspan></text>
              <path d={`M ${x2 + 13} ${y + 52} h 16`} stroke={hot ? '#ffd18a' : '#567994'} strokeWidth="1.5" strokeDasharray="2 4"/>
              <circle cx={x2 + 31} cy={y + 52} r={hot ? 4.5 : 3} fill={hot ? '#f0b667' : '#547c9a'}/>
            </g>
          );
        })}
        {Array.from({length: 13}, (_, i) => {
          const x = 370 + ((i * 29) % 128);
          const delay = (i * .33) % 3.2;
          return <circle key={i} className="flow-particle" cx={x} cy={50 + (i%4)*25} r={i%3===0 ? 2.9 : 1.7} fill={i%2===0 ? '#f6cb7b' : '#8ad8ff'} style={{ animationDelay: `-${delay}s`, animationPlayState: paused ? 'paused' : 'running' }}/>
        })}
        <g className="funnel-exit">
          <ellipse cx="430" cy="735" rx="205" ry="28" stroke="#ba8142" strokeWidth="1.5" strokeDasharray="6 10" fill="none"/>
          <ellipse cx="430" cy="735" rx="154" ry="18" stroke="#cfa368" strokeOpacity=".65" fill="none"/>
          <ellipse cx="430" cy="735" rx="104" ry="10" fill="#d7a65e" opacity=".22" filter="url(#softGlow)"/>
          <text x="430" y="769" fontSize="13" textAnchor="middle" fill="#cba66e" letterSpacing="5">FINAL WATCHLIST</text>
        </g>
      </svg>
      <div className="funnel-footer-caption"><span>CLICK A LAYER TO INSPECT</span><span>视觉宽度采用对数压缩</span></div>
    </div>
  );
}
