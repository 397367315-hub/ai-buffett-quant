'use client';

import { useEffect } from 'react';
import Link from 'next/link';
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
    <main className="fixed inset-0 z-[60] flex flex-col bg-[#f5f2ed]" data-testid="trading-roundtable-page">
      <nav aria-label="返回系统" className="flex shrink-0 items-center justify-between border-b border-[#ded8cd] px-4 py-2 text-sm text-[#554b3d]">
        <Link href="/" className="rounded px-2 py-1 font-medium hover:bg-[#e7e1d6] focus-visible:outline focus-visible:outline-2" data-testid="roundtable-return-home">
          ← 返回系统主页
        </Link>
        <span>交易圆桌</span>
      </nav>
      <iframe
        title="交易圆桌"
        src="/roundtable/index.html#/roundtable"
        className="min-h-0 w-full flex-1 border-0"
        allow="clipboard-read; clipboard-write"
      />
    </main>
  );
}
