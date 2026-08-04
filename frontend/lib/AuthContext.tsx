'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { clearAuthSession, readAuthSession, writeAuthSession } from '@/lib/authSession';

interface AuthContextType {
  isLoggedIn: boolean;
  isAuthReady: boolean;
  username: string;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  isLoggedIn: false,
  isAuthReady: false,
  username: '',
  login: async () => false,
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

  const login = async (user: string, pass: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, password: pass }),
      });
      const data = await res.json();

      if (data.code === 0 && typeof data.data?.token === 'string') {
        const authenticatedUsername = data.data.username || user;
        writeAuthSession({ username: authenticatedUsername, token: data.data.token });
        setUsername(authenticatedUsername);
        setIsLoggedIn(true);
        return true;
      }
      return false;
    } catch {
      return false;
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
