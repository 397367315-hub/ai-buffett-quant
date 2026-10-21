import type { ReactNode } from 'react';

type IconName = 'flask'|'layers'|'users'|'activity'|'arrow'|'play'|'pause'|'refresh'|'check'|'target'|'shield'|'chart'|'spark'|'clock'|'close'|'chevron'|'database'|'info'|'wallet';
export function Icon({ name, size = 18, strokeWidth = 1.8 }: { name: IconName; size?: number; strokeWidth?: number }) {
  const items: Record<IconName, ReactNode> = {
    flask: <><path d="M9 3h6M10 3v5.2L5.5 17A2.4 2.4 0 0 0 7.6 20.6h8.8A2.4 2.4 0 0 0 18.5 17L14 8.2V3"/><path d="M7.2 14h9.6"/></>,
    layers: <><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></>,
    users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
    activity: <><path d="M3 17h3v-5h4v5h4V7h4v10h3"/><path d="M3 21h18"/></>,
    arrow: <><path d="M5 12h14m-6-6 6 6-6 6"/></>,
    play: <path d="m8 5 11 7-11 7V5Z"/>,
    pause: <><path d="M9 5v14M15 5v14"/></>,
    refresh: <><path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M5.9 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 12.1-3"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    target: <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
    chart: <><path d="m3 17 6-7 4 3 7-9"/><path d="M16 4h4v4"/></>,
    spark: <><path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3L12 3Z"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
    close: <path d="M5 5l14 14M19 5 5 19"/>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></>,
    wallet: <><rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10h18M7 15h4"/></>,
  };
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">{items[name]}</svg>;
}
