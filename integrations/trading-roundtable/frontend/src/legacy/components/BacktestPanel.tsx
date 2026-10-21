import type { Backtest } from '../types';
import { Icon } from './Icons';

const n = (v: number) => new Intl.NumberFormat('zh-CN').format(v);
const pct = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}%`;
const tCls = (t: number) => (t <= -1.645 ? 'is-bad' : t >= 1.645 ? 'is-good' : '');

export function BacktestPanel({ data }: { data: Backtest }) {
  const maxAbs = Math.max(0.5, ...data.rows.map(r => Math.abs(r.excess)));
  const negative = data.rows.filter(r => r.t <= -1.645).length;
  const beat = data.rows.filter(r => r.excess > 0).length;

  return (
    <>
      <section className="heading-row">
        <div>
          <div className="eyebrow"><span>05</span> / VALIDATION</div>
          <h1>这些席位<span className="accent">到底赚不赚钱</span></h1>
          {data.status === 'legacy_unverified' && (
            <p className="bt-pending"><b>待复核</b> · {data.status_note ?? '本页数字来自手工脚本运行，未走 v1 验证流程'}</p>
          )}
          <p className="section-summary">
            跑的是各席位真实的筛选脚本：T+1 开盘买入、T+5 收盘卖出，等权，扣掉双边 0.06% 成本，跟同一天的全市场等权比。
          </p>
        </div>
      </section>

      <section className="metrics metrics-seats">
        <div className="metric">
          <div className="metric-label"><span className="metric-icon"><Icon name="activity" size={17}/></span> 采样窗口</div>
          <div className="metric-bottom"><strong>42 / 22</strong><small>采样日 / 快照</small></div>
          <div className="metric-foot">{data.window}</div>
        </div>
        <div className="metric">
          <div className="metric-label"><span className="metric-icon orange"><Icon name="chart" size={17}/></span> 同期基准</div>
          <div className="metric-bottom"><strong>{data.baseline.toFixed(2)}<i>%</i></strong></div>
          <div className="metric-foot">全市场 5 日等权均值</div>
        </div>
        <div className="metric">
          <div className="metric-label"><span className="metric-icon green"><Icon name="layers" size={17}/></span> 跑赢基准</div>
          <div className="metric-bottom"><strong>{beat}</strong><small>/ {data.rows.length} 席</small></div>
          <div className="metric-foot">只有一路奔行贴着 0</div>
        </div>
        <div className="metric metric-last">
          <div className="metric-label"><span className="metric-icon purple"><Icon name="shield" size={17}/></span> 显著跑输</div>
          <div className="metric-bottom"><strong className="metric-highlight">{negative}</strong><small>席</small></div>
          <div className="metric-foot">t ≤ −1.645</div>
        </div>
      </section>

      <section className="bt-panel">
        <div className="panel-heading">
          <div><span className="panel-kicker dark">CANDIDATE ALPHA</span><h2>每个席位的候选，跑赢市场了吗</h2></div>
          <span className="panel-counter">{data.rows.length} SEATS</span>
        </div>
        <table className="bt-table">
          <thead>
            <tr>
              <th>席位</th><th className="num">采样日</th><th className="num">平均候选</th>
              <th>候选超额</th><th className="num">胜率</th><th className="num">t</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map(r => (
              <tr key={r.seat}>
                <td>

                  {r.seat}
                  {r.note && <span className="bt-rownote">{r.note}</span>}
                </td>
                <td className="num">{n(r.days)}</td>
                <td className="num">{r.avgCand}</td>
                <td className={`bt-diff ${r.excess < 0 ? 'neg' : ''}`}>
                  <span className="bt-bar"><i style={{ width: `${Math.abs(r.excess) / maxAbs * 100}%` }}/></span>
                  {pct(r.excess)}
                </td>
                <td className="num">{r.win.toFixed(1)}%</td>
                <td className={`num ${tCls(r.t)}`}>{r.t.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="bt-note">{data.note}</p>
      </section>

      {data.gate && data.gate.length > 0 && (
        <section className="bt-panel">
          <div className="panel-heading">
            <div><span className="panel-kicker dark">GATE ONLY</span><h2>只看闸门：开闸之后市场是不是更好</h2></div>
          </div>
          <table className="bt-table">
            <thead>
              <tr><th>席位</th><th className="num">开闸天数</th><th className="num">开闸 − 关闸</th><th className="num">t</th></tr>
            </thead>
            <tbody>
              {data.gate.map(g => (
                <tr key={g.seat}>
                  <td>{g.seat}</td>
                  <td className="num">{n(g.days)}</td>
                  <td className={`num ${g.excess < 0 ? 'is-bad' : ''}`}>{pct(g.excess)}</td>
                  <td className={`num ${tCls(g.t)}`}>{g.t.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="bt-note">闸门看的是「今天能不能做」，不是「买哪只」。这两个问题在这套系统里第一次被分开测。</p>
        </section>
      )}

      <div className="bt-verdict">
        <span className="bt-verdict-title">没有一个席位跑赢市场 · 但闸门有话说</span>
        <p>{data.verdict}</p>
        <p>
          这份结果和面板上原来的那张表不一样：原来那张用的是「A 区占比」「SOS 数」这类指标做代理，
          这一张跑的是席位真正的筛选脚本。代理能算出好看的数，真脚本算不出来。
        </p>
      </div>
    </>
  );
}
