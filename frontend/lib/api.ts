const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}/api/v1${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function formatYi(value: number): string {
  const yi = value / 1e8;
  const sign = value >= 0 ? '+' : '';
  return `${sign}${yi.toFixed(2)}亿`;
}

export function formatYiShort(value: number): string {
  const yi = value / 1e8;
  return `${yi.toFixed(1)}亿`;
}

export function getChangeColor(value: number): string {
  if (value > 0) return 'text-[#EF5350]';
  if (value < 0) return 'text-[#26A69A]';
  return 'text-[#8B949E]';
}

export function getBgColor(value: number): string {
  if (value > 0) return '#EF5350';
  if (value < 0) return '#26A69A';
  return '#8B949E';
}
