'use client';

import { useState, useEffect, useMemo } from 'react';
import { ChevronLeft, ChevronRight, Calendar } from 'lucide-react';
import { apiFetch } from '@/lib/api';

interface CalendarDatePickerProps {
  selectedDate: string | null;
  onSelectDate: (date: string) => void;
  onClose?: () => void;
}

export default function CalendarDatePicker({ selectedDate, onSelectDate, onClose }: CalendarDatePickerProps) {
  const [currentMonth, setCurrentMonth] = useState(() => {
    const d = selectedDate ? new Date(selectedDate) : new Date();
    return { year: d.getFullYear(), month: d.getMonth() + 1 };
  });
  const [availableDates, setAvailableDates] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAvailableDates();
  }, []);

  const fetchAvailableDates = async () => {
    try {
      const res = await apiFetch<any>('/flow/concept/dates');
      setAvailableDates(new Set(res.data.dates));
    } catch (err) {
      console.error('Failed to fetch dates:', err);
    } finally {
      setLoading(false);
    }
  };

  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

  const daysInMonth = new Date(currentMonth.year, currentMonth.month, 0).getDate();
  const firstDayOfWeek = new Date(currentMonth.year, currentMonth.month - 1, 1).getDay();

  const weekDays = ['日', '一', '二', '三', '四', '五', '六'];

  const goToPrevMonth = () => {
    setCurrentMonth(prev => {
      if (prev.month === 1) return { year: prev.year - 1, month: 12 };
      return { year: prev.year, month: prev.month - 1 };
    });
  };

  const goToNextMonth = () => {
    setCurrentMonth(prev => {
      if (prev.month === 12) return { year: prev.year + 1, month: 1 };
      return { year: prev.year, month: prev.month + 1 };
    });
  };

  const getDateStr = (day: number) => {
    return `${currentMonth.year}-${String(currentMonth.month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  };

  const isToday = (day: number) => getDateStr(day) === todayStr;
  const isSelected = (day: number) => getDateStr(day) === selectedDate;
  const hasData = (day: number) => availableDates.has(getDateStr(day));
  const isFuture = (day: number) => {
    const d = new Date(currentMonth.year, currentMonth.month - 1, day);
    return d > today;
  };

  const handleSelect = (day: number) => {
    if (isFuture(day)) return;
    onSelectDate(getDateStr(day));
    onClose?.();
  };

  const monthName = `${currentMonth.year}年${currentMonth.month}月`;

  return (
    <div className="bg-card border border-border rounded-lg p-4 shadow-xl w-[320px]">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={goToPrevMonth}
          className="p-1 rounded hover:bg-[#21262D] text-text-secondary hover:text-text transition-colors"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="text-sm font-medium text-text">{monthName}</span>
        <button
          onClick={goToNextMonth}
          className="p-1 rounded hover:bg-[#21262D] text-text-secondary hover:text-text transition-colors"
        >
          <ChevronRight size={16} />
        </button>
      </div>

      {/* Weekday headers */}
      <div className="grid grid-cols-7 gap-1 mb-2">
        {weekDays.map((wd) => (
          <div key={wd} className="text-center text-xs text-text-secondary py-1">
            {wd}
          </div>
        ))}
      </div>

      {/* Days grid */}
      <div className="grid grid-cols-7 gap-1">
        {Array.from({ length: firstDayOfWeek }, (_, i) => (
          <div key={`empty-${i}`} className="aspect-square" />
        ))}

        {Array.from({ length: daysInMonth }, (_, i) => {
          const day = i + 1;
          const dateStr = getDateStr(day);
          const future = isFuture(day);
          const active = hasData(day);
          const selected = isSelected(day);
          const todayDate = isToday(day);

          let cellClass = 'aspect-square flex items-center justify-center text-sm rounded-md transition-all ';
          let dotClass = '';

          if (future) {
            cellClass += 'text-[#484F58] cursor-not-allowed';
          } else if (selected) {
            cellClass += 'bg-accent text-white font-bold';
          } else if (todayDate) {
            cellClass += 'border border-accent text-accent font-medium hover:bg-[#1F6FEB22] cursor-pointer';
          } else if (active) {
            cellClass += 'text-text hover:bg-[#21262D] cursor-pointer bg-[#1f6feb11]';
          } else {
            cellClass += 'text-text-secondary hover:bg-[#21262D] cursor-pointer';
          }

          return (
            <button
              key={day}
              className={cellClass}
              disabled={future}
              onClick={() => handleSelect(day)}
              title={dateStr}
            >
              <div className="relative flex flex-col items-center">
                <span>{day}</span>
                {active && !selected && (
                  <span className="absolute -bottom-0.5 w-1 h-1 rounded-full bg-up" />
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 pt-3 border-t border-border text-xs text-text-secondary">
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-up inline-block" /> 有数据
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-accent inline-block" /> 已选中
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full border border-accent inline-block" /> 今天
        </span>
      </div>
    </div>
  );
}
