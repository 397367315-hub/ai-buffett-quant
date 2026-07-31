'use client';

import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';

export type FlowObserverBoardType = 'industry' | 'concept';
export type FlowObserverMode = 'live' | 'history';
export type FlowNetworkNodeType = 'outflow' | 'inflow' | 'new_money' | 'market_exit';

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

export interface ObserverNetworkNode {
  type: FlowNetworkNodeType;
  code: string;
  name: string;
}

export interface ObserverTransfer {
  source: ObserverNetworkNode;
  target: ObserverNetworkNode;
  amount: number;
  inferred?: boolean;
  basis?: string;
}

export interface ObserverFlowInference {
  method: string;
  label: string;
  description: string;
  confidence: string;
  paired_amount: number;
  inflow_total: number;
  outflow_total: number;
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
  transfers?: ObserverTransfer[];
  flow_inference?: ObserverFlowInference;
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
  playbackProgress?: number;
  historyDates?: string[];
}

type FlowSide = 'left' | 'right';

interface FlowVisualNode {
  key: string;
  type: FlowNetworkNodeType;
  name: string;
  code: string;
  value: number;
  side: FlowSide;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
}

interface FlowRibbon {
  source: FlowVisualNode;
  target: FlowVisualNode;
  amount: number;
  width: number;
  startX: number;
  startY: number;
  ctrl1X: number;
  ctrl1Y: number;
  ctrl2X: number;
  ctrl2Y: number;
  endX: number;
  endY: number;
  startColor: string;
  endColor: string;
  index: number;
}

interface SceneState {
  nodes: FlowVisualNode[];
  ribbons: FlowRibbon[];
  centerX: number;
}

interface TransitionState {
  from: ObserverFlowData | null;
  to: ObserverFlowData | null;
  startedAt: number;
}

interface HoveredFlow {
  key: string;
  sourceName: string;
  targetName: string;
  amount: number;
  x: number;
  y: number;
}

const OUTFLOW_COLOR = '#00FF7F';
const INFLOW_COLOR = '#FF4D4D';
const GOLD = '#FFD43B';
const MARKET_EXIT = '#D7DCE2';
const MUTED = '#8E8E93';
const TRANSITION_MS = 900;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function lerp(start: number, end: number, progress: number): number {
  return start + (end - start) * progress;
}

function easeInOutCubic(progress: number): number {
  return progress < 0.5
    ? 4 * progress * progress * progress
    : 1 - Math.pow(-2 * progress + 2, 3) / 2;
}

function numberOr(value: number | undefined | null, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function seededUnit(seed: number): number {
  let value = seed || 1;
  value = Math.imul(value ^ (value >>> 15), 1 | value);
  value ^= value + Math.imul(value ^ (value >>> 7), 61 | value);
  return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
}

function distribute(count: number, top: number, bottom: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [(top + bottom) / 2];
  const gap = (bottom - top) / (count - 1);
  return Array.from({ length: count }, (_, index) => top + gap * index);
}

function formatAmount(value: number): string {
  return `${(Math.abs(value) / 1e8).toFixed(2)}亿`;
}

function formatSignedAmount(value: number): string {
  const sign = value > 0 ? '+' : value < 0 ? '-' : '';
  return `${sign}${formatAmount(value)}`;
}

function truncateCanvasText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let output = '';
  for (const char of text) {
    if (ctx.measureText(`${output}${char}...`).width > maxWidth) break;
    output += char;
  }
  return `${output}...`;
}

function formatHistoryDate(value: string): string {
  return value.length >= 10 ? value.slice(5, 10) : value;
}

function historyTimelinePoints(dates: string[]): Array<{ position: number; label: string }> {
  if (dates.length === 0) return [];
  const pointCount = Math.min(6, dates.length);
  if (pointCount === 1) return [{ position: 1, label: formatHistoryDate(dates[0]) }];
  return Array.from({ length: pointCount }, (_, index) => {
    const dateIndex = Math.round(index * (dates.length - 1) / (pointCount - 1));
    return {
      position: dateIndex / (dates.length - 1),
      label: formatHistoryDate(dates[dateIndex]),
    };
  });
}

function nodeKey(type: FlowNetworkNodeType, code: string): string {
  return `${type}:${code}`;
}

function networkColor(type: FlowNetworkNodeType): string {
  if (type === 'outflow') return OUTFLOW_COLOR;
  if (type === 'inflow') return INFLOW_COLOR;
  if (type === 'new_money') return GOLD;
  return MARKET_EXIT;
}

function rowAmount(row: ObserverRow): number {
  return Math.abs(numberOr(row.main_net_inflow));
}

function fallbackTransfers(data: ObserverFlowData): ObserverTransfer[] {
  const inflows = (data.inflows || []).filter((row) => rowAmount(row) > 0);
  const outflows = (data.outflows || []).filter((row) => rowAmount(row) > 0);
  const inflowTotal = inflows.reduce((total, row) => total + rowAmount(row), 0);
  const outflowTotal = outflows.reduce((total, row) => total + rowAmount(row), 0);
  const paired = Math.min(inflowTotal, outflowTotal);
  const transfers: ObserverTransfer[] = [];
  const inferred = (source: ObserverNetworkNode, target: ObserverNetworkNode, amount: number): void => {
    if (amount <= 0) return;
    transfers.push({ source, target, amount, inferred: true, basis: '展示范围内净流量平衡分配' });
  };

  if (paired > 0 && inflowTotal > 0 && outflowTotal > 0) {
    outflows.forEach((outflow) => inflows.forEach((inflow) => {
      inferred(
        { type: 'outflow', code: outflow.code, name: outflow.name },
        { type: 'inflow', code: inflow.code, name: inflow.name },
        paired * rowAmount(outflow) / outflowTotal * rowAmount(inflow) / inflowTotal,
      );
    }));
  }
  if (inflowTotal > paired) {
    inflows.forEach((inflow) => inferred(
      { type: 'new_money', code: '__NEW_MONEY__', name: '新资金进场' },
      { type: 'inflow', code: inflow.code, name: inflow.name },
      (inflowTotal - paired) * rowAmount(inflow) / inflowTotal,
    ));
  }
  if (outflowTotal > paired) {
    outflows.forEach((outflow) => inferred(
      { type: 'outflow', code: outflow.code, name: outflow.name },
      { type: 'market_exit', code: '__MARKET_EXIT__', name: '市场离场' },
      (outflowTotal - paired) * rowAmount(outflow) / outflowTotal,
    ));
  }
  return transfers;
}

function getTransfers(data: ObserverFlowData): ObserverTransfer[] {
  return data.transfers && data.transfers.length > 0 ? data.transfers : fallbackTransfers(data);
}

function blendRows(
  before: ObserverRow[] | undefined,
  after: ObserverRow[] | undefined,
  progress: number,
): ObserverRow[] {
  const previous = new Map((before || []).map((row) => [row.code, row]));
  const target = new Map((after || []).map((row) => [row.code, row]));
  const codes = [...target.keys(), ...[...previous.keys()].filter((code) => !target.has(code))];
  return codes
    .map((code) => {
      const source = previous.get(code);
      const destination = target.get(code);
      const stable = destination || source;
      if (!stable) return null;
      const from = source || { ...stable, main_net_inflow: 0, change_pct: 0 };
      const to = destination || { ...stable, main_net_inflow: 0, change_pct: 0 };
      return {
        ...stable,
        close_price: lerp(numberOr(from.close_price), numberOr(to.close_price), progress),
        change_pct: lerp(numberOr(from.change_pct), numberOr(to.change_pct), progress),
        main_net_inflow: lerp(numberOr(from.main_net_inflow), numberOr(to.main_net_inflow), progress),
        main_net_inflow_pct: lerp(numberOr(from.main_net_inflow_pct), numberOr(to.main_net_inflow_pct), progress),
        super_large_net_inflow: lerp(numberOr(from.super_large_net_inflow), numberOr(to.super_large_net_inflow), progress),
        large_net_inflow: lerp(numberOr(from.large_net_inflow), numberOr(to.large_net_inflow), progress),
        medium_net_inflow: lerp(numberOr(from.medium_net_inflow), numberOr(to.medium_net_inflow), progress),
        up_count: Math.round(lerp(numberOr(from.up_count), numberOr(to.up_count), progress)),
        down_count: Math.round(lerp(numberOr(from.down_count), numberOr(to.down_count), progress)),
        fading: !destination,
      } as ObserverRow & { fading?: boolean };
    })
    .filter((row): row is ObserverRow & { fading?: boolean } => row !== null)
    .filter((row) => progress < 0.98 || !row.fading)
    .sort((left, right) => Math.abs(right.main_net_inflow) - Math.abs(left.main_net_inflow));
}

function blendTransfers(
  before: ObserverFlowData | null,
  after: ObserverFlowData | null,
  progress: number,
): ObserverTransfer[] {
  const previous = before ? getTransfers(before) : [];
  const target = after ? getTransfers(after) : [];
  const byKey = (link: ObserverTransfer): string => `${link.source.type}:${link.source.code}->${link.target.type}:${link.target.code}`;
  const previousMap = new Map(previous.map((link) => [byKey(link), link]));
  const targetMap = new Map(target.map((link) => [byKey(link), link]));
  const keys = [...targetMap.keys(), ...[...previousMap.keys()].filter((key) => !targetMap.has(key))];
  return keys
    .map((key) => {
      const source = previousMap.get(key);
      const destination = targetMap.get(key);
      const stable = destination || source;
      if (!stable) return null;
      return {
        link: {
          ...stable,
          amount: lerp(numberOr(source?.amount), numberOr(destination?.amount), progress),
        },
        fading: !destination,
      };
    })
    .filter((item): item is { link: ObserverTransfer; fading: boolean } => item !== null)
    .filter((item) => progress < 0.98 || !item.fading)
    .map((item) => item.link);
}

function blendData(
  before: ObserverFlowData | null,
  after: ObserverFlowData | null,
  progress: number,
): ObserverFlowData | null {
  if (!after) return before;
  if (!before) return after;
  return {
    ...after,
    inflows: blendRows(before.inflows, after.inflows, progress),
    outflows: blendRows(before.outflows, after.outflows, progress),
    transfers: blendTransfers(before, after, progress),
    market: {
      ...after.market,
      sh_amount: lerp(numberOr(before.market?.sh_amount), numberOr(after.market?.sh_amount), progress),
      sh_index: lerp(numberOr(before.market?.sh_index), numberOr(after.market?.sh_index), progress),
      sh_change_pct: lerp(numberOr(before.market?.sh_change_pct), numberOr(after.market?.sh_change_pct), progress),
    },
    summary: {
      ...after.summary,
      inflow_total: lerp(numberOr(before.summary?.inflow_total), numberOr(after.summary?.inflow_total), progress),
      outflow_total: lerp(numberOr(before.summary?.outflow_total), numberOr(after.summary?.outflow_total), progress),
      shown_net_flow: lerp(numberOr(before.summary?.shown_net_flow), numberOr(after.summary?.shown_net_flow), progress),
      inflow_count: Math.round(lerp(numberOr(before.summary?.inflow_count), numberOr(after.summary?.inflow_count), progress)),
      outflow_count: Math.round(lerp(numberOr(before.summary?.outflow_count), numberOr(after.summary?.outflow_count), progress)),
    },
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

function buildScene(width: number, height: number, data: ObserverFlowData): SceneState {
  const outflowRows = (data.outflows || []).slice(0, 12);
  const inflowRows = (data.inflows || []).slice(0, 12);
  const visibleOutflowTotal = outflowRows.reduce((total, row) => total + rowAmount(row), 0);
  const visibleInflowTotal = inflowRows.reduce((total, row) => total + rowAmount(row), 0);
  const paired = numberOr(data.flow_inference?.paired_amount, Math.min(visibleOutflowTotal, visibleInflowTotal));
  const newMoney = Math.max(0, numberOr(data.flow_inference?.inflow_total, visibleInflowTotal) - paired);
  const marketExit = Math.max(0, numberOr(data.flow_inference?.outflow_total, visibleOutflowTotal) - paired);
  const leftItems: Array<{ type: FlowNetworkNodeType; code: string; name: string; value: number }> = outflowRows.map((row) => ({
    type: 'outflow', code: row.code, name: row.name || row.code, value: rowAmount(row),
  }));
  const rightItems: Array<{ type: FlowNetworkNodeType; code: string; name: string; value: number }> = inflowRows.map((row) => ({
    type: 'inflow', code: row.code, name: row.name || row.code, value: rowAmount(row),
  }));
  if (newMoney > 0) leftItems.push({ type: 'new_money', code: '__NEW_MONEY__', name: '新资金进场', value: newMoney });
  if (marketExit > 0) rightItems.push({ type: 'market_exit', code: '__MARKET_EXIT__', name: '市场离场', value: marketExit });

  const leftX = width * 0.30;
  const barWidth = clamp(width * 0.024, 10, 24);
  const rightX = width * 0.70 - barWidth;
  const top = Math.min(height * 0.34, 252);
  const bottom = Math.max(top + 100, height - 100);
  const allValues = [...leftItems, ...rightItems].map((item) => item.value);
  const maxValue = Math.max(1, ...allValues);
  const maxCount = Math.max(leftItems.length, rightItems.length);
  const slot = maxCount > 1 ? (bottom - top) / (maxCount - 1) : bottom - top;
  const maxNodeHeight = clamp(slot * 0.72, 22, 58);
  const nodes: FlowVisualNode[] = [];
  const makeSide = (items: typeof leftItems, side: FlowSide, x: number): void => {
    const positions = distribute(items.length, top, bottom);
    items.forEach((item, index) => {
      const intensity = clamp(Math.sqrt(item.value / maxValue), 0.22, 1);
      nodes.push({
        key: nodeKey(item.type, item.code),
        type: item.type,
        name: item.name,
        code: item.code,
        value: item.value,
        side,
        x,
        y: positions[index],
        width: barWidth,
        height: clamp(14 + intensity * maxNodeHeight, 18, maxNodeHeight),
        color: networkColor(item.type),
      });
    });
  };
  makeSide(leftItems, 'left', leftX);
  makeSide(rightItems, 'right', rightX);

  const nodeMap = new Map(nodes.map((node) => [node.key, node]));
  const usedSource = new Map<string, number>();
  const usedTarget = new Map<string, number>();
  const links = getTransfers(data)
    .filter((link) => link.amount > 0)
    .sort((left, right) => right.amount - left.amount);
  const maxAmount = Math.max(1, ...links.map((link) => link.amount));
  const maxRibbonWidth = width < 500 ? 2 : 2.8;
  const ribbons: FlowRibbon[] = [];

  links.forEach((link, index) => {
    const source = nodeMap.get(nodeKey(link.source.type, link.source.code));
    const target = nodeMap.get(nodeKey(link.target.type, link.target.code));
    if (!source || !target) return;
    const sourceUsed = usedSource.get(source.key) || 0;
    const targetUsed = usedTarget.get(target.key) || 0;
    const sourceRatio = clamp((sourceUsed + link.amount / 2) / Math.max(source.value, 1), 0, 1);
    const targetRatio = clamp((targetUsed + link.amount / 2) / Math.max(target.value, 1), 0, 1);
    const startY = source.y - source.height / 2 + sourceRatio * source.height;
    const endY = target.y - target.height / 2 + targetRatio * target.height;
    usedSource.set(source.key, sourceUsed + link.amount);
    usedTarget.set(target.key, targetUsed + link.amount);
    const bend = (seededUnit(hashString(`${source.key}-${target.key}`)) - 0.5) * height * 0.13;
    const distance = target.x - (source.x + source.width);
    ribbons.push({
      source,
      target,
      amount: link.amount,
      width: clamp(
        0.2 + Math.sqrt(link.amount / maxAmount) * maxRibbonWidth,
        0.4,
        Math.min(source.height * 0.24, target.height * 0.24, maxRibbonWidth),
      ),
      startX: source.x + source.width,
      startY,
      ctrl1X: source.x + source.width + distance * 0.25,
      ctrl1Y: lerp(startY, endY, 0.18) + bend,
      ctrl2X: target.x - distance * 0.25,
      ctrl2Y: lerp(startY, endY, 0.82) + bend,
      endX: target.x,
      endY,
      startColor: networkColor(link.source.type),
      endColor: networkColor(link.target.type),
      index,
    });
  });

  return { nodes, ribbons, centerX: width * 0.5 };
}

function traceRibbonCurve(
  ctx: CanvasRenderingContext2D,
  ribbon: FlowRibbon,
  offset: number,
): void {
  ctx.bezierCurveTo(
    ribbon.ctrl1X,
    ribbon.ctrl1Y - offset,
    ribbon.ctrl2X,
    ribbon.ctrl2Y - offset,
    ribbon.endX,
    ribbon.endY - offset,
  );
}

function drawRibbon(ctx: CanvasRenderingContext2D, ribbon: FlowRibbon): void {
  const half = ribbon.width / 2;
  const gradient = ctx.createLinearGradient(ribbon.startX, 0, ribbon.endX, 0);
  gradient.addColorStop(0, ribbon.startColor);
  gradient.addColorStop(0.48, ribbon.startColor);
  gradient.addColorStop(1, ribbon.endColor);
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(ribbon.startX, ribbon.startY - half);
  traceRibbonCurve(ctx, ribbon, half);
  ctx.lineTo(ribbon.endX, ribbon.endY + half);
  ctx.bezierCurveTo(
    ribbon.ctrl2X,
    ribbon.ctrl2Y + half,
    ribbon.ctrl1X,
    ribbon.ctrl1Y + half,
    ribbon.startX,
    ribbon.startY + half,
  );
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.globalAlpha = 0.16;
  ctx.shadowColor = ribbon.startColor;
  ctx.shadowBlur = Math.min(4, 1 + ribbon.width * 0.7);
  ctx.fill();
  ctx.globalAlpha = 0.34;
  ctx.lineWidth = 0.35;
  ctx.strokeStyle = gradient;
  ctx.stroke();
  ctx.restore();
}

function ribbonKey(ribbon: FlowRibbon): string {
  return `${ribbon.source.key}->${ribbon.target.key}`;
}

function drawRibbonHighlight(ctx: CanvasRenderingContext2D, ribbon: FlowRibbon): void {
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(ribbon.startX, ribbon.startY);
  ctx.bezierCurveTo(
    ribbon.ctrl1X,
    ribbon.ctrl1Y,
    ribbon.ctrl2X,
    ribbon.ctrl2Y,
    ribbon.endX,
    ribbon.endY,
  );
  ctx.strokeStyle = '#FFFFFF';
  ctx.lineWidth = Math.max(1.4, ribbon.width + 0.8);
  ctx.globalAlpha = 0.72;
  ctx.shadowColor = '#FFFFFF';
  ctx.shadowBlur = 7;
  ctx.stroke();
  ctx.restore();
}

function findRibbonAtPoint(scene: SceneState, x: number, y: number): FlowRibbon | null {
  let nearest: FlowRibbon | null = null;
  let nearestDistance = Number.POSITIVE_INFINITY;
  for (const ribbon of scene.ribbons) {
    const samples = 30;
    for (let index = 0; index <= samples; index += 1) {
      const point = cubicPoint(ribbon, index / samples);
      const distance = Math.hypot(point.x - x, point.y - y);
      if (distance < nearestDistance) {
        nearest = ribbon;
        nearestDistance = distance;
      }
    }
  }
  if (!nearest) return null;
  return nearestDistance <= Math.max(10, nearest.width * 1.55) ? nearest : null;
}

function cubicPoint(ribbon: FlowRibbon, progress: number): { x: number; y: number } {
  const t = clamp(progress, 0, 1);
  const u = 1 - t;
  const tt = t * t;
  const uu = u * u;
  return {
    x: u * uu * ribbon.startX
      + 3 * uu * t * ribbon.ctrl1X
      + 3 * u * tt * ribbon.ctrl2X
      + tt * t * ribbon.endX,
    y: u * uu * ribbon.startY
      + 3 * uu * t * ribbon.ctrl1Y
      + 3 * u * tt * ribbon.ctrl2Y
      + tt * t * ribbon.endY,
  };
}

function drawRibbonParticles(ctx: CanvasRenderingContext2D, ribbon: FlowRibbon, tick: number): void {
  const seed = hashString(`${ribbon.source.key}-${ribbon.target.key}`);
  const intensity = clamp(ribbon.width / 2.8, 0.12, 1);
  const particleCount = clamp(Math.round(1 + intensity * 4), 1, 5);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let index = 0; index < particleCount; index += 1) {
    const phase = seededUnit(seed + index * 67) + tick * (0.10 + intensity * 0.12);
    const progress = phase - Math.floor(phase);
    const point = cubicPoint(ribbon, progress);
    const radius = clamp(0.65 + ribbon.width * 0.08 + seededUnit(seed + index * 17) * 0.65, 0.7, 1.8);
    ctx.fillStyle = progress < 0.52 ? ribbon.startColor : ribbon.endColor;
    ctx.globalAlpha = 0.52;
    ctx.shadowColor = ctx.fillStyle;
    ctx.shadowBlur = 9;
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function drawNode(ctx: CanvasRenderingContext2D, node: FlowVisualNode, width: number, tick: number): void {
  const pulse = 1 + Math.sin(tick * 1.8 + seededUnit(hashString(node.key)) * Math.PI * 2) * 0.04;
  const barHeight = node.height * pulse;
  const barY = node.y - barHeight / 2;
  ctx.save();
  const glow = ctx.createRadialGradient(node.x + node.width / 2, node.y, 2, node.x + node.width / 2, node.y, node.height * 1.8);
  glow.addColorStop(0, `${node.color}88`);
  glow.addColorStop(0.42, `${node.color}22`);
  glow.addColorStop(1, `${node.color}00`);
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(node.x + node.width / 2, node.y, node.height * 1.65, 0, Math.PI * 2);
  ctx.fill();

  const gradient = ctx.createLinearGradient(node.x, 0, node.x + node.width, 0);
  gradient.addColorStop(0, `${node.color}55`);
  gradient.addColorStop(0.45, node.color);
  gradient.addColorStop(1, `${node.color}AA`);
  ctx.fillStyle = gradient;
  ctx.shadowColor = node.color;
  ctx.shadowBlur = 12;
  drawRoundedRect(ctx, node.x, barY, node.width, barHeight, 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = `${node.color}DD`;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();

  const left = node.side === 'left';
  const labelX = left ? node.x - 11 : node.x + node.width + 11;
  const labelWidth = left ? Math.max(58, node.x - 30) : Math.max(58, width - node.x - node.width - 30);
  const labelColor = node.type === 'new_money' ? GOLD : node.type === 'market_exit' ? MARKET_EXIT : '#FFFFFF';
  ctx.save();
  ctx.textAlign = left ? 'right' : 'left';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = labelColor;
  ctx.font = `${width < 500 ? 11 : 13}px PingFang SC, Microsoft YaHei, sans-serif`;
  ctx.fillText(truncateCanvasText(ctx, node.name || node.code, labelWidth), labelX, node.y - 9);
  ctx.fillStyle = node.color;
  ctx.font = `600 ${width < 500 ? 11 : 14}px JetBrains Mono, SFMono-Regular, monospace`;
  ctx.fillText(formatAmount(node.value), labelX, node.y + 10);
  ctx.restore();
}

function drawCrown(ctx: CanvasRenderingContext2D, x: number, y: number): void {
  ctx.save();
  ctx.fillStyle = GOLD;
  ctx.shadowColor = GOLD;
  ctx.shadowBlur = 9;
  ctx.beginPath();
  ctx.moveTo(x - 12, y + 8);
  ctx.lineTo(x - 10, y - 4);
  ctx.lineTo(x - 4, y + 1);
  ctx.lineTo(x, y - 8);
  ctx.lineTo(x + 5, y + 1);
  ctx.lineTo(x + 11, y - 4);
  ctx.lineTo(x + 13, y + 8);
  ctx.closePath();
  ctx.fill();
  ctx.fillRect(x - 12, y + 8, 25, 3);
  ctx.restore();
}

function drawTimeline(
  ctx: CanvasRenderingContext2D,
  width: number,
  progress: number,
  mode: FlowObserverMode,
  historyDates: string[],
): void {
  const liveLabels = width < 500
    ? ['09:30', '10:30', '11:30', '13:30', '15:00']
    : ['09:30', '10:00', '10:30', '11:00', '11:30', '13:00', '14:00', '15:00'];
  const livePoints = liveLabels.map((label, index) => ({
    label,
    position: index / Math.max(1, liveLabels.length - 1),
  }));
  const points = mode === 'history' && historyTimelinePoints(historyDates).length > 0
    ? historyTimelinePoints(historyDates)
    : livePoints;
  const left = 24;
  const right = width - 24;
  const y = 68;
  const activeX = lerp(left, right, clamp(progress, 0, 1));
  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineWidth = width < 500 ? 1.8 : 2.2;
  const gradient = ctx.createLinearGradient(left, y, right, y);
  gradient.addColorStop(0, OUTFLOW_COLOR);
  gradient.addColorStop(0.48, GOLD);
  gradient.addColorStop(1, INFLOW_COLOR);
  ctx.strokeStyle = gradient;
  ctx.shadowColor = OUTFLOW_COLOR;
  ctx.shadowBlur = 8;
  ctx.beginPath();
  ctx.moveTo(left, y);
  ctx.lineTo(right, y);
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = 'rgba(255,255,255,0.8)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(activeX, y - 9);
  ctx.lineTo(activeX, y + 9);
  ctx.stroke();
  ctx.fillStyle = '#FFFFFF';
  ctx.beginPath();
  ctx.arc(activeX, y, 4.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = `${width < 500 ? 10 : 12}px JetBrains Mono, SFMono-Regular, monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  points.forEach(({ label, position }) => {
    const x = lerp(left, right, position);
    ctx.fillStyle = position <= progress + 0.001 ? '#FFFFFF' : MUTED;
    ctx.fillText(label, x, y - 14);
  });
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillStyle = mode === 'live' ? OUTFLOW_COLOR : MUTED;
  ctx.font = '11px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.fillText(mode === 'live' ? '盘中实时进度' : '历史日级回放', left, y + 17);
  ctx.restore();
}

function shanghaiSessionProgress(): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date());
  const hour = Number(parts.find((part) => part.type === 'hour')?.value || 0);
  const minute = Number(parts.find((part) => part.type === 'minute')?.value || 0);
  const now = hour * 60 + minute;
  const morningStart = 9 * 60 + 30;
  const morningEnd = 11 * 60 + 30;
  const afternoonStart = 13 * 60;
  const afternoonEnd = 15 * 60;
  if (now <= morningStart) return 0;
  if (now < morningEnd) return ((now - morningStart) / (morningEnd - morningStart)) * 0.48;
  if (now < afternoonStart) return 0.5;
  if (now < afternoonEnd) return 0.52 + ((now - afternoonStart) / (afternoonEnd - afternoonStart)) * 0.48;
  return 1;
}

function renderCanvas(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  data: ObserverFlowData | null,
  mode: FlowObserverMode,
  playbackProgress: number,
  historyDates: string[],
  timestamp: number,
  hoveredKey: string | null,
): SceneState | null {
  const tick = timestamp / 1000;
  ctx.fillStyle = '#020303';
  ctx.fillRect(0, 0, width, height);

  const background = ctx.createRadialGradient(width * 0.5, height * 0.56, 24, width * 0.5, height * 0.56, Math.max(width, height) * 0.72);
  background.addColorStop(0, '#0B1712');
  background.addColorStop(0.46, '#07100D');
  background.addColorStop(1, '#020303');
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);

  if (!data) {
    ctx.fillStyle = '#FFFFFF';
    ctx.font = '600 16px PingFang SC, Microsoft YaHei, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('正在等待资金流数据', width / 2, height / 2);
    return null;
  }

  const progress = mode === 'live' ? shanghaiSessionProgress() : clamp(playbackProgress, 0, 1);
  const scene = buildScene(width, height, data);
  const sourceLabel = data.source === 'cache' ? '本地缓存' : data.source === 'eastmoney' ? '东方财富' : data.source;

  drawTimeline(ctx, width, progress, mode, historyDates);
  ctx.save();
  ctx.strokeStyle = 'rgba(255,255,255,0.12)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, 112);
  ctx.lineTo(width, 112);
  ctx.stroke();
  ctx.fillStyle = '#FFFFFF';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.font = `600 ${width < 500 ? 14 : 17}px PingFang SC, Microsoft YaHei, sans-serif`;
  ctx.fillText(`${data.board_label || '板块'}资金迁移`, 24, 124);
  ctx.fillStyle = MUTED;
  ctx.font = '11px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.fillText(`${sourceLabel} · ${mode === 'live' ? '实时快照' : '日级缓存'}`, 24, 148);
  ctx.textAlign = 'right';
  ctx.fillStyle = '#FFFFFF';
  ctx.font = '600 12px JetBrains Mono, SFMono-Regular, monospace';
  ctx.fillText(data.data_date || '--', width - 24, 126);
  ctx.fillStyle = MUTED;
  ctx.font = '11px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.fillText(mode === 'live' ? '每15秒刷新' : '历史回放', width - 24, 148);
  ctx.restore();

  const leftHeadingX = width * 0.30 + 8;
  const rightHeadingX = width * 0.70 - 8;
  drawCrown(ctx, leftHeadingX, 178);
  drawCrown(ctx, rightHeadingX, 178);
  ctx.save();
  ctx.font = `600 ${width < 500 ? 13 : 15}px PingFang SC, Microsoft YaHei, sans-serif`;
  ctx.textBaseline = 'middle';
  ctx.fillStyle = OUTFLOW_COLOR;
  ctx.textAlign = 'center';
  ctx.fillText('流出最多', leftHeadingX, 199);
  ctx.fillStyle = INFLOW_COLOR;
  ctx.fillText('流入最多', rightHeadingX, 199);
  if (width >= 500) {
    ctx.textAlign = 'center';
    ctx.fillStyle = GOLD;
    ctx.font = '10px PingFang SC, Microsoft YaHei, sans-serif';
    ctx.fillText('连线为展示范围内净流量推断', width / 2, 199);
  }
  ctx.restore();

  scene.ribbons.forEach((ribbon) => drawRibbon(ctx, ribbon));
  scene.ribbons.forEach((ribbon) => drawRibbonParticles(ctx, ribbon, tick));
  const hoveredRibbon = scene.ribbons.find((ribbon) => ribbonKey(ribbon) === hoveredKey);
  if (hoveredRibbon) drawRibbonHighlight(ctx, hoveredRibbon);
  scene.nodes.forEach((node) => drawNode(ctx, node, width, tick));

  ctx.save();
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.setLineDash([2, 8]);
  ctx.beginPath();
  ctx.moveTo(scene.centerX, 218);
  ctx.lineTo(scene.centerX, height - 100);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';
  ctx.fillStyle = MUTED;
  ctx.font = '11px PingFang SC, Microsoft YaHei, sans-serif';
  ctx.fillText(`展示净额 ${formatSignedAmount(numberOr(data.summary?.shown_net_flow))}`, 24, height - 37);
  ctx.textAlign = 'right';
  ctx.fillText(data.flow_inference?.confidence === 'low' ? '推断置信度：低 · 不代表逐笔资金路径' : '板块净流量网络', width - 24, height - 37);
  ctx.restore();
  return scene;
}

export default function FlowObserverCanvas({
  data,
  mode,
  playbackProgress = 1,
  historyDates = [],
}: FlowObserverCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const sceneRef = useRef<SceneState | null>(null);
  const animationRef = useRef<number | null>(null);
  const transitionRef = useRef<TransitionState>({ from: null, to: data, startedAt: performance.now() });
  const hoveredRef = useRef<HoveredFlow | null>(null);
  const [hovered, setHovered] = useState<HoveredFlow | null>(null);

  useEffect(() => {
    const now = performance.now();
    const current = blendData(
      transitionRef.current.from,
      transitionRef.current.to,
      easeInOutCubic(clamp((now - transitionRef.current.startedAt) / TRANSITION_MS, 0, 1)),
    );
    transitionRef.current = { from: current, to: data, startedAt: now };
  }, [data]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return undefined;
    const resize = () => {
      const rect = wrap.getBoundingClientRect();
      const width = Math.max(1, Math.floor(rect.width));
      const height = Math.max(720, Math.floor(rect.height));
      const dpr = window.devicePixelRatio || 1;
      sizeRef.current = { width, height };
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = '100%';
      canvas.style.height = `${height}px`;
      canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return undefined;
    const frame = (timestamp: number) => {
      const { width, height } = sizeRef.current;
      const transition = transitionRef.current;
      const progress = easeInOutCubic(clamp((timestamp - transition.startedAt) / TRANSITION_MS, 0, 1));
      const current = blendData(transition.from, transition.to, progress);
      if (width > 0 && height > 0) {
        sceneRef.current = renderCanvas(
          ctx,
          width,
          height,
          current,
          mode,
          playbackProgress,
          historyDates,
          timestamp,
          hoveredRef.current?.key || null,
        );
      }
      animationRef.current = window.requestAnimationFrame(frame);
    };
    animationRef.current = window.requestAnimationFrame(frame);
    return () => {
      if (animationRef.current) window.cancelAnimationFrame(animationRef.current);
    };
  }, [historyDates, mode, playbackProgress]);

  const handlePointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const scene = sceneRef.current;
    if (!canvas || !scene) return;
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * sizeRef.current.width / Math.max(1, rect.width);
    const y = (event.clientY - rect.top) * sizeRef.current.height / Math.max(1, rect.height);
    const ribbon = findRibbonAtPoint(scene, x, y);
    const next = ribbon
      ? {
        key: ribbonKey(ribbon),
        sourceName: ribbon.source.name,
        targetName: ribbon.target.name,
        amount: ribbon.amount,
        x: clamp(x, 120, Math.max(120, sizeRef.current.width - 120)),
        y: clamp(y - 16, 54, Math.max(54, sizeRef.current.height - 72)),
      }
      : null;
    if (next?.key === hoveredRef.current?.key) return;
    hoveredRef.current = next;
    setHovered(next);
  };

  const handlePointerLeave = () => {
    hoveredRef.current = null;
    setHovered(null);
  };

  return (
    <div ref={wrapRef} className="relative h-full min-h-[720px] min-w-0 w-full overflow-hidden bg-[#020303]">
      <canvas
        ref={canvasRef}
        className="block h-full w-full cursor-crosshair"
        role="img"
        aria-label="A股板块资金迁移动态观察图"
        onPointerMove={handlePointerMove}
        onPointerLeave={handlePointerLeave}
      />
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full border border-white/20 bg-[#050807ee] px-3 py-2 text-center shadow-xl"
          style={{ left: `${hovered.x}px`, top: `${hovered.y}px` }}
        >
          <div className="text-xs text-white">{hovered.sourceName} <span className="mx-1 text-[#FFD43B]">-&gt;</span> {hovered.targetName}</div>
          <div className="mt-1 font-mono text-sm font-semibold text-[#FFD43B]">{formatAmount(hovered.amount)}</div>
        </div>
      )}
      {!data && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="border border-[#00FF7F44] bg-[#020303dd] px-3 py-2 text-sm text-[#8E8E93]">正在等待资金流数据</span>
        </div>
      )}
    </div>
  );
}
