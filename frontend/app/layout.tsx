import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from '@/lib/AuthContext';
import AppShell from '@/components/AppShell';

export const metadata: Metadata = {
  title: 'AI巴菲特量化智能分析系统',
  description: '专业数据看板 + AI量化策略 + 新手学习分析',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen flex flex-col bg-bg">
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
