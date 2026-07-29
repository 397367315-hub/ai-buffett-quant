'use client';

import { ReactNode } from 'react';

import { TermItem } from '@/lib/types';

export default function TermCard({ term, simple_explanation, professional_explanation, usage_guide, category, related_terms }: TermItem) {
  return (
    <div className="bg-card border border-border rounded-lg p-6 hover:border-accent transition-colors">
      <span className="inline-block px-2 py-0.5 text-xs rounded bg-[#1F6FEB22] text-accent mb-3">
        {category}
      </span>
      <h3 className="text-lg font-bold text-text mb-4">{term}</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-[#0D1117] rounded p-4">
          <div className="text-warn text-xs mb-1.5 font-medium">通俗解释</div>
           <p className="text-text-secondary text-sm leading-relaxed">{simple_explanation}</p>
        </div>
        <div className="bg-[#0D1117] rounded p-4">
          <div className="text-accent text-xs mb-1.5 font-medium">专业解释</div>
          <p className="text-text-secondary text-sm leading-relaxed">{professional_explanation}</p>
        </div>
        <div className="bg-[#0D1117] rounded p-4">
          <div className="text-up text-xs mb-1.5 font-medium">怎么用</div>
          <p className="text-text-secondary text-sm leading-relaxed">{usage_guide}</p>
        </div>
      </div>
      {related_terms.length > 0 && (
        <div className="mt-4 pt-4 border-t border-border">
          <span className="text-text-secondary text-xs mr-2">相关术语：</span>
          {related_terms.map((rt) => (
            <span key={rt} className="inline-block px-2 py-0.5 text-xs rounded bg-[#30363D] text-text-secondary mr-1.5 cursor-pointer hover:bg-accent hover:text-white transition-colors">
              {rt}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
