'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/AuthContext';
import { Eye, EyeOff, Lock, User, TrendingUp, LayoutDashboard, Lightbulb, Sparkles, Zap, Bot } from 'lucide-react';

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!username.trim() || !password.trim()) {
      setError('请输入账号和密码');
      return;
    }

    setLoading(true);
    await new Promise(resolve => setTimeout(resolve, 400));

    const success = await login(username, password);
    setLoading(false);

    if (success) {
      router.push('/market');
    } else {
      setError('账号或密码错误，请重试');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex overflow-auto" style={{
      background: 'radial-gradient(circle at 13% 16%, rgba(88,166,255,0.18), transparent 30%), radial-gradient(circle at 38% 72%, rgba(38,166,154,0.12), transparent 34%), linear-gradient(135deg, #0D1117 0%, #131a24 48%, #0D1117 100%)',
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    }}>
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute w-[600px] h-[600px] rounded-full blur-[120px] opacity-20" 
          style={{ background: 'linear-gradient(135deg, #58A6FF, #26A69A)', top: '-10%', left: '-5%' }} />
        <div className="absolute w-[400px] h-[400px] rounded-full blur-[100px] opacity-10" 
          style={{ background: 'linear-gradient(135deg, #D29922, #EF5350)', bottom: '-5%', right: '-5%' }} />
      </div>

      <div className="relative z-10 w-full min-h-screen flex items-center justify-center p-6">
        <div className="w-full max-w-5xl flex flex-col lg:flex-row gap-0 lg:gap-8 items-stretch">
          
          {/* 左侧品牌区 */}
          <div className="flex-1 flex flex-col justify-center p-8 lg:p-12 lg:pr-4" style={{ animation: 'loginPanelEnter 0.8s ease both' }}>
            <div className="mb-8">
              <div className="flex items-center gap-3 mb-4">
                <span className="text-4xl">📈</span>
                <div>
                  <h1 className="text-3xl lg:text-4xl font-bold text-[#E6EDF3] tracking-tight">AI 巴菲特</h1>
                  <p className="text-lg text-[#58A6FF] font-medium tracking-wide mt-0.5">量化分析系统</p>
                </div>
              </div>
              <p className="text-[#8B949E] text-sm leading-relaxed max-w-md">
                AI驱动 · 多策略量化 · 实时资金追踪<br />
                用巴菲特的智慧 + AI的速度，洞察A股市场
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-8">
              {[
                { icon: LayoutDashboard, label: '专业看板', desc: '12个数据看板' },
                { icon: Bot, label: 'AI量化交易', desc: '三策略自动PK' },
                { icon: Lightbulb, label: '新手学堂', desc: '术语+案例+百科' },
                { icon: Sparkles, label: '智能分析', desc: '资金流向+情绪' },
              ].map((item) => {
                const Icon = item.icon;
                return (
                  <div key={item.label} className="bg-white/5 border border-white/10 rounded-lg p-3 backdrop-blur-sm">
                    <Icon size={16} className="text-[#58A6FF] mb-1.5" />
                    <div className="text-sm font-medium text-[#E6EDF3]">{item.label}</div>
                    <div className="text-xs text-[#8B949E]">{item.desc}</div>
                  </div>
                );
              })}
            </div>

            <div className="text-xs text-[#8B949E] space-y-1">
              <p>数据来源：东方财富公开API | 仅供学习参考</p>
              <p>管理员分配账号，请联系系统管理员获取</p>
            </div>
          </div>

          {/* 右侧登录表单 */}
          <div className="lg:w-[420px] flex-shrink-0" style={{ animation: 'loginPanelEnter 0.6s ease both 0.15s' }}>
            <div className="bg-[#161B22]/90 backdrop-blur-xl border border-[#30363D] rounded-2xl p-8 shadow-2xl">
              <div className="mb-6">
                <div className="text-xs text-[#58A6FF] font-medium tracking-widest mb-2 uppercase">Account Access</div>
                <h2 className="text-xl font-bold text-[#E6EDF3]">登录系统</h2>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm text-[#8B949E] mb-1.5">账号</label>
                  <div className="relative">
                    <User size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#484F58]" />
                    <input
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="请输入管理员账号"
                      autoComplete="username"
                      className="w-full bg-[#0D1117] border border-[#30363D] rounded-lg pl-9 pr-3 py-2.5 text-sm text-[#E6EDF3] placeholder:text-[#484F58] focus:outline-none focus:border-[#58A6FF] transition-colors"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm text-[#8B949E] mb-1.5">密码</label>
                  <div className="relative">
                    <Lock size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#484F58]" />
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="请输入密码"
                      autoComplete="current-password"
                      className="w-full bg-[#0D1117] border border-[#30363D] rounded-lg pl-9 pr-10 py-2.5 text-sm text-[#E6EDF3] placeholder:text-[#484F58] focus:outline-none focus:border-[#58A6FF] transition-colors"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-[#484F58] hover:text-[#8B949E]"
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>

                {error && (
                  <div className="bg-[#EF535015] border border-[#EF535033] text-[#EF5350] text-sm rounded-lg px-3 py-2">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-2.5 bg-[#58A6FF] text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
                >
                  {loading ? (
                    <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <TrendingUp size={16} />
                  )}
                  {loading ? '验证中...' : '进入系统'}
                </button>

                <div className="pt-4 border-t border-[#30363D]">
                  <div className="flex items-center justify-center gap-1 text-xs text-[#484F58]">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#26A69A]" />
                    安全连接 · 管理员账号登录
                  </div>
                </div>
              </form>
            </div>
          </div>

        </div>
      </div>

      <style jsx>{`
        @keyframes loginPanelEnter {
          from { opacity: 0; transform: translateY(14px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
