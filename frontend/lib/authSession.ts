export const AUTH_SESSION_KEY = 'stockflow_session';

export interface AuthSession {
  username: string;
  token: string;
}

export function readAuthSession(): AuthSession | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = JSON.parse(window.localStorage.getItem(AUTH_SESSION_KEY) || 'null');
    if (typeof value?.username === 'string' && typeof value?.token === 'string' && value.token) {
      return { username: value.username, token: value.token };
    }
  } catch {
    // Invalid or legacy sessions are cleared below.
  }
  window.localStorage.removeItem(AUTH_SESSION_KEY);
  return null;
}

export function writeAuthSession(session: AuthSession): void {
  window.localStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(session));
}

export function clearAuthSession(): void {
  if (typeof window !== 'undefined') window.localStorage.removeItem(AUTH_SESSION_KEY);
}
