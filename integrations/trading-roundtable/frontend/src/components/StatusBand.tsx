import { useState } from 'react';
import { ApiError, errorText, get, newKey, runJob, type Job } from '../api/v1';
import type { HealthData, MarketSnapshot } from '../api/types';
import { lastKnownSnapshot, useApp, useSession } from '../lib/hooks';
import { BAR_STATUS_LABEL, FRESHNESS_LABEL, QUALITY_LABEL, SOURCE_MODE_LABEL, freshnessText, label } from '../lib/labels';
import { dateOnly, shanghai, shortId } from '../lib/format';
import { Drawer, JobProgress, KV, Tag } from './ui';

/** 「今天还没有行情」类错误：改采服务端给出的最近完整交易日。 */
const NO_SESSION_YET: Record<string, (today: string) => string> = {
  NON_TRADING_SESSION: t => `今天（${t}）不是交易日`,
  SESSION_NOT_STARTED: t => `今天（${t}）还没开盘`,
  SOURCE_EMPTY: t => `数据源还没有今天（${t}）的日线`,
};

/** 模式字：数据从哪来，一眼看清（合成测试 / 数据源 / 本机归档 / 混合）。 */
function modeWord(md: MarketSnapshot['metadata'] | undefined): string {
  if (!md) return '无数据';
  if (md.is_synthetic) return '合成测试数据';
  return ({ provider_api: '数据源快照', file_import: '本机历史归档', mixed: '数据源＋本机缓存', user_manual: '手工录入' } as
    Record<string, string>)[md.source_mode] ?? '真实快照';
}

function freshnessTone(f: string | undefined): 'ok' | 'warn' | 'bad' {
  return f === 'fresh' ? 'ok' : f === 'stale' ? 'bad' : 'warn';
}

/** 顶部数据状态：数据截至哪天、什么时候取的、谁生成的、质量与缺口——每页常驻。 */
export function StatusBand() {
  const app = useApp();
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const snap = app.snapshot;
  const md = snap?.metadata;
  const replay = session?.mode === 'replay';
  const unavailable = !snap && !!(app.snapshotError || app.healthError);
  const state = unavailable ? 'unavailable' : !snap ? 'empty' : replay ? 'replay' : md?.freshness;
  const bar = snap ? snap.daily_bars.find(b => b.session_date === md?.session_date) : null;
  const last = unavailable ? lastKnownSnapshot() : null;

  const collect = async () => {
    setBusy(true); setErr(null); setNote(null);
    try {
      // 「今天」按服务端时钟（health.checked_at 的上海日期），不用浏览器时钟猜
      const h0 = await get<HealthData>('/api/v1/health/data');
      const today = dateOnly(h0.checked_at);
      const run = (day: string) => runJob('/api/v1/snapshots', { session_date: day, source_mode: 'provider_api', symbols: [],
                                                                  allow_stale: true }, newKey('collect-snapshot'), setJob);
      try {
        // 先要今天：交易时段内得到盘中快照，收盘后得到当天完整日线
        await run(today);
      } catch (e) {
        if (!(e instanceof ApiError) || !NO_SESSION_YET[e.code]) throw e;
        // 今天没有行情（休市 / 未开盘 / 还没发布）：这次请求已让服务端从数据源取回交易日历，
        // 由服务端给出「最近完整交易日」再采一次——不在浏览器里推算交易日
        const h1 = await get<HealthData>('/api/v1/health/data');
        const day = h1.latest_completed_session_date;
        if (!day || day >= today) throw e;
        await run(day);
        setNote(`${NO_SESSION_YET[e.code](today)}，已改为采集最近完整交易日 ${day}。`);
      }
      await app.refreshAll();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  return (
    <header className={`statusband${replay ? ' is-replay' : ''}${unavailable ? ' is-unavailable' : ''}`}
            data-testid="status-band" data-ui-data-state={state} data-demo="false"
            data-snapshot-id={snap?.snapshot_id ?? ''} data-session-mode={session?.mode ?? 'none'}>
      <div className="stamp">
        <span className="stamp-label">{replay ? '历史训练：知识截点' : '数据截至（行情所属交易日）'}</span>
        {replay ? (
          <span className="stamp-value" data-testid="cutoff">{shanghai(session?.knowledge_cutoff_at)}</span>
        ) : md?.quote_date ? (
          <span className="stamp-value" data-testid="quote-date">{md.quote_date}</span>
        ) : (
          <span className="stamp-value is-empty" data-testid="quote-date">{unavailable ? '数据不可用' : '还没有快照'}</span>
        )}
      </div>
      <dl className="meta">
        <div><dt>模式</dt><dd className="mode-word" data-testid="mode-word">
          {replay ? `历史训练（${shortId(session?.replay_id)}）` : snap ? modeWord(md) : '无数据'}
        </dd></div>
        {snap ? <>
          <div><dt>快照</dt><dd title={snap.snapshot_id}>{shortId(snap.snapshot_id, 8)}</dd></div>
          <div><dt>获取于</dt><dd>{shanghai(md?.captured_at)}</dd></div>
          <div><dt>生成于</dt><dd>{shanghai(md?.generated_at)}</dd></div>
          <div><dt>新鲜度</dt><dd><Tag tone={freshnessTone(md?.freshness)} testid="freshness" title={md?.freshness_reason}>
            {freshnessText(md?.freshness, md?.freshness_reason)}</Tag></dd></div>
          <div><dt>质量</dt><dd><Tag tone={md?.quality === 'complete' ? 'ok' : md?.quality === 'partial' ? 'warn' : 'bad'}
            testid="quality">{label(QUALITY_LABEL, md?.quality)}</Tag></dd></div>
          {bar ? <div><dt>日线</dt><dd>{label(BAR_STATUS_LABEL, bar.bar_status)}</dd></div> : null}
        </> : null}
        {unavailable ? <div><dt>原因</dt><dd className="unknown" data-testid="unavailable-reason">
          {errorText(app.snapshotError ?? app.healthError)}</dd></div> : null}
        {unavailable && last ? <div><dt>上次成功的数据</dt><dd data-testid="last-known">{last.quote_date ?? '--'}（获取于 {shanghai(last.captured_at)}；
          现在不能据此执行）</dd></div> : null}
      </dl>
      <div className="band-actions">
        {snap ? <button className="btn secondary small" onClick={() => setOpen(true)}>来源与缺口</button> : null}
        {!replay ? <button className="btn small" onClick={collect} disabled={busy} data-testid="collect"
                           title="点一次取一次，不在后台自动刷新。交易时段内取到的是盘中快照（未收盘），收盘后再点一次得到当天完整日线。">
          {busy ? '采集中…' : '采集最新快照'}</button> : null}
        <JobProgress job={busy ? job : null} />
        {note && !err ? <span className="note" role="status" data-testid="collect-note">{note}</span> : null}
        {err ? <span className="unknown" role="alert" data-testid="collect-error">{errorText(err)}</span> : null}
      </div>
      {open && snap ? <SourceDrawer snap={snap} stale={app.snapshotStale} onClose={() => setOpen(false)} /> : null}
    </header>
  );
}

function SourceDrawer({ snap, stale, onClose }: { snap: MarketSnapshot; stale: boolean; onClose: () => void }) {
  const md = snap.metadata;
  const byRole: Record<string, number> = {};
  snap.evidence.forEach(e => { byRole[e.source_ref.source_id] = (byRole[e.source_ref.source_id] ?? 0) + 1; });
  return (
    <Drawer title="来源与缺口" onClose={onClose}>
      <KV items={[
        ['快照 ID', <code>{snap.snapshot_id}</code>],
        ['行情所属交易日', md.quote_date ?? '无'],
        ['会话日', md.session_date],
        ['最近完整交易日', md.latest_completed_session_date ?? '日历不覆盖'],
        ['获取于（上海）', shanghai(md.captured_at, true)],
        ['生成于（上海）', shanghai(md.generated_at, true)],
        ['来源方式', label(SOURCE_MODE_LABEL, md.source_mode)],
        ['精度', md.feed_precision],
        ['新鲜度', `${label(FRESHNESS_LABEL, md.freshness)}：${md.freshness_reason}${stale ? '（读取时按当前日历重估）' : ''}`],
        ['质量', label(QUALITY_LABEL, md.quality)],
        ['价格口径', `${md.price_basis}（${md.adjustment_policy_version ?? '未登记'}）`],
        ['交易日历', md.calendar_version],
        ['候选股票池', `${md.universe_policy_id}`],
        ['合成数据', md.is_synthetic ? '是（测试/演示，不是市场事实）' : '否'],
      ]} />
      <h3 style={{ marginTop: 16 }}>缺失的输入</h3>
      {md.missing_fields.length ? (
        <ul>{md.missing_fields.map(m => <li key={m}><span className="unknown">{m}</span></li>)}</ul>
      ) : <p className="note">没有登记的缺失项。</p>}
      <h3>证据来源（按来源计数）</h3>
      <ul>{Object.entries(byRole).map(([k, v]) => <li key={k}>{k}：{v} 条</li>)}</ul>
      {snap.conflicts.length ? <>
        <h3>来源冲突（保留双方证据，不取均值）</h3>
        <ul>{snap.conflicts.slice(0, 20).map((c: any) => <li key={c.conflict_id}>{c.field_path}：{c.reason}</li>)}</ul>
      </> : null}
      <p className="note">快照内容不可改：刷新会生成新的快照 ID，已冻结的研究与计划继续引用原来的快照。</p>
    </Drawer>
  );
}
