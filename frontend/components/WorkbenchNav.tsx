'use client';

import Link from 'next/link';
import { Menu, X, ChevronRight } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

const LINKS = [
  ['/market', 'V5 决策中枢'],
  ['/market/v4', 'V4 工作台'],
  ['/research', '研究中心'],
  ['/research/midday', '午间研究'],
  ['/roci', '风险机会'],
  ['/strong-stock-decision', '强势股决策'],
  ['/pro/stock-picker', '智能选股'],
  ['/quant', '量化策略'],
  ['/pro/flow-observer', '资金流观察'],
  ['/pro/topic-strength', '题材强弱'],
  ['/pro/personal', '个人股票池'],
  ['/pro/robot', 'AI机器人池'],
  ['/ai', 'AI助手'],
] as const;

export default function WorkbenchNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const active = (href: string) => href === '/market' ? pathname === '/market' : pathname.startsWith(href);
  return <div className="workbench-nav">
    <div className="workbench-nav-inner">
      <Link href="/market" className="workbench-nav-brand">AI量化系统</Link>
      <nav className="workbench-nav-links" aria-label="系统功能导航">
        {LINKS.map(([href, label]) => <Link key={href} href={href} className={active(href) ? 'is-active' : ''}>{label}</Link>)}
      </nav>
      <button type="button" className="workbench-nav-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-label={open ? '关闭系统功能导航' : '打开系统功能导航'} title={open ? '关闭导航' : '打开导航'}>{open ? <X size={16} /> : <Menu size={16} />}</button>
    </div>
    {open && <div className="workbench-nav-mobile"><div className="workbench-nav-mobile-grid">{LINKS.map(([href, label]) => <Link key={href} href={href} className={active(href) ? 'is-active' : ''} onClick={() => setOpen(false)}>{label}<ChevronRight size={12} /></Link>)}</div></div>}
  </div>;
}
