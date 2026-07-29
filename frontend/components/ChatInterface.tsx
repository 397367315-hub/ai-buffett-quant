'use client';

import { useState, useRef, useEffect } from 'react';
import { Send, User, Bot } from 'lucide-react';
import { ChatMessage } from '@/lib/types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export default function ChatInterface() {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'assistant', content: '👋 你好！我是小财，你的A股入门老师。有什么不懂的，尽管问我！\n\n比如你可以问：\n• "什么是主力净流入？"\n• "北向资金是什么意思？"\n• "怎么看龙虎榜？"' },
  ]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || streaming) return;

    const userMsg: ChatMessage = { role: 'user', content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setStreaming(true);

    const assistantMsg: ChatMessage = { role: 'assistant', content: '' };
    setMessages((prev) => [...prev, assistantMsg]);

    try {
      const response = await fetch(`${API_BASE}/api/v1/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: 'web_user',
          message: input,
          context: { mode: 'beginner' },
        }),
      });

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          const lines = chunk.split('\n').filter((l) => l.startsWith('data: '));

          for (const line of lines) {
            const jsonStr = line.slice(6);
            if (jsonStr === '[DONE]') continue;

            try {
              const parsed = JSON.parse(jsonStr);
              if (parsed.type === 'text') {
                setMessages((prev) => {
                  const updated = [...prev];
                  const last = updated[updated.length - 1];
                  updated[updated.length - 1] = { ...last, content: last.content + parsed.content };
                  return updated;
                });
              }
            } catch (e) {
              // skip parse errors
            }
          }
        }
      }
    } catch (err) {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: 'assistant', content: '抱歉，AI服务暂时不可用。请稍后再试。' };
        return updated;
      });
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="bg-card border border-border rounded-lg flex flex-col h-[600px]">
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
            {msg.role === 'assistant' && (
              <div className="w-8 h-8 rounded-full bg-[#EF535022] flex items-center justify-center shrink-0 mt-1">
                <Bot size={16} className="text-up" />
              </div>
            )}
            <div
              className={`max-w-[80%] rounded-lg px-4 py-3 text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-accent text-white'
                  : 'bg-[#0D1117] border border-border text-text'
              }`}
            >
              <div className="whitespace-pre-wrap">{msg.content}</div>
              {streaming && i === messages.length - 1 && msg.role === 'assistant' && (
                <span className="inline-block w-2 h-4 bg-text-secondary animate-pulse ml-0.5 align-middle" />
              )}
            </div>
            {msg.role === 'user' && (
              <div className="w-8 h-8 rounded-full bg-[#58A6FF22] flex items-center justify-center shrink-0 mt-1">
                <User size={16} className="text-accent" />
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="border-t border-border p-4">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="输入问题，比如：什么是主力净流入？"
            className="flex-1 bg-[#0D1117] border border-border rounded-md px-4 py-2 text-sm text-text placeholder:text-text-secondary focus:outline-none focus:border-accent"
            disabled={streaming}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || streaming}
            className="px-4 py-2 bg-accent text-white rounded-md hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 text-sm"
          >
            <Send size={14} />
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
