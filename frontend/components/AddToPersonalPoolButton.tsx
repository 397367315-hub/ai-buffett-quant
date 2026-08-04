'use client';

import { useState } from 'react';
import { BookmarkPlus, Check, Loader2, AlertCircle } from 'lucide-react';
import Link from 'next/link';
import { apiFetch } from '@/lib/api';

type PoolKey = 'core' | 'watchlist' | 'leaders' | 'etf' | 'blacklist';

const POOL_LABELS: Record<PoolKey, string> = {
  core: '核心持仓池',
  watchlist: '长期观察池',
  leaders: '行业龙头池',
  etf: 'ETF池',
  blacklist: '黑名单',
};

interface AddToPersonalPoolButtonProps {
  code: string;
  name: string;
  industry?: string;
  thesis?: string;
  source?: string;
  pool?: PoolKey;
  assetType?: 'stock' | 'etf';
  className?: string;
  compact?: boolean;
}

export default function AddToPersonalPoolButton({
  code,
  name,
  industry = '',
  thesis = '',
  source = 'analysis',
  pool = 'watchlist',
  assetType = 'stock',
  className = '',
  compact = false,
}: AddToPersonalPoolButtonProps) {
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const poolLabel = POOL_LABELS[pool];

  const add = async () => {
    if (state === 'loading') return;
    setState('loading');
    setMessage('');
    try {
      const response = await apiFetch<{ data: { created: boolean } }>('/personal/items', {
        method: 'POST',
        body: JSON.stringify({
          code,
          name,
          pool,
          asset_type: assetType,
          industry,
          thesis,
          source,
        }),
      });
      setState('done');
      setMessage(response.data.created ? `已加入${poolLabel}` : `已经在${poolLabel}`);
    } catch (caught) {
      setState('error');
      setMessage(caught instanceof Error ? caught.message : '加入失败');
    }
  };

  if (state === 'done') {
    return (
      <Link
        href={`/pro/personal?pool=${pool}&q=${encodeURIComponent(code)}`}
        className={`inline-flex items-center gap-1 text-xs text-down hover:underline ${className}`}
        title={`${message}，点击查看`}
      >
        <Check size={13} />{compact ? '已加入' : message}
      </Link>
    );
  }

  return (
    <button
      type="button"
      onClick={add}
      disabled={state === 'loading'}
      className={`inline-flex items-center gap-1.5 border border-accent/60 text-accent hover:bg-[#1F6FEB22] disabled:opacity-60 rounded-md px-2 py-1.5 text-xs transition-colors ${className}`}
      title={state === 'error' ? message : `加入个人${poolLabel}`}
      aria-label={`将${name}加入个人股票池`}
    >
      {state === 'loading' ? <Loader2 size={13} className="animate-spin" /> : state === 'error' ? <AlertCircle size={13} /> : <BookmarkPlus size={13} />}
      {compact ? (state === 'error' ? '重试' : '加入个人池') : (state === 'error' ? '重试加入' : '加入个人池')}
    </button>
  );
}
