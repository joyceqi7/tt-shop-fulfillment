"use client";

import { useMemo } from "react";

import { formatCents } from "@/lib/utils";
import type { MonthlyPoint } from "@/lib/types";

interface Props {
  points: MonthlyPoint[];
}

/**
 * 双系列柱图：左轴 GMV（success），右轴 cost（warning）。
 * 简单 SVG 实现，柱子用 hsl(var(--success/warning))。
 */
export function MonthlyChart({ points }: Props) {
  const computed = useMemo(() => {
    if (points.length === 0)
      return { bars: [] as Array<{ x: number; gmv: number; cost: number; month: string }>, max: 1 };
    const max = Math.max(
      ...points.map((p) => Math.max(p.gmv, p.cost)),
      1,
    );
    const w = 800;
    const h = 220;
    const padX = 40;
    const padY = 24;
    const innerW = w - padX * 2;
    const innerH = h - padY * 2;
    const slot = innerW / Math.max(1, points.length);
    return {
      bars: points.map((p, i) => ({
        x: padX + slot * i + slot * 0.15,
        gmv: p.gmv,
        cost: p.cost,
        month: p.month,
        slotW: slot * 0.7,
        innerH,
        padY,
        max,
      })),
      max,
      w,
      h,
    };
  }, [points]);

  if (points.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">
        暂无月度数据
      </div>
    );
  }

  const w = (computed as any).w;
  const h = (computed as any).h;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2 rounded-sm bg-success" />
          GMV
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block size-2 rounded-sm bg-warning" />
          采购成本
        </span>
      </div>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        className="h-56 w-full"
        role="img"
        aria-label="月度 GMV / 成本"
      >
        {computed.bars.map((b: any) => {
          const gmvH = (b.gmv / b.max) * b.innerH;
          const costH = (b.cost / b.max) * b.innerH;
          const baseY = b.padY + b.innerH;
          const halfW = b.slotW / 2;
          return (
            <g key={b.month}>
              <rect
                x={b.x}
                y={baseY - gmvH}
                width={halfW - 1}
                height={gmvH}
                fill="hsl(var(--success))"
                opacity={0.85}
              />
              <rect
                x={b.x + halfW + 1}
                y={baseY - costH}
                width={halfW - 1}
                height={costH}
                fill="hsl(var(--warning))"
                opacity={0.85}
              />
              <text
                x={b.x + b.slotW / 2}
                y={baseY + 14}
                textAnchor="middle"
                className="fill-muted-foreground"
                fontSize="10"
              >
                {b.month.slice(2)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="grid grid-cols-2 gap-2 text-[11px] tabular-nums sm:grid-cols-6">
        {points.map((p) => (
          <div
            key={p.month}
            className="rounded-sm border border-border px-2 py-1"
          >
            <p className="text-muted-foreground">{p.month}</p>
            <p className="text-success">+{formatCents(p.gmv)}</p>
            <p className="text-warning">−{formatCents(p.cost)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
