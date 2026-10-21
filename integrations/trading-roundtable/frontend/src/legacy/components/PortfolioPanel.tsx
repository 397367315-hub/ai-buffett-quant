import { useMemo, useState } from 'react';
import type { PoolKey, QuantPageData } from '../types';
import { Icon } from './Icons';
import {
  FEES, calcFee, checkOrder, limitPrices, makeQuoteLookup, money, positionsOf, portfolioStats, r2, today,
} from '../portfolio';
import type { Ledger, Position, Side } from '../portfolio';

const POOLS: { key: PoolKey | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'short', label: '短期' },
  { key: 'swing', label: '波段' },
  { key: 'value', label: '长期价值' },
];

const VIEWS = [
  { key: 'watch', label: '自选', hint: '等着买的票' },
  { key: 'positions', label: '持仓', hint: '已经买了的' },
  { key: 'trades', label: '成交', hint: '每一笔都留痕' },
] as const;

type View = typeof VIEWS[number]['key'];

const pct = (v: number | null) => v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}%`;
const tone = (v: number | null) => v == null || v === 0 ? 'flat' : v > 0 ? 'up' : 'down';
const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${money(Math.abs(v))}`;

function maxBuyShares(cash: number, price: number): number {
  let n = Math.floor(cash / price / 100) * 100;
  while (n > 0) {
    const amount = n * price;
    if (amount + calcFee('buy', amount).total <= cash) break;
    n -= 100;
  }
  return Math.max(0, n);
}

interface Props {
  data: QuantPageData;
  ledger: Ledger;
  backend: 'server' | 'local';
  saving: boolean;
  onOrder: (req: { code: string; name: string; side: Side; shares: number; price: number; note?: string }) => string | null;
  onRemoveWatch: (code: string) => void;
  onReset: () => void;
}

export function PortfolioPanel({ data, ledger, saving, onOrder, onRemoveWatch, onReset }: Props) {
  const [view, setView] = useState<View>('watch');
  const [pool, setPool] = useState<PoolKey | 'all'>('all');
  const [order, setOrder] = useState<{ code: string; name: string; side: Side } | null>(null);
  const [confirming, setConfirming] = useState(false);

  const quoteOf = useMemo(() => makeQuoteLookup(data), [data]);

  const positions = useMemo(() => positionsOf(ledger, quoteOf), [ledger, quoteOf]);
  const posOf = (code: string) => positions.find(p => p.code === code);
  const stats = portfolioStats(ledger, positions);
  const candOf = (code: string) => data.candidates?.find(c => c.code === code);

  const watch = ledger.watch;
  const watchShown = pool === 'all' ? watch : watch.filter(w => w.pool === pool);
  const counts = { all: watch.length, short: 0, swing: 0, value: 0 } as Record<string, number>;
  watch.forEach(w => { counts[w.pool] = (counts[w.pool] ?? 0) + 1; });

  const openPrice = order ? quoteOf(order.code).last : null;
  const position = order ? posOf(order.code) : undefined;
  const day = today();

  return (
    <>
      <section className="heading-row">
        <div>
          <div className="eyebrow"><span>06</span> / MY POOL</div>
          <h1>我的<span className="accent">个人池</span></h1>
          <p className="section-summary">
            候选票加进来，按真实 A 股规则模拟买卖：T+1、100 股一手、佣金万 2.5（最低 5 元）、印花税千 0.5（仅卖出）、过户费万 0.1。
          </p>
        </div>
        <div className="heading-controls">
          <button className={`outline-btn${confirming ? ' pf-confirming' : ''}`}
            onClick={() => { if (confirming) { onReset(); setConfirming(false); } else setConfirming(true); }}
            title="清空自选、持仓与成交记录，资金回到初始">
            <Icon name="refresh" size={15}/> {confirming ? '再点一次确认重置' : '重置模拟盘'}
          </button>
        </div>
      </section>

      <section className="metrics metrics-seats">
        <div className="metric">
          <div className="metric-label"><span className="metric-icon"><Icon name="chart" size={17}/></span> 总资产</div>
          <div className="metric-bottom"><strong>{money(stats.total)}<i>元</i></strong></div>
          <div className="metric-foot">
            {stats.complete ? '现金 ＋ 持仓市值' : `现金 ＋ 已知市值（${stats.unpricedCount} 只无报价未计入）`}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label"><span className="metric-icon orange"><Icon name="layers" size={17}/></span> 持仓市值</div>
          <div className="metric-bottom"><strong>{money(stats.marketValue)}<i>元</i></strong></div>
          <div className="metric-foot">
            {positions.length} 只 · 成本 {money(stats.cost)}
            {stats.unpricedCount > 0 && ` · ${stats.unpricedCount} 只无报价`}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label"><span className="metric-icon green"><Icon name="database" size={17}/></span> 可用资金</div>
          <div className="metric-bottom"><strong>{money(stats.cash)}<i>元</i></strong></div>
          <div className="metric-foot">T+1 卖出后当日可用</div>
        </div>
        <div className="metric metric-last">
          <div className="metric-label"><span className="metric-icon purple"><Icon name="activity" size={17}/></span> 累计盈亏</div>
          <div className="metric-bottom"><strong className={`pf-${tone(stats.pnl)}`}>{signed(stats.pnl)}<i>元</i></strong></div>
          <div className="metric-foot">本金 {money(ledger.initial)} · {pct(stats.pnlPct)}</div>
        </div>
      </section>

      <div className="cand-toolbar">
        {VIEWS.map(v => (
          <button key={v.key} className={`cand-tab ${view === v.key ? 'is-on' : ''}`} onClick={() => setView(v.key)}>
            {v.label}<span>{v.key === 'watch' ? watch.length : v.key === 'positions' ? positions.length : ledger.trades.length}</span>
          </button>
        ))}
        <span className="pf-store">{saving ? '保存中…' : '本机浏览器保存'}</span>
      </div>

      {view === 'watch' && (
        <>
          <div className="pf-poolbar">
            {POOLS.map(p => (
              <button key={p.key} className={`pf-pool ${pool === p.key ? 'is-on' : ''}`} onClick={() => setPool(p.key)}>
                {p.label}<span>{counts[p.key] ?? 0}</span>
              </button>
            ))}
          </div>
          {watchShown.length === 0 ? (
            <div className="seat-notice"><Icon name="info" size={18}/><span>
              {watch.length === 0
                ? '个人池还是空的。去「今日研究候选」点开任意一只票，点「加入个人池」。'
                : '这个分池里还没有票。'}
            </span></div>
          ) : (
            <div className="cand-grid">
              {watchShown.map(w => {
                const q = quoteOf(w.code);
                const c = candOf(w.code);
                const chg = q.last != null && q.prevClose ? (q.last - q.prevClose) / q.prevClose * 100 : null;
                const pos = posOf(w.code);
                return (
                  <div className="cand-card pf-card" key={w.code}>
                    <div className="cand-card-top">
                      <span className="cand-name">{w.name}<small>{w.code}</small></span>
                      <span className={`cand-pool pool-${w.pool}`}>{w.poolLabel}</span>
                    </div>
                    <div className="pf-quote">
                      <strong>{q.last == null ? '—' : q.last.toFixed(2)}</strong>
                      <span className={`pf-chg pf-${tone(chg)}`}>{pct(chg)}</span>
                    </div>
                    <div className="cand-card-mid">
                      <span className="cand-seat">{w.seat}</span>
                      {c?.rules?.stop != null && <span className="pf-stop">止损 {c.rules.stop}</span>}
                      {pos && <span className="pf-hold">持 {pos.shares} 股</span>}
                    </div>
                    <div className="pf-actions">
                      <button className="pf-buy" disabled={q.last == null}
                        title={q.last == null ? '没有这只票的最新价，等下一次刷新' : undefined}
                        onClick={() => setOrder({ code: w.code, name: w.name, side: 'buy' })}>买入</button>
                      <button className="pf-remove" onClick={() => onRemoveWatch(w.code)}>移出自选</button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {view === 'positions' && (
        positions.length === 0
          ? <div className="seat-notice"><Icon name="info" size={18}/><span>还没有持仓。在「自选」里点一只票的「买入」开始。</span></div>
          : <div className="pf-positions">
              <div className="pf-phead">
                <span>股票</span><span>持仓</span><span>可卖</span><span>成本</span>
                <span>现价</span><span>市值</span><span>浮动盈亏</span><span>今日</span><span/>
              </div>
              {positions.map(p => (
                <div className="pf-prow" key={p.code}>
                  <span className="pf-pname">{p.name}<small>{p.code}</small></span>
                  <span>{p.shares}</span>
                  <span className={p.available < p.shares ? 'pf-locked' : ''}>{p.available}</span>
                  <span>{p.avgCost.toFixed(2)}</span>
                  <span>{p.last == null ? '—' : p.last.toFixed(2)}</span>
                  <span title={p.marketValue == null ? '当日无报价，市值未知' : undefined}>
                    {p.marketValue == null ? '—' : money(p.marketValue)}
                  </span>
                  <span className={`pf-${tone(p.pnl)}`}>
                    {p.pnl == null ? '—' : signed(p.pnl)}<small>{pct(p.pnlPct)}</small>
                  </span>
                  <span className={`pf-${tone(p.todayPnl)}`}>{signed(p.todayPnl)}</span>
                  <span>
                    <button className="pf-sell" onClick={() => setOrder({ code: p.code, name: p.name, side: 'sell' })}>卖出</button>
                  </span>
                </div>
              ))}
              <p className="pf-note">
                「可卖」已经扣掉今天买入的部分——T+1 下当日买入的股份第二个交易日才能卖。
                {day && ` 今天是 ${day}。`}
              </p>
            </div>
      )}

      {view === 'trades' && (
        ledger.trades.length === 0
          ? <div className="seat-notice"><Icon name="info" size={18}/><span>还没有成交记录。</span></div>
          : <div className="pf-positions">
              <div className="pf-phead pf-thead">
                <span>时间</span><span>方向</span><span>股票</span><span>价格</span>
                <span>股数</span><span>成交额</span><span>费用</span><span>现金变动</span><span/>
              </div>
              {[...ledger.trades].reverse().map(t => (
                <div className="pf-prow pf-trow" key={t.id}>
                  <span className="pf-time">{t.ts.slice(5, 16).replace('T', ' ')}</span>
                  <span className={t.side === 'buy' ? 'pf-up' : 'pf-down'}>{t.side === 'buy' ? '买入' : '卖出'}</span>
                  <span className="pf-pname">{t.name}<small>{t.code}</small></span>
                  <span>{t.price.toFixed(2)}</span>
                  <span>{t.shares}</span>
                  <span>{money(t.amount)}</span>
                  <span className="pf-fee" title={`佣金 ${money(t.fee.commission)} · 印花税 ${money(t.fee.stamp)} · 过户费 ${money(t.fee.transfer)}`}>
                    {money(t.fee.total)}
                  </span>
                  <span className={t.net > 0 ? 'pf-up' : 'pf-down'}>{signed(t.net)}</span>
                  <span/>
                </div>
              ))}
            </div>
      )}

      <div className="disclosure" style={{ marginTop: 22 }}>
        <Icon name="shield" size={16}/>
        <span>
          模拟盘，不接券商、不下单。成交价取面板最近一次行情快照
          {data.quoteDate ? `（${data.quoteDate.slice(0, 4)}-${data.quoteDate.slice(4, 6)}-${data.quoteDate.slice(6, 8)} 收盘，每 5 分钟刷新）` : ''}，
          不是逐笔行情，涨跌停价附近实际可能成交不了。
        </span>
      </div>

      {order && openPrice != null && (
        <OrderDialog
          order={order}
          price={openPrice}
          prevClose={quoteOf(order.code).prevClose}
          position={position}
          cash={ledger.cash}
          onClose={() => setOrder(null)}
          onSubmit={(shares, note) => {
            const err = onOrder({ ...order, shares, price: openPrice, note });
            if (!err) setOrder(null);
            return err;
          }}
          onCheck={(shares) => checkOrder(ledger, { ...order, shares, price: openPrice }, position)}
        />
      )}
    </>
  );
}

function OrderDialog({ order, price, prevClose, position, cash, onClose, onSubmit, onCheck }: {
  order: { code: string; name: string; side: Side };
  price: number; prevClose: number | null;
  position?: Position; cash: number;
  onClose: () => void;
  onSubmit: (shares: number, note?: string) => string | null;
  onCheck: (shares: number) => string | null;
}) {
  const buy = order.side === 'buy';
  const maxBuy = maxBuyShares(cash, price);
  const ceiling = position ? position.shares : 0;
  const [shares, setShares] = useState<number>(buy ? Math.min(maxBuy, 100) : (position?.available ?? 0));
  const [err, setErr] = useState<string | null>(null);
  // 《交易心理分析》的下单前自检：六项全部勾选才允许买入
  const PSY = [
    '我承认这是一笔概率交易，不是「它一定会涨」',
    '这笔亏掉在我的风险预算内，不会影响生活',
    '我按系统给的信号进场，不是因为怕错过',
    '我已经写下退出条件（价格或时间），并会执行',
    '这笔单子属于同一种做法，不是临时起意',
    '接下来 20 笔内我不改参数，先让样本说话',
  ];
  const [psy, setPsy] = useState<boolean[]>(() => PSY.map(() => false));
  const psyDone = psy.every(Boolean);
  const amount = r2(shares * price);
  const fee = calcFee(order.side, amount);
  const lim = prevClose ? limitPrices(prevClose, order.code, order.name) : null;
  const atUp = lim && price >= lim.up - 0.005;
  const atDown = lim && price <= lim.down + 0.005;
  const locked = position ? position.shares - position.available : 0;
  const t1Blocked = !buy && locked > 0 && (position?.available ?? 0) === 0;
  const liveErr = shares > 0 ? onCheck(shares) : null;
  const blocked = t1Blocked || shares <= 0 || !!liveErr;
  const chg = prevClose ? (price - prevClose) / prevClose * 100 : null;

  const quick: { label: string; v: number }[] = buy
    ? [
        { label: '1 手', v: 100 },
        { label: '1/4 仓', v: Math.floor(maxBuy / 400) * 100 },
        { label: '半仓', v: Math.floor(maxBuy / 200) * 100 },
        { label: '满仓', v: maxBuy },
      ]
    : [
        { label: '1/4', v: Math.floor((position?.available ?? 0) / 400) * 100 },
        { label: '1/3', v: Math.floor((position?.available ?? 0) / 300) * 100 },
        { label: '半仓', v: Math.floor((position?.available ?? 0) / 200) * 100 },
        { label: '全部', v: position?.available ?? 0 },
      ];

  return (
    <div className="drawer-scrim pf-scrim" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="pf-dialog" role="dialog" aria-modal="true" aria-label={`${buy ? '买入' : '卖出'} ${order.name}`}>
        <div className="drawer-top">
          <span>{buy ? 'BUY' : 'SELL'} / {order.code}</span>
          <button aria-label="关闭" className="icon-button" onClick={onClose}><Icon name="close" size={20}/></button>
        </div>
        <h2 className="pf-dtitle">{buy ? '买入' : '卖出'} {order.name}</h2>
        <div className="pf-dquote">
          <div><label>现价</label><strong>{price.toFixed(2)}</strong><span className={`pf-${tone(chg)}`}>{pct(chg)}</span></div>
          <div><label>涨停 / 跌停</label><strong>{lim ? `${lim.up.toFixed(2)} / ${lim.down.toFixed(2)}` : '—'}</strong></div>
          <div><label>{buy ? '可用资金' : '可卖股数'}</label><strong>{buy ? money(cash) : `${position?.available ?? 0}`}</strong></div>
        </div>

        <div className="pf-field">
          <label>股数</label>
          <input type="number" min={0} step={buy ? 100 : 1} value={shares || ''}
            placeholder={buy ? '100 的整数倍' : `最多可卖 ${position?.available ?? 0} 股`}
            onChange={e => { setShares(Math.max(0, Math.floor(Number(e.target.value) || 0))); setErr(null); }}/>
          <div className="pf-quick">{quick.map(q => (
            <button key={q.label} disabled={q.v <= 0} onClick={() => { setShares(q.v); setErr(null); }}>{q.label}</button>
          ))}</div>
        </div>

        <div className="pf-summary">
          <div><span>成交金额</span><b>{money(amount)}</b></div>
          <div><span>佣金 万{FEES.commissionRate * 10000}（最低 {FEES.commissionMin} 元）</span><b>{money(fee.commission)}</b></div>
          <div><span>印花税 {FEES.stampRate * 1000}‰{buy ? '（买入不收）' : ''}</span><b>{money(fee.stamp)}</b></div>
          <div><span>过户费 万{FEES.transferRate * 10000}</span><b>{money(fee.transfer)}</b></div>
          <div className="pf-total"><span>{buy ? '需付合计' : '实收金额'}</span><b>{money(buy ? amount + fee.total : amount - fee.total)}</b></div>
        </div>

        {t1Blocked && <p className="pf-error">T+1：这 {locked} 股是今天买的，A 股第二个交易日才能卖，明天再来。</p>}
        {atUp && buy && <p className="pf-warn">现价已在涨停价，实际很难买到，模拟按此价成交。</p>}
        {atDown && !buy && <p className="pf-warn">现价已在跌停价，实际很难卖出，模拟按此价成交。</p>}
        {(liveErr ?? err) && <p className="pf-error">{liveErr ?? err}</p>}

        {buy && (
          <div className="pf-psy">
            <div className="pf-psy-head">
              <b>下单前自检</b>
              <span>来自《交易心理分析》：用概率的方式思考，先接受风险</span>
            </div>
            {PSY.map((t, i) => (
              <label key={i} className={`pf-psy-item${psy[i] ? ' is-on' : ''}`}>
                <input type="checkbox" checked={psy[i]}
                  onChange={e => setPsy(p => p.map((v, k) => k === i ? e.target.checked : v))}/>
                <span>{t}</span>
              </label>
            ))}
          </div>
        )}

        <div className="pf-dactions">
          <button className="outline-btn" onClick={onClose}>取消</button>
          <button className={`solid-btn ${buy ? '' : 'pf-sell-btn'}`} disabled={blocked || (buy && !psyDone)} onClick={() => {
            const checked = onCheck(shares);
            setErr(checked ?? onSubmit(shares) ?? null);
          }}>确认{buy ? '买入' : '卖出'} · {shares || 0} 股</button>
        </div>
        <p className="pf-dnote">
          {ceiling > 0 && !buy ? '卖出后剩余不足 100 股的部分必须一次卖掉。' : ''}
          价格取最近快照，非交易时段按该价成交并计入账本。
        </p>
      </section>
    </div>
  );
}
