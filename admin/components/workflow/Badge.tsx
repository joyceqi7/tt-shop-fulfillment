// tt shop 主题的 accent 是中性灰, 直接用 sky/amber/emerald/rose 的固定色调
// 让 badge 在暗色面板里清晰可读。
const INFO_BADGE = "bg-sky-500/20 text-sky-300 border border-sky-500/30";
const INFO_BADGE_PULSE = `${INFO_BADGE} animate-pulse`;
const OK_BADGE = "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
const WARN_BADGE = "bg-amber-500/20 text-amber-300 border border-amber-500/30";
const ERR_BADGE = "bg-rose-500/20 text-rose-300 border border-rose-500/30";
const NEUTRAL_BADGE = "bg-bg-hover text-text-muted border border-border/60";

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: NEUTRAL_BADGE,
    running: INFO_BADGE_PULSE,
    processing: INFO_BADGE_PULSE,
    scripting: INFO_BADGE_PULSE,
    rendering: INFO_BADGE_PULSE,
    awaiting_review: WARN_BADGE,
    done: OK_BADGE,
    failed: ERR_BADGE,
    pending: NEUTRAL_BADGE,
  };
  const label: Record<string, string> = {
    awaiting_review: "待审核",
    scripting: "写脚本",
    rendering: "渲染中",
    processing: "处理中",
    running: "运行中",
    queued: "排队中",
    done: "已完成",
    failed: "失败",
    pending: "等待中",
  };
  return (
    <span className={`badge ${map[status] || NEUTRAL_BADGE}`}>
      {label[status] || status}
    </span>
  );
}

export function TypeBadge({ type }: { type?: string }) {
  if (!type) return null;
  const map: Record<string, string> = {
    hook: INFO_BADGE,
    problem: WARN_BADGE,
    product: OK_BADGE,
    proof: "bg-indigo-500/20 text-indigo-300 border border-indigo-500/30",
    cta: "bg-pink-500/20 text-pink-300 border border-pink-500/30",
    broll: "bg-bg-hover text-text-muted border border-border/60",
    filler: "bg-bg-hover text-text-dim border border-border/60",
  };
  const labels: Record<string, string> = {
    hook: "开场",
    problem: "痛点",
    product: "产品",
    proof: "效果",
    cta: "召唤",
    broll: "辅助",
    filler: "过渡",
  };
  return (
    <span className={`badge ${map[type] || NEUTRAL_BADGE}`}>
      {labels[type] || type}
    </span>
  );
}
