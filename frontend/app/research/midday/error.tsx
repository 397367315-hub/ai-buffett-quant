'use client';

import { useEffect } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export default function MiddayResearchError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Keep the browser console useful while presenting a recoverable UI.
    console.error('Midday research page failed to render');
  }, []);

  return (
    <main className="mx-auto grid min-h-[60vh] max-w-xl place-items-center px-4">
      <section className="w-full border border-down/40 bg-down/5 p-6 text-center">
        <AlertTriangle size={26} className="mx-auto text-down" />
        <h1 className="mt-3 text-base font-semibold text-text">午间研究数据暂时不完整</h1>
        <p className="mt-2 text-xs leading-5 text-text-secondary">这条历史记录字段不完整或后端正在更新，重新加载即可继续查看其他研究记录。</p>
        <button type="button" onClick={() => reset()} className="command-button mt-4">
          <RefreshCw size={14} />重新加载
        </button>
      </section>
    </main>
  );
}
