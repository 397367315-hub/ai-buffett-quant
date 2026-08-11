'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { AlertTriangle, BookOpenText, Edit3, Loader2, NotebookPen, Plus, ShieldAlert, Trash2, X } from 'lucide-react';
import PersonalWorkspaceNav from '@/components/PersonalWorkspaceNav';
import StockKlineButton from '@/components/StockKlineButton';
import { apiFetch } from '@/lib/api';

type View = 'notes' | 'errors';

interface ResearchNote {
  id: number;
  code: string;
  name: string;
  first_researched_at: string | null;
  why_follow: string;
  competitive_advantage: string;
  risks: string;
  key_metrics: Record<string, unknown>;
  latest_view: string;
  tags: string[];
  error_count: number;
  updated_at: string | null;
}

interface ErrorRecord {
  id: number;
  occurred_on: string;
  error_type: string;
  code: string | null;
  name: string;
  lesson: string;
  prevention: string;
  repeat_count: number;
  requires_confirmation: boolean;
}

interface ErrorPattern {
  error_type: string;
  count: number;
  requires_confirmation: boolean;
  message: string | null;
}

interface NoteForm {
  id?: number;
  code: string;
  name: string;
  first_researched_at: string;
  why_follow: string;
  competitive_advantage: string;
  risks: string;
  key_metrics: string;
  latest_view: string;
  tags: string;
}

interface ErrorForm {
  occurred_on: string;
  error_type: string;
  code: string;
  name: string;
  lesson: string;
  prevention: string;
}

const today = () => new Date().toLocaleDateString('sv-SE');
const emptyNote = (): NoteForm => ({ code: '', name: '', first_researched_at: today(), why_follow: '', competitive_advantage: '', risks: '', key_metrics: '', latest_view: '', tags: '' });
const emptyError = (): ErrorForm => ({ occurred_on: today(), error_type: '', code: '', name: '', lesson: '', prevention: '' });
const metricsText = (metrics: Record<string, unknown>) => Object.entries(metrics || {}).map(([key, value]) => `${key}: ${String(value)}`).join('\n');
const parseMetrics = (text: string) => Object.fromEntries(text.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => { const split = line.search(/[:：]/); if (split < 0) return [line, '']; const key = line.slice(0, split).trim(); const raw = line.slice(split + 1).trim(); const numeric = Number(raw); return [key, raw !== '' && Number.isFinite(numeric) ? numeric : raw]; }));

export default function ResearchPage() {
  const [view, setView] = useState<View>('notes');
  const [notes, setNotes] = useState<ResearchNote[]>([]);
  const [errors, setErrors] = useState<ErrorRecord[]>([]);
  const [patterns, setPatterns] = useState<ErrorPattern[]>([]);
  const [forcedWarningCount, setForcedWarningCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noteForm, setNoteForm] = useState<NoteForm | null>(null);
  const [errorForm, setErrorForm] = useState<ErrorForm | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [noteResponse, errorResponse] = await Promise.all([
        apiFetch<{ data: { notes: ResearchNote[] } }>('/personal/notes'),
        apiFetch<{ data: { errors: ErrorRecord[]; patterns: ErrorPattern[]; forced_warning_count: number } }>('/personal/errors'),
      ]);
      setNotes(noteResponse.data.notes || []);
      setErrors(errorResponse.data.errors || []);
      setPatterns(errorResponse.data.patterns || []);
      setForcedWarningCount(errorResponse.data.forced_warning_count || 0);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究资料加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const saveNote = async (event: FormEvent) => {
    event.preventDefault();
    if (!noteForm) return;
    setSaving(true);
    setError(null);
    try {
      const payload = { ...noteForm, key_metrics: parseMetrics(noteForm.key_metrics), tags: noteForm.tags.split(/[,，]/).map((item) => item.trim()).filter(Boolean) };
      const path = noteForm.id ? `/personal/notes/${noteForm.id}` : '/personal/notes';
      await apiFetch(path, { method: noteForm.id ? 'PUT' : 'POST', body: JSON.stringify(payload) });
      setNoteForm(null);
      setMessage('研究笔记已保存');
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '研究笔记保存失败');
    } finally {
      setSaving(false);
    }
  };

  const saveError = async (event: FormEvent) => {
    event.preventDefault();
    if (!errorForm) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch('/personal/errors', { method: 'POST', body: JSON.stringify(errorForm) });
      setErrorForm(null);
      setMessage('错误模式已记录');
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '错误模式保存失败');
    } finally {
      setSaving(false);
    }
  };

  const removeNote = async (id: number) => {
    if (!window.confirm('确定删除这条研究笔记吗？')) return;
    try { await apiFetch(`/personal/notes/${id}`, { method: 'DELETE' }); await load(); } catch (caught) { setError(caught instanceof Error ? caught.message : '删除失败'); }
  };
  const removeError = async (id: number) => {
    if (!window.confirm('确定删除这条错误记录吗？')) return;
    try { await apiFetch(`/personal/errors/${id}`, { method: 'DELETE' }); await load(); } catch (caught) { setError(caught instanceof Error ? caught.message : '删除失败'); }
  };

  return <div className="max-w-7xl mx-auto px-4 py-5 md:py-6">
    <PersonalWorkspaceNav />
    <header className="flex flex-wrap items-start justify-between gap-4 mb-5"><div><h1 className="text-xl md:text-2xl font-bold text-text flex items-center gap-2"><NotebookPen size={22} className="text-accent" />研究与错误复盘</h1><p className="text-xs text-text-secondary mt-1">持续更新投资依据，并阻止重复犯错</p></div><button type="button" onClick={() => view === 'notes' ? setNoteForm(emptyNote()) : setErrorForm(emptyError())} className="inline-flex items-center gap-1.5 px-3 py-2 bg-accent text-white rounded-md text-xs"><Plus size={14} />{view === 'notes' ? '新建研究笔记' : '记录错误'}</button></header>
    {error && <div className="mb-4 border border-up/50 bg-[#EF535014] rounded-md p-3 text-xs text-up flex gap-2"><AlertTriangle size={15} className="shrink-0" />{error}</div>}
    {message && <div className="mb-4 border border-down/50 bg-[#26A69A12] rounded-md p-3 text-xs text-down flex items-center justify-between gap-3"><span>{message}</span><button type="button" onClick={() => setMessage(null)} title="关闭"><X size={14} /></button></div>}

    <div className="flex border-b border-border mb-5"><button type="button" onClick={() => setView('notes')} className={`px-5 py-2.5 text-sm border-b-2 ${view === 'notes' ? 'border-accent text-accent' : 'border-transparent text-text-secondary'}`}>研究笔记 <span className="font-mono text-xs ml-2">{notes.length}</span></button><button type="button" onClick={() => setView('errors')} className={`px-5 py-2.5 text-sm border-b-2 ${view === 'errors' ? 'border-accent text-accent' : 'border-transparent text-text-secondary'}`}>错误模式 <span className="font-mono text-xs ml-2">{errors.length}</span></button></div>

    {loading ? <div className="py-24 text-center"><Loader2 size={28} className="animate-spin text-accent mx-auto" /><div className="text-xs text-text-secondary mt-3">正在读取研究档案</div></div> : view === 'notes' ? <>
      {notes.length ? <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">{notes.map((note) => <article key={note.id} className="border border-border rounded-md p-4 min-w-0"><div className="flex items-start justify-between gap-3"><div><StockKlineButton code={note.code} name={note.name} className="text-sm font-semibold text-text">{note.name}<span className="font-mono text-text-secondary text-xs ml-2">{note.code}</span></StockKlineButton><div className="text-[11px] text-text-secondary mt-1">首次研究 {note.first_researched_at || '--'} · 更新 {note.updated_at ? new Date(note.updated_at).toLocaleDateString('zh-CN') : '--'}</div></div><div className="flex gap-1"><button type="button" onClick={() => setNoteForm({ id: note.id, code: note.code, name: note.name, first_researched_at: note.first_researched_at || today(), why_follow: note.why_follow, competitive_advantage: note.competitive_advantage, risks: note.risks, key_metrics: metricsText(note.key_metrics), latest_view: note.latest_view, tags: note.tags.join(', ') })} className="p-1.5 text-text-secondary hover:text-accent" title="编辑"><Edit3 size={14} /></button><button type="button" onClick={() => removeNote(note.id)} className="p-1.5 text-text-secondary hover:text-up" title="删除"><Trash2 size={14} /></button></div></div><div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-4 text-xs"><TextBlock label="关注原因" value={note.why_follow} /><TextBlock label="竞争优势" value={note.competitive_advantage} /><TextBlock label="主要风险" value={note.risks} warn /><TextBlock label="最新观点" value={note.latest_view} /></div>{Object.keys(note.key_metrics || {}).length > 0 && <div className="border-t border-border mt-4 pt-3 flex flex-wrap gap-x-4 gap-y-2">{Object.entries(note.key_metrics).map(([key, value]) => <span key={key} className="text-[11px] text-text-secondary">{key}：<b className="font-mono font-normal text-text">{String(value)}</b></span>)}</div>}<div className="mt-3 flex flex-wrap items-center gap-2">{note.tags.map((tag) => <span key={tag} className="border border-border rounded px-1.5 py-0.5 text-[10px] text-text-secondary">{tag}</span>)}{note.error_count > 0 && <span className="text-[11px] text-warn inline-flex items-center gap-1"><AlertTriangle size={11} />关联错误 {note.error_count} 次</span>}</div></article>)}</div> : <Empty icon={BookOpenText} text="还没有研究笔记" />}
    </> : <>
      {forcedWarningCount > 0 && <section className="border border-warn/50 bg-[#D2992210] rounded-md p-4 mb-4"><h2 className="text-sm font-semibold text-warn flex items-center gap-2"><ShieldAlert size={16} />{forcedWarningCount}类错误已触发交易前强制确认</h2><div className="mt-2 space-y-1">{patterns.filter((item) => item.requires_confirmation).map((item) => <p key={item.error_type} className="text-xs text-text-secondary">{item.message}</p>)}</div></section>}
      <section className="border border-border rounded-md overflow-hidden">{errors.length ? <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-xs"><thead className="border-b border-border text-text-secondary"><tr><th className="text-left px-4 py-2.5">日期</th><th className="text-left px-3">错误类型</th><th className="text-left px-3">股票</th><th className="text-left px-3">教训</th><th className="text-left px-3">避免方法</th><th className="text-right px-3">重复</th><th className="w-12 px-4"></th></tr></thead><tbody>{errors.map((item) => <tr key={item.id} className="border-b border-border/60 last:border-b-0 align-top"><td className="px-4 py-3 font-mono text-text-secondary">{item.occurred_on}</td><td className={item.requires_confirmation ? 'px-3 py-3 text-warn' : 'px-3 py-3 text-text'}>{item.error_type}</td><td className="px-3 py-3 text-text-secondary">{item.name || '--'}{item.code && <span className="font-mono ml-1">{item.code}</span>}</td><td className="px-3 py-3 text-text-secondary max-w-[260px] whitespace-normal">{item.lesson}</td><td className="px-3 py-3 text-text max-w-[260px] whitespace-normal">{item.prevention}</td><td className="px-3 py-3 text-right font-mono">{item.repeat_count}</td><td className="px-4 py-3"><button type="button" onClick={() => removeError(item.id)} className="p-1 text-text-secondary hover:text-up" title="删除"><Trash2 size={14} /></button></td></tr>)}</tbody></table></div> : <Empty icon={ShieldAlert} text="还没有错误记录" />}</section>
    </>}

    {noteForm && <Modal title={noteForm.id ? '编辑研究笔记' : '新建研究笔记'} onClose={() => setNoteForm(null)}><form onSubmit={saveNote} className="space-y-4"><div className="grid grid-cols-1 sm:grid-cols-3 gap-3"><Field label="股票代码"><input required value={noteForm.code} onChange={(event) => setNoteForm({ ...noteForm, code: event.target.value })} className="input" placeholder="600519" /></Field><Field label="股票名称"><input value={noteForm.name} onChange={(event) => setNoteForm({ ...noteForm, name: event.target.value })} className="input" /></Field><Field label="首次研究日期"><input type="date" value={noteForm.first_researched_at} onChange={(event) => setNoteForm({ ...noteForm, first_researched_at: event.target.value })} className="input" /></Field></div><Field label="为什么关注"><textarea value={noteForm.why_follow} onChange={(event) => setNoteForm({ ...noteForm, why_follow: event.target.value })} className="input min-h-20 resize-y" /></Field><Field label="竞争优势"><textarea value={noteForm.competitive_advantage} onChange={(event) => setNoteForm({ ...noteForm, competitive_advantage: event.target.value })} className="input min-h-20 resize-y" /></Field><Field label="主要风险"><textarea value={noteForm.risks} onChange={(event) => setNoteForm({ ...noteForm, risks: event.target.value })} className="input min-h-20 resize-y" /></Field><Field label="关键指标（每行“名称: 数值”）"><textarea value={noteForm.key_metrics} onChange={(event) => setNoteForm({ ...noteForm, key_metrics: event.target.value })} className="input min-h-20 font-mono resize-y" placeholder={'营收增速: 18.5\nROE: 12.3'} /></Field><Field label="最新观点"><textarea value={noteForm.latest_view} onChange={(event) => setNoteForm({ ...noteForm, latest_view: event.target.value })} className="input min-h-20 resize-y" /></Field><Field label="标签（逗号分隔）"><input value={noteForm.tags} onChange={(event) => setNoteForm({ ...noteForm, tags: event.target.value })} className="input" /></Field><FormActions saving={saving} onCancel={() => setNoteForm(null)} /></form></Modal>}
    {errorForm && <Modal title="记录错误模式" onClose={() => setErrorForm(null)}><form onSubmit={saveError} className="space-y-4"><div className="grid grid-cols-1 sm:grid-cols-2 gap-3"><Field label="发生日期"><input type="date" required value={errorForm.occurred_on} onChange={(event) => setErrorForm({ ...errorForm, occurred_on: event.target.value })} className="input" /></Field><Field label="错误类型"><input required value={errorForm.error_type} onChange={(event) => setErrorForm({ ...errorForm, error_type: event.target.value })} className="input" placeholder="例如：RSI超买追高" /></Field><Field label="股票代码（可选）"><input value={errorForm.code} onChange={(event) => setErrorForm({ ...errorForm, code: event.target.value })} className="input" /></Field><Field label="股票名称（可选）"><input value={errorForm.name} onChange={(event) => setErrorForm({ ...errorForm, name: event.target.value })} className="input" /></Field></div><Field label="本次教训"><textarea required value={errorForm.lesson} onChange={(event) => setErrorForm({ ...errorForm, lesson: event.target.value })} className="input min-h-24 resize-y" /></Field><Field label="以后如何避免"><textarea required value={errorForm.prevention} onChange={(event) => setErrorForm({ ...errorForm, prevention: event.target.value })} className="input min-h-24 resize-y" /></Field><FormActions saving={saving} onCancel={() => setErrorForm(null)} /></form></Modal>}

    <style jsx>{`.input { width: 100%; border: 1px solid #30363D; background: #0D1117; color: #E6EDF3; border-radius: 4px; padding: 8px 10px; font-size: 12px; outline: none; } .input:focus { border-color: #58A6FF; }`}</style>
  </div>;
}

function TextBlock({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) { return <div><div className="text-[10px] text-text-secondary mb-1">{label}</div><p className={`leading-5 whitespace-pre-wrap ${warn && value ? 'text-warn' : 'text-text-secondary'}`}>{value || '尚未填写'}</p></div>; }
function Empty({ icon: Icon, text }: { icon: typeof BookOpenText; text: string }) { return <div className="py-20 text-center"><Icon size={28} className="text-border mx-auto" /><div className="text-xs text-text-secondary mt-3">{text}</div></div>; }
function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) { return <div className="fixed inset-0 z-[80] bg-black/70 flex items-center justify-center p-3" role="dialog" aria-modal="true"><section className="w-full max-w-2xl max-h-[92vh] overflow-y-auto bg-card border border-border rounded-md"><header className="sticky top-0 bg-card z-10 flex items-center justify-between px-4 py-3 border-b border-border"><h2 className="text-sm font-semibold text-text">{title}</h2><button type="button" onClick={onClose} className="p-1 text-text-secondary hover:text-text" title="关闭"><X size={17} /></button></header><div className="p-4">{children}</div></section></div>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block"><span className="block text-[11px] text-text-secondary mb-1.5">{label}</span>{children}</label>; }
function FormActions({ saving, onCancel }: { saving: boolean; onCancel: () => void }) { return <div className="flex justify-end gap-2 pt-2"><button type="button" onClick={onCancel} className="px-3 py-2 border border-border rounded-md text-xs text-text-secondary">取消</button><button type="submit" disabled={saving} className="inline-flex items-center gap-1.5 px-4 py-2 bg-accent text-white rounded-md text-xs disabled:opacity-50">{saving && <Loader2 size={13} className="animate-spin" />}保存</button></div>; }
