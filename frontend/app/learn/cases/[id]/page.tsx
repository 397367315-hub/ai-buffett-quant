'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { CaseItem } from '@/lib/types';
import { apiFetch } from '@/lib/api';
import CaseDetail from '@/components/CaseDetail';
import { ArrowLeft } from 'lucide-react';

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [caseItem, setCaseItem] = useState<CaseItem | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchCase();
  }, [id]);

  const fetchCase = async () => {
    setLoading(true);
    try {
      const res = await apiFetch<any>(`/learn/cases/${id}`);
      setCaseItem(res.data);
    } catch (err) {
      console.error('Failed to fetch case:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="text-center text-text-secondary py-12">加载中...</div>;
  }

  if (!caseItem) {
    return <div className="text-center text-text-secondary py-12">案例不存在</div>;
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <Link href="/learn/cases" className="inline-flex items-center gap-1 text-text-secondary text-sm hover:text-text mb-6">
        <ArrowLeft size={14} /> 返回案例列表
      </Link>
      <CaseDetail caseItem={caseItem} />
    </div>
  );
}
