'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Bot, Bookmark, CalendarDays, ChartNoAxesCombined, Globe2, NotebookPen, PieChart } from 'lucide-react';

const ITEMS = [
  { href: '/pro/personal', label: '个人股票池', icon: Bookmark },
  { href: '/pro/robot', label: 'AI机器人池', icon: Bot },
  { href: '/pro/portfolio', label: '仓位管理', icon: PieChart },
  { href: '/pro/attribution', label: '业绩归因', icon: ChartNoAxesCombined },
  { href: '/pro/macro', label: '宏观看板', icon: Globe2 },
  { href: '/pro/reports', label: '财报日历', icon: CalendarDays },
  { href: '/pro/research', label: '研究与错误', icon: NotebookPen },
];

export default function PersonalWorkspaceNav() {
  const pathname = usePathname();
  return (
    <nav className="mb-5 border-b border-border overflow-x-auto" aria-label="个人投资工作区">
      <div className="flex min-w-max">
        {ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`inline-flex h-10 items-center gap-1.5 px-3 text-xs border-b-2 transition-colors ${active ? 'border-accent text-accent' : 'border-transparent text-text-secondary hover:text-text'}`}
            >
              <Icon size={14} />{label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
