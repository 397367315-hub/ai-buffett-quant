'use client';

import ChatInterface from '@/components/ChatInterface';
import { Sparkles } from 'lucide-react';

export default function AIPage() {
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text flex items-center gap-2">
          <Sparkles size={22} className="text-warn" />
          AI 投资学习助手
        </h1>
        <p className="text-text-secondary text-sm mt-1">用自然语言回答投资问题，像耐心的老师一样解释复杂概念</p>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        {['什么是主力资金？', '怎么判断主力在进还是出？', '北向资金是什么？', '换手率高说明什么？', '龙虎榜怎么看？'].map((q) => (
          <span key={q} className="px-3 py-1.5 text-xs rounded-full bg-[#21262D] text-text-secondary cursor-pointer hover:bg-[#30363D] hover:text-text transition-colors">
            {q}
          </span>
        ))}
      </div>

      <ChatInterface />
    </div>
  );
}
