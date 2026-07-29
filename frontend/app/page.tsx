import Link from 'next/link';
import { BarChart3, BookOpen, Bot, TrendingUp, ArrowRight } from 'lucide-react';

export default function HomePage() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-12">
      <div className="text-center mb-16">
        <h1 className="text-4xl font-bold text-text mb-4">
          <span className="text-3xl mr-2">🪨</span>
          AI巴菲特量化智能分析
        </h1>
        <p className="text-text-secondary text-lg max-w-2xl mx-auto leading-relaxed">
          让新手看懂资金流向，学会用数据分析市场情绪。
          <br />
          不仅仅是冷冰冰的数据展示，而是「数据 + 解读 + 学习」三位一体。
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-16">
        <Link
          href="/pro"
          className="bg-card border border-border rounded-xl p-8 hover:border-accent transition-all group"
        >
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-lg bg-[#1F6FEB22] flex items-center justify-center">
              <BarChart3 className="text-accent" size={24} />
            </div>
            <h2 className="text-xl font-bold text-text">专业看板模式</h2>
          </div>
          <p className="text-text-secondary mb-4 leading-relaxed">
            实时资金排名、板块轮动热力图、主力动向追踪、个股资金明细、历史回放。
            给有经验的老手使用。
          </p>
          <div className="flex flex-wrap gap-2 mb-4">
            {['资金排名', '热力图', '个股透视', '历史回放', '北向资金'].map((t) => (
              <span key={t} className="px-2 py-1 text-xs rounded bg-[#21262D] text-text-secondary">
                {t}
              </span>
            ))}
          </div>
          <span className="text-accent text-sm flex items-center gap-1 group-hover:gap-2 transition-all">
            进入看板 <ArrowRight size={14} />
          </span>
        </Link>

        <Link
          href="/learn"
          className="bg-card border border-border rounded-xl p-8 hover:border-[#EF5350] transition-all group"
        >
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-lg bg-[#EF535022] flex items-center justify-center">
              <BookOpen className="text-[#EF5350]" size={24} />
            </div>
            <h2 className="text-xl font-bold text-text">新手学习模式</h2>
          </div>
          <p className="text-text-secondary mb-4 leading-relaxed">
            今日资金流向解读、概念板块百科、数据名词词典、情境式案例教学、AI对话问答。
            让完全不懂股票的新手也能看懂。
          </p>
          <div className="flex flex-wrap gap-2 mb-4">
            {['日报解读', '术语词典', '案例教学', '板块百科', 'AI助手'].map((t) => (
              <span key={t} className="px-2 py-1 text-xs rounded bg-[#21262D] text-text-secondary">
                {t}
              </span>
            ))}
          </div>
          <span className="text-[#EF5350] text-sm flex items-center gap-1 group-hover:gap-2 transition-all">
            进入学堂 <ArrowRight size={14} />
          </span>
        </Link>
      </div>

      <div className="bg-card border border-border rounded-xl p-8">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-lg bg-[#D2992222] flex items-center justify-center">
            <TrendingUp className="text-warn" size={20} />
          </div>
          <div>
            <h2 className="text-lg font-bold text-text">新手学习路径（7天入门）</h2>
            <p className="text-text-secondary text-sm">从零开始，每天学习一点，7天看懂资金流向</p>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-3">
          {[
            { day: 1, title: '认识股市', emoji: '🥚' },
            { day: 2, title: '认识板块', emoji: '🐣' },
            { day: 3, title: '认识资金', emoji: '🐥' },
            { day: 4, title: '认识指标', emoji: '🦊' },
            { day: 5, title: '实战看盘', emoji: '🦅' },
            { day: 6, title: '进阶分析', emoji: '🐺' },
            { day: 7, title: '综合实战', emoji: '🦉' },
          ].map((d) => (
            <div key={d.day} className="bg-[#0D1117] border border-border rounded-lg p-4 text-center">
              <div className="text-2xl mb-1">{d.emoji}</div>
              <div className="text-xs text-text-secondary mb-1">Day {d.day}</div>
              <div className="text-sm font-medium text-text">{d.title}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
