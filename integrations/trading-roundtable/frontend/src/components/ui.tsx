import { useEffect, useRef, type ReactNode } from 'react';
import { errorText, type Job } from '../api/v1';
import { JOB_STATE_LABEL, label } from '../lib/labels';

export function Tag({ tone, children, title, testid }: { tone: 'ok' | 'bad' | 'warn' | 'muted' | 'info' | 'seal';
  children: ReactNode; title?: string; testid?: string }) {
  return <span className={`tag ${tone}`} title={title} data-testid={testid}>{children}</span>;
}

export function Section({ title, hint, actions, children, id }: { title: string; hint?: ReactNode; actions?: ReactNode;
  children: ReactNode; id?: string }) {
  return (
    <section className="section" id={id} aria-label={title}>
      <header>
        <h2>{title}</h2>
        {hint ? <span className="hint">{hint}</span> : null}
        {actions ? <div className="row">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty" role="status"><strong>{title}</strong>{children}</div>;
}

export function ErrorNote({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  return (
    <div className="alert" role="alert" data-testid="error-note">
      {title ? <strong>{title}：</strong> : null}{errorText(error)}
    </div>
  );
}

export function Field({ label: lbl, hint, error, children }: { label: string; hint?: ReactNode; error?: string | null;
  children: ReactNode }) {
  return (
    <label className="field">
      <span className="lbl">{lbl}</span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
      {error ? <span className="err">{error}</span> : null}
    </label>
  );
}

export function JobProgress({ job }: { job: Job | null }) {
  if (!job) return null;
  const pct = job.total_units ? Math.min(100, Math.round((job.completed_units / job.total_units) * 100)) : null;
  return (
    <div className="progress" role="status" aria-live="polite" data-testid="job-progress">
      <span>任务 {label(JOB_STATE_LABEL, job.state)}{job.phase && job.phase !== job.state ? `（${job.phase}）` : ''}</span>
      {pct !== null ? <span className="bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span> : null}
      {pct !== null ? <span className="num">{job.completed_units}/{job.total_units}</span> : null}
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [onClose]);
}

/** 抽屉：Esc 关闭，关闭后焦点回到打开它的按钮。 */
export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(document.activeElement);
  useEscape(onClose);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('button, [href], input, select, textarea')?.focus();
    const back = opener.current;
    return () => { (back as HTMLElement | null)?.focus?.(); };
  }, []);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header><h2>{title}</h2><button className="btn quiet" onClick={onClose}>关闭</button></header>
        <div className="body">{children}</div>
      </aside>
    </>
  );
}

export function Dialog({ title, onClose, children, dirty, wide }: { title: string; onClose: () => void; children: ReactNode;
  dirty?: boolean; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(document.activeElement);
  const close = () => {
    if (dirty && !window.confirm('有未保存的内容，确定关闭吗？')) return;
    onClose();
  };
  useEscape(close);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('input, select, textarea, button')?.focus();
    const back = opener.current;
    return () => { (back as HTMLElement | null)?.focus?.(); };
  }, []);
  return (
    <>
      <div className="scrim" onClick={close} />
      <div className={`dialog${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="row spread"><h2>{title}</h2><button className="btn quiet" onClick={close}>关闭</button></div>
        {children}
      </div>
    </>
  );
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v]) => [<dt key={`k-${k}`}>{k}</dt>, <dd key={`v-${k}`}>{v ?? '--'}</dd>])}
    </dl>
  );
}
