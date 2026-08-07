'use client';

import { useEffect, useRef, useState } from 'react';
import { Bot, Database, Loader2, Send, Trash2, User } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { readAuthSession } from '@/lib/authSession';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const USER_ID = 'web_user';
const WELCOME = '你好，我是小财。可以问我投资概念，也可以直接问股票代码、近期走势、板块资金、龙虎榜、宏观数据或你的个人股票池。';
const SUGGESTIONS = ['600519近一个月走势怎样？', '今天哪些板块资金流入？', '最近龙虎榜机构在买什么？', '结合宏观数据分析当前A股'];

type Mode = 'beginner' | 'professional';

interface UIMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: string[];
}

interface HistoryMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
}

export default function ChatInterface() {
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [input, setInput] = useState('');
  const [mode, setMode] = useState<Mode>('beginner');
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const response = await apiFetch<{ data: { messages: HistoryMessage[] } }>(`/ai/history?user_id=${USER_ID}&limit=80`);
        if (!active) return;
        const history = (response.data.messages || [])
          .filter((item) => item.role === 'user' || item.role === 'assistant')
          .map((item) => ({ id: `history-${item.id}`, role: item.role, content: item.content }));
        setMessages(history.length ? history : [{ id: 'welcome', role: 'assistant', content: WELCOME }]);
      } catch {
        if (active) setMessages([{ id: 'welcome', role: 'assistant', content: WELCOME }]);
      } finally {
        if (active) setLoadingHistory(false);
      }
    };
    void load();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: streaming ? 'auto' : 'smooth' });
  }, [messages, streaming]);

  const updateLastAssistant = (update: (message: UIMessage) => UIMessage) => {
    setMessages((previous) => {
      if (!previous.length) return previous;
      const next = [...previous];
      next[next.length - 1] = update(next[next.length - 1]);
      return next;
    });
  };

  const handleEvent = (raw: string) => {
    const dataLine = raw.split('\n').find((line) => line.startsWith('data:'));
    if (!dataLine) return;
    const payload = dataLine.slice(5).trim();
    if (!payload || payload === '[DONE]') return;
    try {
      const event = JSON.parse(payload);
      if (event.type === 'start') {
        updateLastAssistant((message) => ({ ...message, sources: Array.isArray(event.sources) ? event.sources : [] }));
      } else if (event.type === 'text') {
        updateLastAssistant((message) => ({ ...message, content: message.content + String(event.content || '') }));
      } else if (event.type === 'error') {
        updateLastAssistant((message) => ({ ...message, content: message.content || `AI服务暂时不可用：${event.content || '未知错误'}` }));
      }
    } catch {
      // A later complete SSE event will still be processed from the buffer.
    }
  };

  const send = async (rawMessage?: string) => {
    const text = String(rawMessage ?? input).trim();
    if (!text || streaming) return;
    const stamp = Date.now();
    setMessages((previous) => [
      ...previous,
      { id: `user-${stamp}`, role: 'user', content: text },
      { id: `assistant-${stamp}`, role: 'assistant', content: '', sources: [] },
    ]);
    setInput('');
    setStreaming(true);

    try {
      const session = readAuthSession();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (session?.token) headers.Authorization = `Bearer ${session.token}`;
      const response = await fetch(`${API_BASE}/api/v1/ai/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ user_id: USER_ID, message: text, context: { mode } }),
      });
      if (!response.ok || !response.body) {
        let detail = `请求失败：${response.status}`;
        try {
          const payload = await response.json();
          detail = payload?.detail || detail;
        } catch {
          // Keep the HTTP status fallback.
        }
        throw new Error(detail);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let boundary = buffer.indexOf('\n\n');
        while (boundary >= 0) {
          handleEvent(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf('\n\n');
        }
        if (done) break;
      }
      if (buffer.trim()) handleEvent(buffer);
    } catch (error) {
      updateLastAssistant((message) => ({
        ...message,
        content: message.content || (error instanceof Error ? error.message : 'AI服务暂时不可用，请稍后再试。'),
      }));
    } finally {
      setStreaming(false);
    }
  };

  const clear = async () => {
    if (streaming || clearing) return;
    setClearing(true);
    try {
      await apiFetch(`/ai/history?user_id=${USER_ID}`, { method: 'DELETE' });
      setMessages([{ id: `welcome-${Date.now()}`, role: 'assistant', content: WELCOME }]);
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="border border-border bg-card rounded-md flex flex-col h-[min(720px,calc(100vh-210px))] min-h-[520px] overflow-hidden">
      <div className="h-12 shrink-0 border-b border-border px-3 flex items-center justify-between gap-3 bg-[#0D1117]">
        <div className="inline-flex h-8 border border-border rounded-md overflow-hidden">
          {(['beginner', 'professional'] as Mode[]).map((item) => (
            <button key={item} type="button" onClick={() => setMode(item)} className={`px-3 text-xs ${mode === item ? 'bg-accent text-white' : 'text-text-secondary hover:text-text'}`}>
              {item === 'beginner' ? '入门' : '专业'}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => void clear()} disabled={streaming || clearing} className="h-8 w-8 grid place-items-center text-text-secondary hover:text-text disabled:opacity-40" title="清空对话" aria-label="清空对话">
          {clearing ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loadingHistory ? (
          <div className="h-full grid place-items-center text-xs text-text-secondary"><div className="text-center"><Loader2 size={22} className="animate-spin mx-auto mb-2 text-accent" />读取对话记录</div></div>
        ) : (
          <>
            {messages.map((message, index) => (
              <div key={message.id} className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}>
                {message.role === 'assistant' && <Avatar role="assistant" />}
                <div className={`max-w-[min(82%,760px)] min-w-0 rounded-md px-3.5 py-3 text-sm leading-7 ${message.role === 'user' ? 'bg-accent text-white' : 'bg-[#161B22] border border-border text-text'}`}>
                  <div className="whitespace-pre-wrap break-words">{message.content}</div>
                  {streaming && index === messages.length - 1 && message.role === 'assistant' && !message.content && <Loader2 size={15} className="animate-spin text-accent" />}
                  {message.role === 'assistant' && Boolean(message.sources?.length) && (
                    <div className="mt-2 pt-2 border-t border-border/70 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-text-secondary">
                      <Database size={11} />{message.sources?.map((source) => <span key={source}>{source}</span>)}
                    </div>
                  )}
                </div>
                {message.role === 'user' && <Avatar role="user" />}
              </div>
            ))}
            {messages.length === 1 && messages[0]?.id.startsWith('welcome') && (
              <div className="ml-11 flex flex-wrap gap-2">
                {SUGGESTIONS.map((suggestion) => <button key={suggestion} type="button" onClick={() => void send(suggestion)} className="border border-border rounded-md px-3 py-2 text-xs text-text-secondary hover:text-text hover:border-accent/60">{suggestion}</button>)}
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-border p-3 bg-[#0D1117]">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            placeholder="输入股票代码或投资问题"
            rows={2}
            maxLength={4000}
            className="flex-1 resize-none bg-card border border-border rounded-md px-3 py-2 text-sm leading-6 text-text placeholder:text-text-secondary focus:outline-none focus:border-accent"
            disabled={streaming || loadingHistory}
          />
          <button type="button" onClick={() => void send()} disabled={!input.trim() || streaming || loadingHistory} className="h-10 w-10 shrink-0 grid place-items-center bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-40" title="发送" aria-label="发送">
            {streaming ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
          </button>
        </div>
      </div>
    </div>
  );
}

function Avatar({ role }: { role: 'user' | 'assistant' }) {
  return <div className={`w-8 h-8 rounded-full grid place-items-center shrink-0 mt-1 ${role === 'assistant' ? 'bg-up/10 text-up' : 'bg-accent/15 text-accent'}`}>{role === 'assistant' ? <Bot size={16} /> : <User size={16} />}</div>;
}
