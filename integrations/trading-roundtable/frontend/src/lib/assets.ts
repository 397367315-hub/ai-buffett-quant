export function assetUrl(path: string): string {
  if (/^(data:|https?:|blob:)/.test(path)) return path;
  const clean = path.replace(/^\/+/, '');
  return `${String(import.meta.env.BASE_URL || '/').replace(/\/$/, '')}/${clean}`;
}
