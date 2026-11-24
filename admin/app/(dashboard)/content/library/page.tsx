"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { fetcher, patch, type Clip, type Game } from "@/lib/workflow-api";
import { TypeBadge } from "@/components/workflow/Badge";

const TYPES = ["hook", "problem", "product", "proof", "cta", "broll", "filler"] as const;
// 后端模型枚举仍然是英文, 前端给一份中文标签映射, 不改 value 保持 API 兼容。
const TYPE_LABELS: Record<string, string> = {
  hook: "开场钩子",
  problem: "痛点",
  product: "产品展示",
  proof: "效果展示",
  cta: "行动召唤",
  broll: "辅助镜头",
  filler: "过渡片段",
};
type RealPersonFilter = "exclude" | "all" | "only";

export default function LibraryPage() {
  const [q, setQ] = useState("");
  const [contentType, setContentType] = useState("");
  const [game, setGame] = useState("");
  const [minHook, setMinHook] = useState(0);
  const [realPerson, setRealPerson] = useState<RealPersonFilter>("exclude");
  const [selected, setSelected] = useState<Clip | null>(null);

  const { data: games } = useSWR<Game[]>("/games", fetcher);

  const params = useMemo(() => {
    const sp = new URLSearchParams();
    if (q) sp.set("q", q);
    if (contentType) sp.set("content_type", contentType);
    if (game) sp.set("game", game);
    if (minHook > 0) sp.set("min_hook", String(minHook));
    sp.set("real_person", realPerson);
    sp.set("limit", "120");
    return sp.toString();
  }, [q, contentType, game, minHook, realPerson]);

  const { data: clips, mutate: mutateClips } = useSWR<Clip[]>(`/clips?${params}`, fetcher, {
    refreshInterval: 5000,
  });

  // 拉一份只看真人的数量, 用来在排除模式下提示有多少被过滤掉了
  const realOnlyParams = useMemo(() => {
    const sp = new URLSearchParams();
    if (game) sp.set("game", game);
    sp.set("real_person", "only");
    sp.set("limit", "200");
    return sp.toString();
  }, [game]);
  const { data: flaggedClips } = useSWR<Clip[]>(`/clips?${realOnlyParams}`, fetcher);
  const flaggedCount = flaggedClips?.length ?? 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">素材库</h1>
        <p className="text-sm text-text-muted mt-1">
          已切分并打标的全部镜头片段，点击任意片段查看详情和预览。
        </p>
      </header>

      {games && games.length > 0 && (
        <section className="flex flex-wrap gap-2 items-center">
          <span className="text-xs text-text-dim uppercase tracking-wider mr-1">游戏</span>
          <button
            onClick={() => setGame("")}
            className={
              "badge transition-colors " +
              (!game
                ? "bg-accent text-white"
                : "bg-bg-hover text-text-muted hover:text-text")
            }
          >
            全部
          </button>
          {games.map((g) => (
            <button
              key={g.name}
              onClick={() => setGame(g.name)}
              className={
                "badge transition-colors " +
                (game === g.name
                  ? "bg-accent text-white"
                  : "bg-bg-hover text-text-muted hover:text-text")
              }
            >
              🎮 {g.name}
              <span className="ml-1.5 opacity-60">{g.source_count}</span>
            </button>
          ))}
        </section>
      )}

      <section className="card p-4 space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="md:col-span-2">
            <label className="label">搜索描述 / 文字</label>
            <input
              className="input"
              placeholder="例如：开箱、惊讶、游戏画面…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div>
            <label className="label">类型</label>
            <select
              className="input"
              value={contentType}
              onChange={(e) => setContentType(e.target.value)}
            >
              <option value="">全部</option>
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t] || t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">钩子分 ≥ {minHook.toFixed(2)}</label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={minHook}
              onChange={(e) => setMinHook(Number(e.target.value))}
              className="w-full mt-2 accent-accent"
            />
          </div>
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-border flex-wrap">
          <span className="text-xs uppercase tracking-wider text-text-muted">真人素材</span>
          <FilterChip active={realPerson === "exclude"} onClick={() => setRealPerson("exclude")}>
            ✓ 自动排除
            {flaggedCount > 0 && realPerson === "exclude" && (
              <span className="ml-1.5 opacity-60">已隐藏 {flaggedCount}</span>
            )}
          </FilterChip>
          <FilterChip active={realPerson === "all"} onClick={() => setRealPerson("all")}>
            全部显示
          </FilterChip>
          <FilterChip active={realPerson === "only"} onClick={() => setRealPerson("only")}>
            ⚠ 只看真人
          </FilterChip>
          <span className="text-xs text-text-dim ml-1">
            生成视频时含真人的片段永远会自动排除，避免肖像权风险
          </span>
        </div>
      </section>

      <section className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
        {(clips ?? []).map((c) => (
          <button
            key={c.id}
            onClick={() => setSelected(c)}
            className="card overflow-hidden text-left hover:border-border-strong transition-colors"
          >
            <div className="relative aspect-[9/16] bg-bg-panel">
              {c.thumbnail_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={c.thumbnail_url} alt="" className="w-full h-full object-cover" />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-text-dim text-xs">
                  无缩略图
                </div>
              )}
              <div className="absolute top-1.5 left-1.5">
                <TypeBadge type={c.content_type} />
              </div>
              <div className="absolute bottom-1.5 right-1.5 badge bg-black/60 text-text">
                {c.duration.toFixed(1)}s
              </div>
              {c.has_real_person ? (
                <div className="absolute top-1.5 right-1.5 badge bg-err/85 text-white">
                  ⚠ 真人
                </div>
              ) : (c.hook_score ?? 0) > 0.5 ? (
                <div className="absolute top-1.5 right-1.5 badge bg-accent/80 text-white">
                  ★ {(c.hook_score! * 100).toFixed(0)}
                </div>
              ) : null}
            </div>
            <div className="p-2.5">
              <div className="text-xs line-clamp-2 leading-snug min-h-[28px]">
                {c.description || c.transcript || "暂无描述"}
              </div>
              <div className="flex flex-wrap gap-1 mt-1.5">
                {c.game && (
                  <span className="badge bg-accent/15 text-accent">🎮 {c.game}</span>
                )}
                {(c.tags ?? []).slice(0, 3).map((t) => (
                  <span key={t} className="badge bg-bg-hover text-text-muted">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          </button>
        ))}
        {clips && clips.length === 0 && (
          <div className="col-span-full text-sm text-text-dim p-8 text-center card">
            素材库是空的，先去「素材采集」处理几条视频。
          </div>
        )}
      </section>

      {selected && (
        <ClipModal
          clip={selected}
          onClose={() => setSelected(null)}
          onChanged={(updated) => {
            setSelected(updated);
            mutateClips();
          }}
        />
      )}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "badge transition-colors " +
        (active
          ? "bg-accent text-white"
          : "bg-bg-hover text-text-muted hover:text-text")
      }
    >
      {children}
    </button>
  );
}

function ClipModal({
  clip,
  onClose,
  onChanged,
}: {
  clip: Clip;
  onClose: () => void;
  onChanged: (c: Clip) => void;
}) {
  const [saving, setSaving] = useState(false);

  async function toggleRealPerson() {
    setSaving(true);
    try {
      const updated = await patch<Clip>(`/clips/${clip.id}`, {
        has_real_person: !clip.has_real_person,
      });
      onChanged(updated);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div
      className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="card max-w-3xl w-full max-h-[90vh] overflow-hidden flex flex-col md:flex-row"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="md:w-1/2 bg-black flex items-center justify-center">
          {clip.file_url ? (
            <video src={clip.file_url} controls autoPlay className="max-h-[80vh] w-full" />
          ) : (
            <div className="text-text-dim p-8">视频不可用</div>
          )}
        </div>
        <div className="md:w-1/2 p-5 overflow-auto scroll-thin space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-text-dim">#{clip.id}</span>
            <TypeBadge type={clip.content_type} />
            {clip.game && (
              <span className="badge bg-accent/15 text-accent">🎮 {clip.game}</span>
            )}
            <span className="badge bg-bg-hover text-text-muted">{clip.mood}</span>
            {clip.hook_score !== undefined && (
              <span className="badge bg-accent/20 text-accent">
                钩子分 {(clip.hook_score * 100).toFixed(0)}
              </span>
            )}
            {clip.has_real_person && (
              <span className="badge bg-err/20 text-err">⚠ 含真人</span>
            )}
          </div>

          <div
            className={
              "rounded p-3 border text-xs " +
              (clip.has_real_person
                ? "bg-err/10 border-err/30"
                : "bg-ok/5 border-ok/20")
            }
          >
            <div className="flex items-center justify-between gap-2 mb-1.5">
              <span className="font-medium text-text">
                肖像权检测: {clip.has_real_person ? "⚠ 检测到真人" : "✓ 仅游戏画面"}
              </span>
              <button
                onClick={toggleRealPerson}
                disabled={saving}
                className="text-[11px] underline text-text-muted hover:text-text disabled:opacity-50"
              >
                {saving ? "保存中…" : clip.has_real_person ? "标为游戏画面" : "标为真人"}
              </button>
            </div>
            {clip.real_person_note && (
              <div className="text-text-muted italic">{clip.real_person_note}</div>
            )}
            {clip.has_real_person && (
              <div className="text-err mt-1.5">
                此片段不会被自动选入生成视频，避免侵权风险。如果判断有误可点击右上"标为游戏画面"。
              </div>
            )}
          </div>

          <div>
            <div className="label">画面描述</div>
            <div className="text-sm">{clip.description || "—"}</div>
          </div>

          <div>
            <div className="label">原片台词</div>
            <div className="text-sm text-text-muted">{clip.transcript || "—"}</div>
          </div>

          <div>
            <div className="label">标签</div>
            <div className="flex flex-wrap gap-1.5">
              {(clip.tags ?? []).map((t) => (
                <span key={t} className="badge bg-bg-hover text-text-muted">
                  {t}
                </span>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs text-text-muted">
            <div>
              <div className="label">时间</div>
              {clip.start.toFixed(2)}s → {clip.end.toFixed(2)}s
            </div>
            <div>
              <div className="label">时长</div>
              {clip.duration.toFixed(2)}s
            </div>
            <div>
              <div className="label">来源</div>
              原始素材 #{clip.source_id}
            </div>
            <div>
              <div className="label">被引用</div>
              {clip.used_count || 0} 次
            </div>
          </div>

          <div className="pt-3 border-t border-border">
            <button onClick={onClose} className="btn-ghost w-full">
              关闭
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
