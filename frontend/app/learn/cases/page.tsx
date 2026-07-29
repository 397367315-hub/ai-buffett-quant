'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { CaseItem } from '@/lib/types';
import { apiFetch } from '@/lib/api';

export default function CasesPage() {
  const [cases, setCases] = useState<CaseItem[]>([]);
  const [category, setCategory] = useState<string>('');
  const [loading, setLoading] = useState(true);

  const categories = ['', '政策利好', '北向资金', '概念炒作', '技术分析', '机构行为', '风险管理'];

  useEffect(() => {
    fetchCases();
  }, [category]);

  const fetchCases = async () => {
    setLoading(true);
    try {
      const params = category ? `?category=${encodeURIComponent(category)}` : '';
      const res = await apiFetch<any>(`/learn/cases${params}`);
      setCases(res.data);
    } catch (err) {
      console.error('Failed to fetch cases:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-6">
      <h1 className="text-2xl font-bold text-text mb-2">案例教学</h1>
      <p className="text-text-secondary mb-6">基于真实行情的分步教学案例，每步都有知识点和自测题</p>

      <div className="flex flex-wrap gap-2 mb-6">
        {categories.map((c) => (
          <button
            key={c}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              c === category ? 'bg-accent border-accent text-white' : 'border-border text-text-secondary hover:border-text-secondary'
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
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {cases.map((c) => (
            <Link
              key={c.id}
              href={`/learn/cases/${c.id}`}
              className="bg-card border border-border rounded-lg p-5 hover:border-accent transition-all"
            >
              <div className="flex items-center gap-2 mb-2">
                <span className="px-2 py-0.5 text-xs rounded bg-[#1F6FEB22] text-accent">{c.category}</span>
                <span className="text-xs text-text-secondary">{'⭐'.repeat(c.difficulty_level)}</span>
              </div>
              <h3 className="text-base font-bold text-text mb-2 leading-snug">{c.title}</h3>
              <p className="text-text-secondary text-sm line-clamp-2">{c.summary}</p>
              {c.key_learnings && c.key_learnings.length > 0 && (
                <div className="mt-3 pt-3 border-t border-border">
                  <span className="text-xs text-text-secondary">关键收获：</span>
                  <span className="text-xs text-text-secondary">{c.key_learnings[0]}</span>
                </div>
              )}
            </Link>
          ))}
        </div>
      )}

      {!loading && cases.length === 0 && (
        <div className="text-center text-text-secondary py-12">
          暂无数据。请确保后端服务已启动并执行了种子数据导入。
        </div>
      )}
    </div>
  );
}
