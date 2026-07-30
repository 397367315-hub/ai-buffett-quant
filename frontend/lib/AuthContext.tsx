'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

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

const SESSION_KEY = 'stockflow_session';
const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [username, setUsername] = useState('');

  useEffect(() => {
    const session = localStorage.getItem(SESSION_KEY);
    if (session) {
      try {
        const { username: u } = JSON.parse(session);
        setUsername(u);
        setIsLoggedIn(true);
      } catch {
        localStorage.removeItem(SESSION_KEY);
      }
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

      if (data.code === 0) {
        localStorage.setItem(SESSION_KEY, JSON.stringify({ username: user }));
        setUsername(user);
        setIsLoggedIn(true);
        return true;
      }
      return false;
    } catch {
      return false;
    }
  };

  const logout = () => {
    localStorage.removeItem(SESSION_KEY);
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
