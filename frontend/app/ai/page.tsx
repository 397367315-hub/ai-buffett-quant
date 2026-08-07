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

      <ChatInterface />
    </div>
  );
}
