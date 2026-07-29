'use client';

import { useEffect, useState } from 'react';
import { TermItem } from '@/lib/types';
import { apiFetch } from '@/lib/api';
import TermCard from '@/components/TermCard';

export default function TermsPage() {
  const [terms, setTerms] = useState<TermItem[]>([]);
  const [category, setCategory] = useState<string>('');
  const [loading, setLoading] = useState(true);

  const categories = ['', '资金流向', '交易指标', '基础概念', '板块分类', '席位数据', '外资流向', '杠杆工具', '估值指标', '交易数据', '盘口数据', '交易机制', '技术分析', '技术指标', '操作术语', '公司行为'];

  useEffect(() => {
    fetchTerms();
  }, [category]);

  const fetchTerms = async () => {
    setLoading(true);
    try {
      const params = category ? `?category=${encodeURIComponent(category)}` : '';
      const res = await apiFetch<any>(`/learn/terms${params}`);
      setTerms(res.data);
    } catch (err) {
      console.error('Failed to fetch terms:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-text mb-2">术语词典</h1>
      <p className="text-text-secondary mb-6">每个专业术语都有三行解释：通俗解释 + 专业解释 + 怎么用</p>

      {/* 分类筛选 */}
      <div className="flex flex-wrap gap-2 mb-6">
        {categories.map((c) => (
          <button
            key={c}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              c === category
                ? 'bg-accent border-accent text-white'
                : 'border-border text-text-secondary hover:border-text-secondary'
            }`}
            onClick={() => setCategory(c)}
          >
            {c || '全部分类'}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-center text-text-secondary py-12">加载中...</div>
      ) : (
        <div className="space-y-4">
          {terms.map((term) => (
            <TermCard key={term.id} {...term} />
          ))}
        </div>
      )}

      {!loading && terms.length === 0 && (
        <div className="text-center text-text-secondary py-12">
          暂无数据。请确保后端服务已启动并执行了种子数据导入。
        </div>
      )}
    </div>
  );
}
