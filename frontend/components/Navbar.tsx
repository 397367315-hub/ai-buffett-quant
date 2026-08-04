'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Activity, BarChart3, BookOpen, Bot, Menu, X, TrendingUp, DollarSign, Sparkles, Zap, ChevronDown, LogOut, User, Home, BrainCircuit, LineChart, Bookmark } from 'lucide-react';
import { useState, useEffect } from 'react';
import { useAuth } from '@/lib/AuthContext';

const proSubItems = [
  { href: '/market', label: '今日速览', icon: Home },
  { href: '/pro', label: '资金排名', icon: TrendingUp },
  { href: '/pro/sentiment', label: '市场情绪', icon: TrendingUp },
  { href: '/pro/north-flow', label: '北向资金', icon: TrendingUp },
  { href: '/pro/limit-board', label: '涨跌停板', icon: Zap },
  { href: '/pro/potential', label: '潜力股分析', icon: Sparkles },
  { href: '/pro/stock-picker', label: '智能选股', icon: BrainCircuit },
  { href: '/pro/personal', label: '个人投资池', icon: Bookmark },
  { href: '/quant', label: '量化策略', icon: LineChart },
  { href: '/pro/rotation', label: '板块轮动', icon: TrendingUp },
  { href: '/pro/flow-observer', label: '资金流观察', icon: Activity },
  { href: '/pro/dragon-board', label: '龙虎榜', icon: TrendingUp },
  { href: '/pro/block-trade', label: '大宗交易', icon: TrendingUp },
  { href: '/pro/screener', label: '技术筛选', icon: TrendingUp },
  { href: '/pro/sim-trade', label: 'AI模拟炒股', icon: Bot },
  { href: '/fed', label: '美联储分析', icon: DollarSign },
];

export default function Navbar() {
  const pathname = usePathname();
  const { username, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [proDropdownOpen, setProDropdownOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  const isActive = (href: string) => {
    if (href === '/market') return pathname === '/market';
    return pathname.startsWith(href);
  };

  return (
    <nav className="h-14 bg-card border-b border-border flex items-center px-3 md:px-4 sticky top-0 z-50">
      <Link href="/market" className="flex items-center gap-2 mr-4 md:mr-6 shrink-0">
        <span className="text-xl">🪨</span>
        <span className="font-bold text-text hidden sm:inline text-sm md:text-base">AI巴菲特量化分析系统</span>
      </Link>

      {/* 桌面端导航 */}
      <div className="hidden md:flex items-center gap-1 flex-1">
        <div
          className="relative"
          onMouseEnter={() => setProDropdownOpen(true)}
          onMouseLeave={() => setProDropdownOpen(false)}
        >
          <Link href="/pro" className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${pathname.startsWith('/pro') || pathname.startsWith('/quant') || pathname.startsWith('/fed') || pathname.startsWith('/market') ? 'bg-[#1F6FEB22] text-accent font-medium' : 'text-text-secondary hover:text-text hover:bg-[#21262D]'}`}>
            <BarChart3 size={16} /> 专业看板 <ChevronDown size={12} />
          </Link>
          {proDropdownOpen && (
            <div className="absolute top-full left-0 mt-1 bg-card border border-border rounded-lg shadow-xl py-1 w-44 z-50 max-h-80 overflow-y-auto">
              {proSubItems.map((item) => {
                const Icon = item.icon;
                return (
                  <Link key={item.href} href={item.href} className={`flex items-center gap-2 px-3 py-2 text-sm transition-colors ${isActive(item.href) ? 'bg-[#1F6FEB22] text-accent' : 'text-text-secondary hover:text-text hover:bg-[#21262D]'}`}>
                    <Icon size={14} />{item.label}
                  </Link>
                );
              })}
            </div>
          )}
        </div>
        <Link href="/learn" className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${pathname.startsWith('/learn') ? 'bg-[#1F6FEB22] text-accent font-medium' : 'text-text-secondary hover:text-text hover:bg-[#21262D]'}`}>
          <BookOpen size={16} /> 新手学堂
        </Link>
        <Link href="/ai" className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${pathname.startsWith('/ai') ? 'bg-[#1F6FEB22] text-accent font-medium' : 'text-text-secondary hover:text-text hover:bg-[#21262D]'}`}>
          <Bot size={16} /> AI助手
        </Link>
      </div>

      {/* 用户区 */}
      <div className="hidden md:flex items-center gap-2 ml-auto">
        <span className="text-xs text-text-secondary flex items-center gap-1">
          <User size={12} /> {username}
        </span>
        <button onClick={logout} className="flex items-center gap-1 px-2 py-1 text-xs text-text-secondary hover:text-down rounded transition-colors">
          <LogOut size={12} /> 退出
        </button>
      </div>

      {/* 移动端汉堡菜单 */}
      <button className="md:hidden ml-auto text-text-secondary p-1" onClick={() => setMenuOpen(!menuOpen)}>
        {menuOpen ? <X size={20} /> : <Menu size={20} />}
      </button>

      {/* 移动端菜单 */}
      {menuOpen && (
        <div className="absolute top-14 left-0 right-0 bg-card border-b border-border p-4 md:hidden z-50 max-h-[calc(100vh-56px)] overflow-y-auto">
          <Link href="/market" className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-text-secondary hover:text-text" onClick={() => setMenuOpen(false)}>
            <Home size={16} /> 今日速览
          </Link>
          <div className="border-t border-border my-2 pt-2">
            <div className="text-xs text-text-secondary px-3 mb-1">专业看板</div>
            {proSubItems.map((item) => {
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-text-secondary hover:text-text" onClick={() => setMenuOpen(false)}>
                  <Icon size={14} />{item.label}
                </Link>
              );
            })}
          </div>
          <div className="border-t border-border my-2 pt-2">
            <Link href="/learn" className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-text-secondary hover:text-text" onClick={() => setMenuOpen(false)}>
              <BookOpen size={14} />新手学堂
            </Link>
            <Link href="/ai" className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-text-secondary hover:text-text" onClick={() => setMenuOpen(false)}>
              <Bot size={14} />AI助手
            </Link>
          </div>
          <div className="border-t border-border mt-2 pt-3 flex items-center justify-between">
            <span className="text-xs text-text-secondary">{username}</span>
            <button onClick={() => { logout(); setMenuOpen(false); }} className="flex items-center gap-1 px-2 py-1 text-xs text-down rounded border border-down/30">
              <LogOut size={12} />退出
            </button>
          </div>
        </div>
      )}
    </nav>
  );
}
