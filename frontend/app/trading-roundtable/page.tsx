'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/AuthContext';

/** 独立圆桌 bundle 宿主页：保持旧圆桌壳与实验工作台完整，不把它混入 Next AppShell。 */
export default function TradingRoundtablePage() {
  const { isLoggedIn, isAuthReady } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isAuthReady && !isLoggedIn) router.replace('/login');
  }, [isAuthReady, isLoggedIn, router]);

  if (!isAuthReady || !isLoggedIn) return <div className="flex min-h-screen items-center justify-center bg-bg" />;
  return (
    <main className="fixed inset-0 z-[60] bg-[#f5f2ed]" data-testid="trading-roundtable-page">
      <iframe
        title="交易圆桌"
        src="/roundtable/index.html#/roundtable"
        className="h-full w-full border-0"
        allow="clipboard-read; clipboard-write"
      />
    </main>
  );
}
