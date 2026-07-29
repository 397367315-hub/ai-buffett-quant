'use client';

import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { AIReport } from '@/lib/types';
import { Newspaper, RefreshCw } from 'lucide-react';

export default function DailyReportPage() {
  const [report, setReport] = useState<AIReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);

  const fetchReport = async () => {
    setLoading(true);
    try {
      const res = await apiFetch<any>('/ai/daily-report', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setReport(res.data);
    } catch (err) {
      console.error('Failed to fetch report:', err);
    } finally {
      setLoading(false);
    }
  };

  const generateReport = async () => {
    setGenerating(true);
    try {
      const res = await apiFetch<any>('/ai/daily-report', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      setReport(res.data);
    } catch (err) {
      console.error('Failed to generate report:', err);
    } finally {
      setGenerating(false);
    }
  };

  useEffect(() => {
    fetchReport();
  }, []);

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-text flex items-center gap-2">
            <Newspaper size={22} className="text-accent" />
            今日资金流向解读
          </h1>
          <p className="text-text-secondary text-sm mt-1">AI自动生成，用大白话解读今日市场</p>
        </div>
        <button
          onClick={generateReport}
          disabled={generating}
          className="flex items-center gap-1.5 px-4 py-2 bg-accent text-white text-sm rounded-md hover:opacity-90 disabled:opacity-50 transition-colors"
        >
          <RefreshCw size={14} className={generating ? 'animate-spin' : ''} />
          {generating ? '生成中...' : '重新生成'}
        </button>
      </div>

      {(loading) && (
        <div className="text-center text-text-secondary py-12">
          <div className="animate-spin w-8 h-8 border-2 border-accent border-t-transparent rounded-full mx-auto mb-3" />
          正在获取今日解读...
        </div>
      )}

      {!loading && report && (
        <div className="bg-card border border-border rounded-lg p-8">
          <div className="prose prose-invert max-w-none text-text leading-relaxed whitespace-pre-wrap">
            {report.report || 'AI报告生成中，请稍后重新生成...'}
          </div>
        </div>
      )}

      {!loading && !report && (
        <div className="text-center text-text-secondary py-12">
          <p className="mb-4">暂未获取到解读报告</p>
          <button
            onClick={generateReport}
            disabled={generating}
            className="px-6 py-2.5 bg-accent text-white text-sm rounded-lg hover:opacity-90"
          >
            生成今日解读
          </button>
        </div>
      )}
    </div>
  );
}
