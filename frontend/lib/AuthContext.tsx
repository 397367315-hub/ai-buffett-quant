'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { clearAuthSession, readAuthSession, writeAuthSession } from '@/lib/authSession';

interface AuthContextType {
  isLoggedIn: boolean;
  isAuthReady: boolean;
  username: string;
  login: (username: string, password: string) => Promise<LoginResult>;
  logout: () => void;
}

export type LoginFailureReason =
  | 'invalid_credentials'
  | 'unauthorized'
  | 'backend_unreachable'
  | 'timeout'
  | 'response_error';

export type LoginResult =
  | { ok: true; username: string }
  | { ok: false; reason: LoginFailureReason; message: string; status?: number };

const AuthContext = createContext<AuthContextType>({
  isLoggedIn: false,
  isAuthReady: false,
  username: '',
  login: async () => ({
    ok: false,
    reason: 'response_error',
    message: '登录服务暂时不可用，请稍后重试',
  }),
  logout: () => {},
});

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [username, setUsername] = useState('');

  useEffect(() => {
    const session = readAuthSession();
    if (session) {
      setUsername(session.username);
      setIsLoggedIn(true);
    }
    setIsAuthReady(true);
  }, []);

  const login = async (user: string, pass: string): Promise<LoginResult> => {
    const controller = new AbortController();
    const timeoutMs = 15000;
    const timeoutHandle = window.setTimeout(() => controller.abort(), timeoutMs);

    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, password: pass }),
        signal: controller.signal,
      });

      let data: any = null;
      try {
        data = await res.json();
      } catch {
        if (res.status === 401) {
          return { ok: false, reason: 'unauthorized', message: '登录未获授权，请重新登录', status: res.status };
        }
        if (res.status === 408 || res.status === 504) {
          return { ok: false, reason: 'timeout', message: '登录请求超时，请稍后重试', status: res.status };
        }
        return {
          ok: false,
          reason: res.status >= 500 ? 'backend_unreachable' : 'response_error',
          message: res.status >= 500 ? '后端暂时不可用，请稍后重试' : '登录响应格式异常，请稍后重试',
          status: res.status,
        };
      }

      if (res.status === 401) {
        const reason = data?.error === 'INVALID_CREDENTIALS' || data?.code === 401
          ? 'invalid_credentials'
          : 'unauthorized';
        return {
          ok: false,
          reason,
          message: reason === 'invalid_credentials' ? '账号或密码错误，请重试' : '登录未获授权，请重新登录',
          status: res.status,
        };
      }

      if (res.status === 408 || res.status === 504) {
        return { ok: false, reason: 'timeout', message: '登录请求超时，请稍后重试', status: res.status };
      }

      if (res.status >= 500) {
        return { ok: false, reason: 'backend_unreachable', message: '后端暂时不可用，请稍后重试', status: res.status };
      }

      if (!res.ok) {
        return {
          ok: false,
          reason: 'response_error',
          message: typeof data?.message === 'string' ? data.message : `登录请求失败（${res.status}）`,
          status: res.status,
        };
      }

      if (data.code === 0 && typeof data.data?.token === 'string') {
        const authenticatedUsername = data.data.username || user;
        writeAuthSession({ username: authenticatedUsername, token: data.data.token });
        setUsername(authenticatedUsername);
        setIsLoggedIn(true);
        return { ok: true, username: authenticatedUsername };
      }
      return {
        ok: false,
        reason: 'response_error',
        message: '登录响应缺少有效会话信息，请稍后重试',
        status: res.status,
      };
    } catch (caught) {
      if (controller.signal.aborted) {
        return { ok: false, reason: 'timeout', message: '登录请求超时，请稍后重试' };
      }
      if (caught instanceof TypeError) {
        return { ok: false, reason: 'backend_unreachable', message: '后端连接暂时中断，请稍后重试' };
      }
      return { ok: false, reason: 'response_error', message: '登录请求异常，请稍后重试' };
    } finally {
      window.clearTimeout(timeoutHandle);
    }
  };

  const logout = () => {
    clearAuthSession();
    setUsername('');
    setIsLoggedIn(false);
  };

  return (
    <AuthContext.Provider value={{ isLoggedIn, isAuthReady, username, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
