"use client";

import { useMemo } from "react";

import { formatCents } from "@/lib/utils";
import type { TrendPoint } from "@/lib/types";

interface TrendChartProps {
  points: TrendPoint[];
}

/**
 * 极简手写 SVG 折线图。
 * 不引 recharts / chart.js 是因为：
 *   1. 我们只画一条线，不需要复杂坐标系
 *   2. recharts SSR 会带 ~50KB JS，做财务模块时再引也不迟
 *   3. 直接画 SVG 更快、更可控（hover tooltip 留给后续迭代）
 */
export function TrendChart({ points }: TrendChartProps) {
  const { path, area, max, min, ticks } = useMemo(() => {
    if (points.length === 0) {
      return { path: "", area: "", max: 0, min: 0, ticks: [] as TrendPoint[] };
    }
    const values = points.map((p) => p.gmv);
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const range = Math.max(1, max - min);
    const w = 800;
    const h = 200;
    const padX = 30;
    const padY = 20;
    const innerW = w - padX * 2;
    const innerH = h - padY * 2;

    const xs = (i: number) =>
      padX + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
    const ys = (v: number) => padY + (1 - (v - min) / range) * innerH;

    const segs = points.map((p, i) => `${xs(i)},${ys(p.gmv)}`);
    const path = `M ${segs.join(" L ")}`;
    const area = `${path} L ${xs(points.length - 1)},${padY + innerH} L ${xs(0)},${padY + innerH} Z`;

    // 取首/中/末三个点作为 x 轴 tick
    const tickIdx = points.length <= 3 ? points.map((_, i) => i) : [0, Math.floor(points.length / 2), points.length - 1];
    const ticks = tickIdx.map((i) => points[i]);
    return { path, area, max, min, ticks };
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
        暂无数据
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <svg
        viewBox="0 0 800 200"
        preserveAspectRatio="none"
        className="h-48 w-full"
        role="img"
        aria-label="GMV 趋势"
      >
        <defs>
          <linearGradient id="gmvGradient" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--success))" stopOpacity="0.18" />
            <stop offset="100%" stopColor="hsl(var(--success))" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#gmvGradient)" />
        <path
          d={path}
          fill="none"
          stroke="hsl(var(--success))"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <div className="flex justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>{formatCents(min)}</span>
        <span>{formatCents(max)}</span>
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground tabular-nums">
        {ticks.map((t) => (
          <span key={t.date}>{t.date.slice(5)}</span>
        ))}
      </div>
    </div>
  );
}
