'use client';

import { CaseItem } from '@/lib/types';
import { useState } from 'react';

interface Props {
  caseItem: CaseItem;
}

export default function CaseDetail({ caseItem }: Props) {
  const [currentStep, setCurrentStep] = useState(0);
  const [quizAnswered, setQuizAnswered] = useState(false);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);

  const step = caseItem.steps[currentStep];
  const isQuizStep = currentStep === caseItem.steps.length;

  return (
    <div className="bg-card border border-border rounded-lg p-6">
      <div className="flex items-center gap-2 mb-4">
        <span className="px-2 py-0.5 text-xs rounded bg-[#1F6FEB22] text-accent">{caseItem.category}</span>
        <span className="px-2 py-0.5 text-xs rounded bg-[#21262D] text-text-secondary">
          难度 {'⭐'.repeat(caseItem.difficulty_level)}
        </span>
      </div>

      <h2 className="text-xl font-bold text-text mb-2">{caseItem.title}</h2>
      <p className="text-text-secondary text-sm mb-6">{caseItem.summary}</p>

      {/* 步骤进度条 */}
      <div className="flex gap-1 mb-6">
        {caseItem.steps.map((_, i) => (
          <div
            key={i}
            className={`h-1.5 rounded-full flex-1 transition-colors ${
              i < currentStep ? 'bg-up' : i === currentStep ? 'bg-accent' : 'bg-[#30363D]'
            }`}
          />
        ))}
        <div
          className={`h-1.5 w-8 rounded-full transition-colors ${
            isQuizStep ? 'bg-warn' : 'bg-[#30363D]'
          }`}
        />
      </div>

      {/* 步骤内容 */}
      {!isQuizStep && step && (
        <div className="bg-[#0D1117] rounded-lg p-6 mb-6">
          <h4 className="text-lg font-bold text-accent mb-3">{step.title}</h4>
          <p className="text-text leading-relaxed mb-4">{step.content}</p>
          <div className="bg-[#D2992222] border border-[#D2992255] rounded p-3">
            <span className="text-warn text-xs font-medium">关键要点：</span>
            <span className="text-text-secondary text-sm ml-1">{step.key_point}</span>
          </div>
        </div>
      )}

      {/* 自测题 */}
      {isQuizStep && caseItem.quiz && (
        <div className="bg-[#0D1117] rounded-lg p-6 mb-6">
          <h4 className="text-lg font-bold text-warn mb-4">自测题</h4>
          <p className="text-text mb-4">{caseItem.quiz.question}</p>
          <div className="space-y-2">
            {caseItem.quiz.options.map((opt) => {
              const isCorrect = opt === caseItem.quiz.answer;
              const isSelected = opt === selectedOption;
              let borderClass = 'border-border';
              if (quizAnswered) {
                if (isCorrect) borderClass = 'border-[#EF5350] bg-[#EF535011]';
                else if (isSelected && !isCorrect) borderClass = 'border-[#26A69A] bg-[#26A69A11]';
              }
              return (
                <button
                  key={opt}
                  disabled={quizAnswered}
                  className={`w-full text-left p-3 rounded border ${borderClass} text-text text-sm hover:border-accent disabled:cursor-default transition-colors`}
                  onClick={() => {
                    setSelectedOption(opt);
                    setQuizAnswered(true);
                  }}
                >
                  {opt}
                </button>
              );
            })}
          </div>
          {quizAnswered && (
            <div className={`mt-4 p-3 rounded text-sm ${selectedOption === caseItem.quiz.answer ? 'bg-[#EF535022] text-up' : 'bg-[#26A69A22] text-down'}`}>
              {selectedOption === caseItem.quiz.answer ? '✅ 答对了！' : '❌ 答错了'}<br />
              {caseItem.quiz.explanation}
            </div>
          )}
        </div>
      )}

      {/* 导航按钮 */}
      <div className="flex justify-between">
        <button
          disabled={currentStep === 0}
          className="px-4 py-2 text-sm rounded border border-border text-text-secondary disabled:opacity-30 hover:border-accent hover:text-text transition-colors"
          onClick={() => { setCurrentStep(Math.max(0, currentStep - 1)); setQuizAnswered(false); setSelectedOption(null); }}
        >
          上一步
        </button>
        {currentStep < caseItem.steps.length ? (
          <button
            className="px-4 py-2 text-sm rounded bg-accent text-white hover:opacity-90 transition-colors"
            onClick={() => setCurrentStep(currentStep + 1)}
          >
            下一步
          </button>
        ) : (
          <span className="text-text-secondary text-sm flex items-center gap-1">
            学习完成
          </span>
        )}
      </div>

      {/* 关键收获 */}
      {caseItem.key_learnings.length > 0 && (
        <div className="mt-6 pt-6 border-t border-border">
          <h4 className="text-sm font-medium text-text mb-2">本案例关键收获</h4>
          <ul className="space-y-1">
            {caseItem.key_learnings.map((kl, i) => (
              <li key={i} className="text-text-secondary text-sm flex items-start gap-2">
                <span className="text-accent">•</span> {kl}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
