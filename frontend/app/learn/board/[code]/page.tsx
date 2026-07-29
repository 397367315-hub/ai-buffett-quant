'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { BoardEncyclopedia } from '@/lib/types';
import { apiFetch } from '@/lib/api';
import { ArrowLeft, TrendingUp, Users, Lightbulb, AlertTriangle } from 'lucide-react';

export default function BoardPage() {
  const { code } = useParams<{ code: string }>();
  const [board, setBoard] = useState<BoardEncyclopedia | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchBoard();
  }, [code]);

  const fetchBoard = async () => {
    setLoading(true);
    try {
      const res = await apiFetch<any>(`/learn/board/${code}`);
      setBoard(res.data);
    } catch (err) {
      console.error('Failed to fetch board:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="text-center text-text-secondary py-12">加载中...</div>;
  }

  if (!board) {
    return <div className="text-center text-text-secondary py-12">板块不存在</div>;
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <Link href="/learn" className="inline-flex items-center gap-1 text-text-secondary text-sm hover:text-text mb-6">
        <ArrowLeft size={14} /> 返回学堂
      </Link>

      {/* 标题 */}
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <span className="text-3xl">🏷️</span>
          <h1 className="text-2xl font-bold text-text">{board.name}</h1>
        </div>
        <p className="text-lg text-accent">{board.one_liner}</p>
      </div>

      {/* 通俗解释 */}
      <div className="bg-card border border-border rounded-lg p-6 mb-6">
        <h3 className="text-lg font-bold text-text mb-3 flex items-center gap-2">
          <Lightbulb size={18} className="text-warn" />
          通俗解释
        </h3>
        <p className="text-text-secondary leading-relaxed">{board.simple_explanation}</p>
      </div>

      {/* 产业链 */}
      {board.industry_chain && Object.keys(board.industry_chain).length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-4 flex items-center gap-2">
            <TrendingUp size={18} className="text-up" />
            产业链
          </h3>
          <div className="space-y-3">
            {Object.entries(board.industry_chain).map(([key, items]) => (
              <div key={key} className="flex gap-4">
                <div className="w-16 text-sm font-medium text-accent shrink-0">{key}</div>
                <div className="flex flex-wrap gap-1.5">
                  {(items as string[]).map((item) => (
                    <span key={item} className="px-2 py-0.5 text-xs rounded bg-[#21262D] text-text-secondary">
                      {item}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 明星公司 */}
      {board.key_companies && board.key_companies.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-4 flex items-center gap-2">
            <Users size={18} className="text-accent" />
            明星公司
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {board.key_companies.map((company) => (
              <div key={company.code} className="bg-[#0D1117] border border-border rounded-lg p-3 flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-text">{company.name}</div>
                  <div className="text-xs text-text-secondary">{company.code}</div>
                </div>
                <span className="text-xs text-accent bg-[#1F6FEB22] px-2 py-0.5 rounded">{company.role}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 涨跌逻辑 */}
      {board.triggers && board.triggers.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-3">什么情况下会涨？</h3>
          <ul className="space-y-1.5">
            {board.triggers.map((t) => (
              <li key={t} className="text-text-secondary text-sm flex items-start gap-2">
                <span className="text-up">•</span> {t}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 新手提示 */}
      {board.beginner_tip && (
        <div className="bg-[#26A69A22] border border-[#26A69A44] rounded-lg p-6 mb-6">
          <h3 className="text-lg font-bold text-text mb-2 flex items-center gap-2">
            <AlertTriangle size={18} className="text-down" />
            新手锦囊
          </h3>
          <p className="text-text-secondary leading-relaxed">{board.beginner_tip}</p>
        </div>
      )}

      {/* 延伸阅读 */}
      {board.related_reading && board.related_reading.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-6">
          <h3 className="text-lg font-bold text-text mb-3">延伸阅读</h3>
          <ul className="space-y-1">
            {board.related_reading.map((r) => (
              <li key={r} className="text-accent text-sm cursor-pointer hover:underline">
                📖 {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
