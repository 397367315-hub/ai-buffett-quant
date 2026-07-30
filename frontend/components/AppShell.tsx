'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect, ReactNode } from 'react';
import { useAuth } from '@/lib/AuthContext';
import Navbar from './Navbar';
import Footer from './Footer';

export default function AppShell({ children }: { children: ReactNode }) {
  const { isLoggedIn, isAuthReady } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  const isLoginPage = pathname === '/login';

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

  return (
    <>
      <Navbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </>
  );
}
