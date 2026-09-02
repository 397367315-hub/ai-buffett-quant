'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, ReactNode } from 'react';
import { useAuth } from '@/lib/AuthContext';
import Navbar from './Navbar';
import Footer from './Footer';
import WorkbenchNav from './WorkbenchNav';

export default function AppShell({ children }: { children: ReactNode }) {
  const { isLoggedIn, isAuthReady } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  const isLoginPage = pathname === '/login';
  const isV5Workbench = pathname === '/market';
  const isFullscreenWorkbench = pathname.startsWith('/market/v4') || pathname === '/strong-stock-decision';

  useEffect(() => {
    if (isAuthReady && !isLoggedIn && !isLoginPage) {
      router.push('/login');
    }
  }, [isAuthReady, isLoggedIn, isLoginPage, router]);

  // 登录页不显示导航和页脚
  if (isLoginPage) {
    return <>{children}</>;
  }

  // 未登录不渲染
  if (!isAuthReady || !isLoggedIn) {
    return (
      <div className="flex items-center justify-center h-screen bg-bg">
        <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full" />
      </div>
    );
  }

  // V5 owns its full terminal chrome. V4 and the strong-stock module use the
  // compact cross-module navigation without adding a second bar to V5.
  if (isV5Workbench) {
    return <main className="flex-1">{children}</main>;
  }

  if (isFullscreenWorkbench) {
    return <><WorkbenchNav /><main className="flex-1">{children}</main></>;
  }

  return (
    <>
      <Navbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </>
  );
}
