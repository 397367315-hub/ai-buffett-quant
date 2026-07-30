'use client';

import Link from 'next/link';
import { BookOpen, Newspaper, Layers, Lightbulb, TrendingUp, ArrowRight, Zap } from 'lucide-react';

export default function LearnHome() {
  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-text mb-2">新手学堂</h1>
        <p className="text-text-secondary">从零开始学习AI巴菲特量化分析系统，每天进步一点点</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
        {[
          { href: '/learn/daily-report', icon: Newspaper, title: '今日资金解读', desc: 'AI自动生成的市场日报', color: 'text-[#58A6FF]' },
          { href: '/learn/terms', icon: BookOpen, title: '术语词典', desc: '20+核心术语，三栏式解释', color: 'text-[#D29922]' },
          { href: '/learn/cases', icon: Lightbulb, title: '案例教学', desc: '真实行情 + 分步讲解 + 自测', color: 'text-[#EF5350]' },
          { href: '/learn/board/BK0917', icon: Layers, title: '板块百科', desc: '了解每个概念板块的来龙去脉', color: 'text-[#a371f7]' },
          { href: '/ai', icon: Zap, title: 'AI 学习助手', desc: '有问题？随时问小财老师', color: 'text-[#26A69A]' },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="bg-card border border-border rounded-lg p-5 hover:border-accent transition-all group"
            >
              <Icon className={`${item.color} mb-3`} size={24} />
              <h3 className="text-base font-bold text-text mb-1">{item.title}</h3>
              <p className="text-text-secondary text-sm">{item.desc}</p>
            </Link>
          );
        })}
      </div>

      {/* 学习进度概览 */}
      <div className="bg-card border border-border rounded-lg p-6">
        <div className="flex items-center gap-3 mb-4">
          <TrendingUp className="text-up" size={20} />
          <h3 className="text-lg font-bold text-text">7天学习路径</h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
          {[
            { day: 1, title: '认识股市', desc: 'A股基础知识' },
            { day: 2, title: '认识板块', desc: '概念 vs 行业' },
            { day: 3, title: '认识资金', desc: '主力/散户/外资' },
            { day: 4, title: '认识指标', desc: '成交量/换手率' },
            { day: 5, title: '实战看盘', desc: '开盘到收盘' },
            { day: 6, title: '进阶分析', desc: '龙虎榜/财报' },
            { day: 7, title: '综合实战', desc: '完整复盘' },
          ].map((d) => (
            <div key={d.day} className="bg-[#0D1117] border border-border rounded-lg p-3 text-center">
              <div className="text-xs text-text-secondary mb-1">Day {d.day}</div>
              <div className="text-sm font-medium text-text">{d.title}</div>
              <div className="text-xs text-text-secondary mt-0.5">{d.desc}</div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex justify-center">
          <Link
            href="/learn/terms"
            className="inline-flex items-center gap-2 px-6 py-2.5 bg-accent text-white rounded-lg text-sm font-medium hover:opacity-90 transition-colors"
          >
            开始学习 <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </div>
  );
}
