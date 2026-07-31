'use client';

import { useEffect, useRef } from 'react';

export type FlowObserverBoardType = 'industry' | 'concept';
export type FlowObserverMode = 'live' | 'history';

export interface ObserverDateEntry {
  date: string;
  board_count: number;
  is_complete: boolean;
}

export interface ObserverRow {
  code: string;
  name: string;
  close_price: number;
  change_pct: number;
  main_net_inflow: number;
  main_net_inflow_pct: number;
  super_large_net_inflow: number;
  large_net_inflow: number;
  medium_net_inflow?: number;
  up_count: number;
  down_count: number;
  leading_stock?: string;
}

export interface ObserverMarket {
  sh_amount?: number;
  sh_index?: number;
  sh_change_pct?: number;
  data_date?: string;
}

export interface ObserverCoverage {
  snapshot_board_count: number;
  directory_board_count: number;
  is_complete: boolean;
}

export interface ObserverFlowData {
  board_type: FlowObserverBoardType;
  board_label: string;
  inflows: ObserverRow[];
  outflows: ObserverRow[];
  market: ObserverMarket;
  summary: {
    inflow_total: number;
    outflow_total: number;
    shown_net_flow: number;
    inflow_count: number;
    outflow_count: number;
    requested_limit: number;
  };
  source_status: {
    inflows: boolean;
    outflows: boolean;
    market: boolean;
  };
  available: boolean;
  source: string;
  is_realtime: boolean;
  data_date: string | null;
  updated_at: string;
  history_coverage?: ObserverCoverage;
}

interface FlowObserverCanvasProps {
  data: ObserverFlowData | null;
  mode: FlowObserverMode;
}

interface PathState {
  side: 'left' | 'right';
  row: ObserverRow;
  startX: number;
  startY: number;
  ctrl1X: number;
  ctrl1Y: number;
  ctrl2X: number;
  ctrl2Y: number;
  endX: number;
  endY: number;
  width: number;
  intensity: number;
  color: string;
}

interface ParticleState {
  pathIndex: number;
  phase: number;
  speed: number;
  radius: number;
  alpha: number;
  drift: number;
}

interface SceneState {
  paths: PathState[];
  particles: ParticleState[];
  center: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function createRandom(seed: number): () => number {
  let state = seed || 1;
  return () => {
    state = Math.imul(state ^ (state >>> 15), 1 | state);
    state ^= state + Math.imul(state ^ (state >>> 7), 61 | state);
    return ((state ^ (state >>> 14)) >>> 0) / 4294967296;
  };
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(date);
}

function formatAmount(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${(value / 1e8).toFixed(2)}亿`;
}

function formatSignedAmount(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value >= 0 ? '+' : '';
  return `${sign}${(value / 1e8).toFixed(2)}亿`;
}

function formatPercent(value: number | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

function distribute(count: number, top: number, bottom: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [(top + bottom) / 2];
  const gap = (bottom - top) / (count - 1);
  return Array.from({ length: count }, (_, index) => top + gap * index);
}

function cubicPoint(
  startX: number,
  startY: number,
  ctrl1X: number,
  ctrl1Y: number,
  ctrl2X: number,
  ctrl2Y: number,
  endX: number,
  endY: number,
  t: number,
): { x: number; y: number } {
  const u = 1 - t;
  const tt = t * t;
  const uu = u * u;
  const uuu = uu * u;
  const ttt = tt * t;
  return {
    x: uuu * startX + 3 * uu * t * ctrl1X + 3 * u * tt * ctrl2X + ttt * endX,
    y: uuu * startY + 3 * uu * t * ctrl1Y + 3 * u * tt * ctrl2Y + ttt * endY,
  };
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function buildScene(width: number, height: number, data: ObserverFlowData | null, mode: FlowObserverMode): SceneState | null {
  if (!data) return null;

  const outflows = [...(data.outflows || [])].slice(0, 12);
  const inflows = [...(data.inflows || [])].slice(0, 12);
  const rows = [...outflows, ...inflows];
  const maxAbs = Math.max(1, ...rows.map((row) => Math.abs(row.main_net_inflow || 0)));
  const center = {
    x: width * 0.5,
    y: height * 0.52,
    width: clamp(width * 0.24, 220, 340),
    height: clamp(height * 0.18, 122, 170),
  };
  const leftX = width * 0.11;
  const rightX = width * 0.89;
  const top = width < 620 ? height * 0.28 : height * 0.16;
  const bottom = width < 620 ? height * 0.82 : height * 0.84;
  const leftYs = distribute(outflows.length, top, bottom);
  const rightYs = distribute(inflows.length, top, bottom);

  const paths: PathState[] = [];
  const particles: ParticleState[] = [];

  const intensityFor = (value: number): number => {
    const normalized = Math.log10(Math.abs(value) + 1) / Math.log10(maxAbs + 1);
    return clamp(normalized, 0.1, 1);
  };

  const makeParticleCount = (intensity: number): number => {
    if (intensity >= 0.86) return 10;
    if (intensity >= 0.72) return 8;
    if (intensity >= 0.55) return 6;
    if (intensity >= 0.35) return 5;
    return 4;
  };

  outflows.forEach((row, index) => {
    const intensity = intensityFor(row.main_net_inflow);
    const seed = hashString(`${row.code}-${row.main_net_inflow}-${index}-out`);
    const rand = createRandom(seed);
    const y = leftYs[index];
    const drift = (index - (outflows.length - 1) / 2) * height * 0.02;
    const path = {
      side: 'left' as const,
      row,
      startX: leftX,
      startY: y,
      ctrl1X: width * 0.26,
      ctrl1Y: y + drift * 1.1,
      ctrl2X: center.x - width * 0.12,
      ctrl2Y: center.y - drift * 0.85,
      endX: center.x - center.width * 0.22,
      endY: center.y + drift * 0.45,
      width: 1.4 + intensity * 4.8,
      intensity,
      color: 'rgba(38, 166, 154, 0.92)',
    } satisfies PathState;
    paths.push(path);

    const count = makeParticleCount(intensity);
    for (let i = 0; i < count; i += 1) {
      particles.push({
        pathIndex: paths.length - 1,
        phase: rand(),
        speed: 0.12 + intensity * 0.18 + rand() * 0.04,
        radius: 1.2 + intensity * 1.4 + rand() * 0.6,
        alpha: 0.65 + intensity * 0.25,
        drift: (rand() - 0.5) * 0.01,
      });
    }
  });

  inflows.forEach((row, index) => {
    const intensity = intensityFor(row.main_net_inflow);
    const seed = hashString(`${row.code}-${row.main_net_inflow}-${index}-in`);
    const rand = createRandom(seed);
    const y = rightYs[index];
    const drift = (index - (inflows.length - 1) / 2) * height * 0.02;
    const path = {
      side: 'right' as const,
      row,
      startX: rightX,
      startY: y,
      ctrl1X: width * 0.74,
      ctrl1Y: y + drift * 1.1,
      ctrl2X: center.x + width * 0.12,
      ctrl2Y: center.y - drift * 0.85,
      endX: center.x + center.width * 0.22,
      endY: center.y + drift * 0.45,
      width: 1.4 + intensity * 4.8,
      intensity,
      color: 'rgba(239, 83, 80, 0.92)',
    } satisfies PathState;
    paths.push(path);

    const count = makeParticleCount(intensity);
    for (let i = 0; i < count; i += 1) {
      particles.push({
        pathIndex: paths.length - 1,
        phase: rand(),
        speed: 0.12 + intensity * 0.18 + rand() * 0.04,
        radius: 1.2 + intensity * 1.4 + rand() * 0.6,
        alpha: 0.65 + intensity * 0.25,
        drift: (rand() - 0.5) * 0.01,
      });
    }
  });

  if (mode === 'history') {
    particles.forEach((particle, index) => {
      particle.speed *= 0.92 + (index % 3) * 0.025;
      particle.alpha *= 0.96;
    });
  }

  return { paths, particles, center };
}

function drawPath(
  ctx: CanvasRenderingContext2D,
  path: PathState,
): void {
  ctx.save();
  ctx.lineWidth = path.width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = path.color;
  ctx.shadowBlur = 8 + path.intensity * 8;
  ctx.shadowColor = path.color;
  ctx.globalAlpha = 0.28 + path.intensity * 0.42;
  ctx.beginPath();
  ctx.moveTo(path.startX, path.startY);
  ctx.bezierCurveTo(path.ctrl1X, path.ctrl1Y, path.ctrl2X, path.ctrl2Y, path.endX, path.endY);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = path.color;
  ctx.globalAlpha = 0.9;
  ctx.beginPath();
  ctx.arc(path.startX, path.startY, 2.6, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(path.endX, path.endY, 2.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  ctx.save();
  ctx.fillStyle = 'rgba(230, 237, 243, 0.72)';
  ctx.font = '12px JetBrains Mono, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const badgeX = path.side === 'left' ? path.startX - 16 : path.startX + 16;
  drawRoundedRect(ctx, badgeX - 12, path.startY - 10, 24, 20, 8);
  ctx.fillStyle = path.side === 'left' ? 'rgba(38, 166, 154, 0.18)' : 'rgba(239, 83, 80, 0.18)';
  ctx.fill();
  ctx.strokeStyle = path.side === 'left' ? 'rgba(38, 166, 154, 0.38)' : 'rgba(239, 83, 80, 0.38)';
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = '#E6EDF3';
  ctx.fillText(path.side === 'left' ? '出' : '入', badgeX, path.startY + 0.5);
  ctx.restore();
}

function renderCanvas(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  scene: SceneState | null,
  data: ObserverFlowData | null,
  mode: FlowObserverMode,
  timestamp: number,
): void {
  ctx.clearRect(0, 0, width, height);

  const bg = ctx.createLinearGradient(0, 0, width, height);
  bg.addColorStop(0, '#05070A');
  bg.addColorStop(1, '#090D12');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
  ctx.lineWidth = 1;
  const gridGapX = Math.max(48, Math.floor(width / 11));
  const gridGapY = Math.max(48, Math.floor(height / 9));
  for (let x = 0; x <= width; x += gridGapX) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 0; y <= height; y += gridGapY) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  ctx.restore();

  if (!data || !scene) {
    ctx.save();
    ctx.fillStyle = '#E6EDF3';
    ctx.font = '600 18px PingFang SC, Microsoft YaHei, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('暂无资金流数据', width / 2, height / 2 - 8);
    ctx.fillStyle = '#8B949E';
    ctx.font = '13px PingFang SC, Microsoft YaHei, sans-serif';
    ctx.fillText('请检查数据源或切换到其他板块。', width / 2, height / 2 + 18);
    ctx.restore();
    return;
  }

  const { center } = scene;
  const boardLabel = data.board_label || (data.board_type === 'industry' ? '行业板块' : '概念板块');
  const statusLabel = data.is_realtime ? '实时' : '历史缓存';
  const sourceLabel = data.source === 'cache' ? '本地缓存' : '东方财富实时源';

  ctx.save();
  ctx.fillStyle = 'rgba(8, 10, 14, 0.86)';
  const compactHeader = width < 620;
  const headerWidth = compactHeader ? width - 32 : Math.min(width - 32, 260);
  const headerHeight = compactHeader ? 126 : 96;
  drawRoundedRect(ctx, 16, 16, headerWidth, headerHeight, 14);
  ctx.fill();
  ctx.strokeStyle = 'rgba(88, 166, 255, 0.20)';
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.fillStyle = '#E6EDF3';
  ctx.font = '600 17px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(boardLabel, 30, 28);
  ctx.fillStyle = '#8B949E';
  ctx.font = '12px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.fillText(`${statusLabel} · ${sourceLabel}`, 30, 52);
  ctx.fillText(`更新 ${formatDateTime(data.updated_at)}`, 30, 72);
  if (compactHeader) {
    const compactLine = mode === 'live'
      ? `沪市成交额 ${formatAmount(data.market?.sh_amount)}`
      : `历史日级快照 ${data.data_date || '--'}`;
    ctx.fillText(compactLine, 30, 94);
  }
  ctx.restore();

  if (!compactHeader) {
    ctx.save();
    ctx.fillStyle = 'rgba(8, 10, 14, 0.86)';
    drawRoundedRect(ctx, width - 222, 16, 206, 96, 14);
    ctx.fill();
    ctx.strokeStyle = 'rgba(88, 166, 255, 0.20)';
    ctx.stroke();
    ctx.fillStyle = '#E6EDF3';
    ctx.font = '600 15px PingFang SC, Microsoft YaHei, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(mode === 'live' ? '沪市成交额' : '历史日级快照', width - 206, 28);
    ctx.fillStyle = '#8B949E';
    ctx.font = '12px PingFang SC, Microsoft YaHei, sans-serif';
    if (mode === 'live') {
      ctx.fillText(formatAmount(data.market?.sh_amount), width - 206, 52);
      ctx.fillText(
        `上证 ${data.market?.sh_index?.toFixed(2) ?? '--'} · ${formatPercent(data.market?.sh_change_pct)}`,
        width - 206,
        72,
      );
    } else {
      ctx.fillText(data.data_date || '--', width - 206, 52);
      const coverage = data.history_coverage;
      ctx.fillText(
        coverage
          ? `${coverage.snapshot_board_count}/${coverage.directory_board_count} · ${coverage.is_complete ? '完整' : '部分'}`
          : '缓存回放',
        width - 206,
        72,
      );
    }
    ctx.restore();
  }

  const centerBoxWidth = center.width;
  const centerBoxHeight = center.height;
  const centerX = center.x - centerBoxWidth / 2;
  const centerY = center.y - centerBoxHeight / 2;

  ctx.save();
  ctx.fillStyle = 'rgba(10, 13, 18, 0.96)';
  drawRoundedRect(ctx, centerX, centerY, centerBoxWidth, centerBoxHeight, 20);
  ctx.fill();
  ctx.strokeStyle = data.is_realtime ? 'rgba(239, 83, 80, 0.48)' : 'rgba(88, 166, 255, 0.42)';
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.fillStyle = data.is_realtime ? 'rgba(239, 83, 80, 0.12)' : 'rgba(88, 166, 255, 0.10)';
  drawRoundedRect(ctx, centerX + 10, centerY + 10, centerBoxWidth - 20, 22, 12);
  ctx.fill();
  ctx.fillStyle = '#E6EDF3';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = '600 14px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.fillText(mode === 'live' ? '实时观察' : '历史回放', center.x, centerY + 21);

  ctx.font = '600 24px PingFang SC, Microsoft YaHei, sans-serif';
  const mainLine = mode === 'live' ? formatAmount(data.market?.sh_amount) : (data.data_date || '--');
  ctx.fillText(mainLine, center.x, center.y - 3);
  ctx.fillStyle = '#8B949E';
  ctx.font = '13px PingFang SC, Microsoft YaHei, sans-serif';
  const subLine = mode === 'live'
    ? `上证 ${data.market?.sh_index?.toFixed(2) ?? '--'} · ${formatPercent(data.market?.sh_change_pct)}`
    : data.history_coverage
      ? `缓存 ${data.history_coverage.snapshot_board_count}/${data.history_coverage.directory_board_count}`
      : '历史缓存';
  ctx.fillText(subLine, center.x, center.y + 28);
  ctx.fillText(
    mode === 'live'
      ? `${data.summary.inflow_count} 流入 / ${data.summary.outflow_count} 流出`
      : `净额 ${formatSignedAmount(data.summary.shown_net_flow)}`,
    center.x,
    center.y + 48,
  );
  ctx.restore();

  const tick = timestamp / 1000;
  scene.paths.forEach((path) => {
    drawPath(ctx, path);
  });

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  scene.particles.forEach((particle, index) => {
    const path = scene.paths[particle.pathIndex];
    if (!path) return;
    const progress = (tick * particle.speed + particle.phase + particle.drift) % 1;
    const point = cubicPoint(
      path.startX,
      path.startY,
      path.ctrl1X,
      path.ctrl1Y,
      path.ctrl2X,
      path.ctrl2Y,
      path.endX,
      path.endY,
      progress,
    );
    const trailProgress = Math.max(0, progress - 0.04);
    const trail = cubicPoint(
      path.startX,
      path.startY,
      path.ctrl1X,
      path.ctrl1Y,
      path.ctrl2X,
      path.ctrl2Y,
      path.endX,
      path.endY,
      trailProgress,
    );

    ctx.fillStyle = path.color;
    ctx.globalAlpha = particle.alpha;
    ctx.shadowColor = path.color;
    ctx.shadowBlur = 12;
    ctx.beginPath();
    ctx.arc(point.x, point.y, particle.radius, 0, Math.PI * 2);
    ctx.fill();

    ctx.globalAlpha = particle.alpha * 0.18;
    ctx.beginPath();
    ctx.arc(trail.x, trail.y, particle.radius * 1.7, 0, Math.PI * 2);
    ctx.fill();

    if (index % 3 === 0) {
      ctx.globalAlpha = particle.alpha * 0.14;
      ctx.beginPath();
      ctx.arc(point.x, point.y, particle.radius * 2.8, 0, Math.PI * 2);
      ctx.fill();
    }
  });
  ctx.restore();

  ctx.save();
  ctx.fillStyle = 'rgba(230, 237, 243, 0.72)';
  ctx.font = '12px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = 'rgba(239, 83, 80, 0.72)';
  ctx.fillRect(24, height - 30, 10, 10);
  ctx.fillStyle = 'rgba(230, 237, 243, 0.72)';
  ctx.fillText('红色 = 主力净流入', 40, height - 24);
  ctx.fillStyle = 'rgba(38, 166, 154, 0.72)';
  ctx.fillRect(154, height - 30, 10, 10);
  ctx.fillStyle = 'rgba(230, 237, 243, 0.72)';
  ctx.fillText('绿色 = 主力净流出', 170, height - 24);
  if (width >= 620) {
    ctx.fillText(`数据日期 ${data.data_date || '--'}`, width - 136, height - 24);
  }
  ctx.restore();
}

export default function FlowObserverCanvas({ data, mode }: FlowObserverCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<SceneState | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;

    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      const width = Math.max(320, Math.floor(rect.width));
      const height = Math.max(420, Math.floor(rect.height));
      const dpr = window.devicePixelRatio || 1;
      sizeRef.current = { width, height };
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        sceneRef.current = buildScene(width, height, data, mode);
      }
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    return () => {
      observer.disconnect();
    };
  }, [data, mode]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const frame = (timestamp: number) => {
      const { width, height } = sizeRef.current;
      if (width > 0 && height > 0) {
        renderCanvas(ctx, width, height, sceneRef.current, data, mode, timestamp);
      }
      rafRef.current = window.requestAnimationFrame(frame);
    };

    rafRef.current = window.requestAnimationFrame(frame);
    return () => {
      if (rafRef.current) {
        window.cancelAnimationFrame(rafRef.current);
      }
    };
  }, [data, mode]);

  return (
    <div ref={wrapRef} className="relative h-full min-h-[540px] w-full overflow-hidden bg-[#05070A]">
      <canvas ref={canvasRef} className="block h-full w-full" />
      {!data && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-center">
          <div className="rounded-lg border border-border bg-[#0D1117]/80 px-4 py-3 text-sm text-text-secondary">
            正在等待资金流数据
          </div>
        </div>
      )}
    </div>
  );
}
