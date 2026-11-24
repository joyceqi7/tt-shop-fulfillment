"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher, type Job } from "@/lib/workflow-api";
import { formatDateTime } from "@/lib/utils";
import { StatusBadge } from "@/components/workflow/Badge";

// 后端任务 kind 是英文枚举, UI 上展示中文更易读。
const JOB_KIND_LABELS: Record<string, string> = {
  ingest: "素材处理",
  generate_script: "写脚本",
  render_video: "渲染视频",
  generate_cover: "生成封面",
  generate: "视频生成",
};

export default function JobsPage() {
  const [filterStatus, setFilterStatus] = useState("");
  const [filterKind, setFilterKind] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);

  const params = new URLSearchParams();
  if (filterStatus) params.set("status", filterStatus);
  if (filterKind) params.set("kind", filterKind);
  params.set("limit", "80");

  const { data: jobs } = useSWR<Job[]>(`/jobs?${params}`, fetcher, { refreshInterval: 2000 });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">任务监控</h1>
        <p className="text-sm text-text-muted mt-1">所有后台处理任务的状态、进度和日志。</p>
      </header>

      <section className="flex items-center gap-3">
        <select className="input max-w-[160px]" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
          <option value="">所有状态</option>
          <option value="queued">排队中</option>
          <option value="running">处理中</option>
          <option value="done">已完成</option>
          <option value="failed">失败</option>
        </select>
        <select className="input max-w-[160px]" value={filterKind} onChange={(e) => setFilterKind(e.target.value)}>
          <option value="">所有类型</option>
          <option value="ingest">素材处理</option>
          <option value="generate">视频生成</option>
        </select>
      </section>

      <section className="card divide-y divide-border">
        {(jobs ?? []).length === 0 && (
          <div className="p-4 text-sm text-text-dim">没有匹配的任务。</div>
        )}
        {(jobs ?? []).map((j) => {
          const open = openId === j.id;
          return (
            <div key={j.id}>
              <button
                onClick={() => setOpenId(open ? null : j.id)}
                className="w-full p-3 flex items-center gap-3 text-sm hover:bg-bg-hover transition-colors text-left"
              >
                <span className="font-mono text-text-dim w-12">#{j.id}</span>
                <span className="badge bg-bg-hover text-text-muted w-24 justify-center">
                  {JOB_KIND_LABELS[j.kind] || j.kind}
                </span>
                <StatusBadge status={j.status} />
                <span className="text-text-muted text-xs w-24 truncate">{j.stage || "-"}</span>
                <div className="flex-1 h-1.5 bg-bg rounded">
                  <div
                    className="h-full bg-accent rounded transition-all"
                    style={{ width: `${j.progress || 0}%` }}
                  />
                </div>
                <span className="font-mono text-xs text-text-dim w-12 text-right">
                  {Math.round(j.progress || 0)}%
                </span>
                <span className="text-text-dim text-[11px]">{open ? "▾" : "▸"}</span>
              </button>
              {open && (
                <div className="p-4 bg-bg-panel border-t border-border space-y-2">
                  <div className="text-xs text-text-muted">
                    关联对象 <span className="font-mono">#{j.target_id}</span>
                    {j.started_at && (
                      <> · 开始 <span className="font-mono">{formatDateTime(j.started_at)}</span></>
                    )}
                    {j.finished_at && (
                      <> · 结束 <span className="font-mono">{formatDateTime(j.finished_at)}</span></>
                    )}
                  </div>
                  {j.error && (
                    <div className="text-xs text-err whitespace-pre-wrap">{j.error}</div>
                  )}
                  <pre className="text-[11px] text-text-muted whitespace-pre-wrap max-h-72 overflow-auto scroll-thin font-mono">
                    {j.log || "（暂无日志）"}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </section>
    </div>
  );
}
