'use client';

import Link from 'next/link';
import dynamic from 'next/dynamic';
import type { MouseEvent, ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { BarChart3, ExternalLink, Loader2, RefreshCw, X } from 'lucide-react';
import type { KlineRow } from '@/components/KlineChart';
import { apiFetch } from '@/lib/api';

const KlineChart = dynamic(() => import('@/components/KlineChart'), {
  ssr: false,
  loading: () => <div className="grid min-h-[300px] place-items-center text-xs text-text-secondary sm:min-h-[420px]"><Loader2 size={20} className="animate-spin text-accent" /></div>,
});

type KlineCategory = 4 | 5 | 6 | 11;

interface KlineData {
  stock_code: string;
  stock_name: string;
  category: KlineCategory;
  category_label: string;
  rows: KlineRow[];
  count: number;
  available: boolean;
  source: string;
  data_date: string | null;
  is_realtime: boolean;
  warning: string | null;
}

interface Props {
  code: string;
  name?: string;
  children?: ReactNode;
  className?: string;
  title?: string;
}

const CATEGORY_OPTIONS: Array<{ value: KlineCategory; label: string; offset: number }> = [
  { value: 4, label: '日K', offset: 120 },
  { value: 5, label: '周K', offset: 160 },
  { value: 6, label: '月K', offset: 120 },
  { value: 11, label: '60分钟', offset: 120 },
];

const responseCache = new Map<string, KlineData>();

function cacheKey(code: string, category: KlineCategory): string {
  return `${code.trim().toUpperCase()}:${category}`;
}

function sourceLabel(source: string): string {
  const normalized = String(source || '').toLowerCase();
  if (normalized.includes('eastmoney')) return '东方财富';
  if (normalized.includes('tencent')) return '腾讯行情';
  if (normalized.includes('cache')) return '系统缓存';
  return source || '来源未标注';
}

export default function StockKlineButton({ code, name, children, className = '', title }: Props) {
  const normalizedCode = String(code || '').trim();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<KlineCategory>(4);
  const [data, setData] = useState<KlineData | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const requestId = useRef(0);

  const close = useCallback(() => setOpen(false), []);

  const load = useCallback(async (nextCategory: KlineCategory, force = false) => {
    if (!normalizedCode) return;
    const key = cacheKey(normalizedCode, nextCategory);
    const cached = responseCache.get(key);
    setCategory(nextCategory);
    setError('');
    if (cached && !force) {
      setData(cached);
      setLoading(false);
      setProgress(100);
      return;
    }

    const currentRequest = ++requestId.current;
    const option = CATEGORY_OPTIONS.find((item) => item.value === nextCategory);
    setLoading(true);
    setProgress(12);
    try {
      const response = await apiFetch<{ data: KlineData }>(
        `/kline?code=${encodeURIComponent(normalizedCode)}&category=${nextCategory}&offset=${option?.offset || 120}`,
      );
      if (requestId.current !== currentRequest) return;
      responseCache.set(key, response.data);
      setData(response.data);
      setProgress(100);
    } catch (caught) {
      if (requestId.current !== currentRequest) return;
      setData(null);
      setError(caught instanceof Error ? caught.message : 'K线加载失败，请稍后重试');
    } finally {
      if (requestId.current === currentRequest) setLoading(false);
    }
  }, [normalizedCode]);

  const show = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (!normalizedCode) return;
    setOpen(true);
    void load(4);
  };

  useEffect(() => {
    if (!loading) return undefined;
    const timer = window.setInterval(() => {
      setProgress((current) => Math.min(92, current + Math.max(1, Math.round((92 - current) / 7))));
    }, 280);
    return () => window.clearInterval(timer);
  }, [loading]);

  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [close, open]);

  return (
    <>
      <button
        type="button"
        onClick={show}
        disabled={!normalizedCode}
        className={`text-left hover:text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/70 disabled:cursor-default disabled:opacity-60 ${className}`}
        title={title || `查看${name || normalizedCode}K线`}
      >
        {children ?? <>{name || normalizedCode}</>}
      </button>

      {open && typeof document !== 'undefined' && createPortal(
        <div
          className="fixed inset-0 z-[100] flex items-end justify-center bg-black/75 sm:items-center sm:p-4"
          onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label={`${name || normalizedCode}K线图`}
            className="flex max-h-[96dvh] w-full flex-col overflow-hidden rounded-t-md border border-border bg-card shadow-2xl sm:max-w-5xl sm:rounded-md"
          >
            <header className="shrink-0 border-b border-border bg-[#0D1117] px-3 py-3 sm:px-4">
              <div className="flex items-start gap-3">
                <BarChart3 size={18} className="mt-0.5 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-baseline gap-2">
                    <h2 className="truncate text-sm font-semibold text-text">{data?.stock_name || name || normalizedCode}</h2>
                    <span className="shrink-0 font-mono text-[11px] text-text-secondary">{data?.stock_code || normalizedCode}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-text-secondary">
                    <span>{data?.category_label || CATEGORY_OPTIONS.find((item) => item.value === category)?.label}</span>
                    <span>数据日 {data?.data_date || '--'}</span>
                    <span>{sourceLabel(data?.source || '')}</span>
                    <span className={data?.is_realtime ? 'text-up' : 'text-warn'}>{data?.is_realtime ? '盘中实时' : '历史/缓存'}</span>
                  </div>
                </div>
                <button type="button" onClick={close} className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-text-secondary hover:bg-[#21262D] hover:text-text" title="关闭" aria-label="关闭K线弹窗"><X size={18} /></button>
              </div>

              <div className="mt-3 flex items-center justify-between gap-2">
                <div className="grid flex-1 grid-cols-4 overflow-hidden rounded-md border border-border sm:flex sm:flex-none">
                  {CATEGORY_OPTIONS.map((item) => (
                    <button
                      type="button"
                      key={item.value}
                      onClick={() => void load(item.value)}
                      disabled={loading && category === item.value}
                      className={`h-8 border-r border-border px-2 text-[11px] last:border-r-0 sm:px-4 ${category === item.value ? 'bg-accent text-white' : 'bg-card text-text-secondary hover:text-text'} disabled:opacity-60`}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <button type="button" onClick={() => void load(category, true)} disabled={loading} className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-border text-text-secondary hover:border-accent hover:text-text disabled:opacity-50" title="刷新K线" aria-label="刷新K线"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
              </div>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {loading ? (
                <div className="grid min-h-[300px] place-items-center px-5 py-10 sm:min-h-[420px]" role="status">
                  <div className="w-full max-w-xs text-center">
                    <Loader2 size={23} className="mx-auto animate-spin text-accent" />
                    <div className="mt-3 text-xs text-text">正在读取{CATEGORY_OPTIONS.find((item) => item.value === category)?.label}</div>
                    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#21262D]"><div className="h-full bg-accent transition-all duration-300" style={{ width: `${progress}%` }} /></div>
                    <div className="mt-1.5 font-mono text-[10px] text-text-secondary">{progress}%</div>
                  </div>
                </div>
              ) : error ? (
                <div className="grid min-h-[300px] place-items-center px-5 py-10 text-center sm:min-h-[420px]">
                  <div><div className="text-sm text-down">{error}</div><button type="button" onClick={() => void load(category, true)} className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs text-text-secondary hover:border-accent hover:text-text"><RefreshCw size={13} />重新加载</button></div>
                </div>
              ) : data?.available && data.rows.length > 0 ? (
                <KlineChart rows={data.rows} height="clamp(300px, 56dvh, 470px)" />
              ) : (
                <div className="grid min-h-[300px] place-items-center px-5 py-10 text-xs text-text-secondary sm:min-h-[420px]">该股票当前没有可核验的{CATEGORY_OPTIONS.find((item) => item.value === category)?.label}数据</div>
              )}
              {data?.warning && !loading && <div className="border-t border-warn/30 px-4 py-2.5 text-[10px] leading-5 text-warn">{data.warning}</div>}
            </div>

            <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-border bg-[#0D1117] px-3 py-2.5 sm:px-4">
              <span className="text-[10px] text-text-secondary">拖动缩放条可查看历史区间</span>
              <Link href={`/pro/stock?code=${encodeURIComponent(normalizedCode)}`} onClick={close} className="inline-flex items-center gap-1.5 text-xs text-accent hover:text-text">打开完整个股页<ExternalLink size={13} /></Link>
            </footer>
          </section>
        </div>,
        document.body,
      )}
    </>
  );
}
