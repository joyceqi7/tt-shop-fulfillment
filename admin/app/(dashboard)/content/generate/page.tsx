"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  fetcher,
  post,
  patch,
  del,
  uploadFiles,
  type Clip,
  type Generation,
  type Game,
  type Scene,
  type BgmInfo,
  type FontInfo,
  type Job,
} from "@/lib/workflow-api";
import { StatusBadge } from "@/components/workflow/Badge";
import { GamePicker } from "@/components/workflow/GamePicker";

const VOICE_PRESETS = [
  { label: "默认女声", value: "" },
  { label: "Bella · 年轻营销女声", value: "EXAVITQu4vr4xnSDxMaL" },
  { label: "Rachel · 清晰自然女声", value: "21m00Tcm4TlvDq8ikWAM" },
  { label: "Elli · 轻快女声", value: "MF3mGyEYCl7XYWbV9V6O" },
  { label: "Josh · 清爽青年男声", value: "TxGEqnHWrfWFTfGW9XjX" },
  { label: "Antoni · 自然叙述男声", value: "ErXwobaYiN019PkySvjV" },
  { label: "Adam · 男声", value: "pNInz6obpgDQGcFmaJgB" },
];

export default function GeneratePage() {
  const [productName, setProductName] = useState("");
  const [productInfo, setProductInfo] = useState("");
  const [targetDuration, setTargetDuration] = useState(30);
  const [voice, setVoice] = useState("");
  const [game, setGame] = useState("");
  const [useBgm, setUseBgm] = useState(true);
  const [selectedBgm, setSelectedBgm] = useState("");
  const [bgmCategory, setBgmCategory] = useState("");
  const [subtitleFont, setSubtitleFont] = useState("impact");
  const [subtitleHighlightFont, setSubtitleHighlightFont] = useState("bebas_neue");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: gens, mutate } = useSWR<Generation[]>("/generations?limit=20", fetcher, {
    refreshInterval: 3000,
  });
  const { data: games } = useSWR<Game[]>("/games", fetcher);
  const { data: bgm, mutate: mutateBgm } = useSWR<BgmInfo>("/bgm/styles", fetcher);
  const { data: fontInfo } = useSWR<FontInfo>("/fonts", fetcher);
  const selectedGameCount = games?.find((g) => g.name === game)?.source_count ?? 0;
  const bgmCount = bgm?.files?.length ?? 0;

  async function submit() {
    setError(null);
    if (!productName.trim()) {
      setError("请填产品名称");
      return;
    }
    setSubmitting(true);
    try {
      await post("/generations", {
        product_name: productName,
        product_info: productInfo,
        target_duration: targetDuration,
        style: "energetic",
        voice: voice.trim() || null,
        game: game.trim() || undefined,
        bgm_style: useBgm ? undefined : "none",
        bgm_file: useBgm && selectedBgm ? selectedBgm : null,
        bgm_category: useBgm && !selectedBgm && bgmCategory ? bgmCategory : null,
        bgm_start_mode: "smart",
        subtitle_font: subtitleFont,
        subtitle_highlight_font: subtitleHighlightFont,
      });
      mutate();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">视频生成</h1>
        <p className="text-sm text-text-muted mt-1">
          自动选片 + 撰写英文脚本 →
          <span className="text-warn"> 你审核并调整文案 </span>→ 合成成片
        </p>
      </header>

      <section className="card p-5 space-y-4">
        <div>
          <label className="label">对应游戏</label>
          <GamePicker
            value={game}
            onChange={setGame}
            placeholder=""
            allowEmpty
            emptyLabel="全部素材 (不限游戏)"
          />
          <div className="text-xs text-text-dim mt-1.5">
            {game ? (
              <>
                只会从「{game}」相关的素材里挑片段
                {selectedGameCount > 0 && (
                  <span className="text-text-muted"> · {selectedGameCount} 个原始素材可用</span>
                )}
              </>
            ) : (
              "不限游戏：会从所有素材里挑（多游戏混剪不推荐用于卖某一个游戏）"
            )}
          </div>
        </div>

        <div>
          <label className="label">产品名称 *</label>
          <input
            className="input"
            value={productName}
            onChange={(e) => setProductName(e.target.value)}
          />
        </div>

        <div>
          <label className="label">产品卖点 / 信息</label>
          <textarea
            className="input"
            rows={4}
            value={productInfo}
            onChange={(e) => setProductInfo(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">目标时长 (秒)</label>
            <input
              type="number"
              className="input"
              min={10}
              max={90}
              value={targetDuration}
              onChange={(e) => setTargetDuration(Number(e.target.value) || 30)}
            />
          </div>
          <div>
            <label className="label">配音音色</label>
            <select className="input" value={voice} onChange={(e) => setVoice(e.target.value)}>
              {VOICE_PRESETS.map((v) => (
                <option key={v.label} value={v.value}>
                  {v.label}
                </option>
              ))}
            </select>
            <div className="text-xs text-text-dim mt-1.5">
              推荐 Bella 做营销口播；默认值可在服务器配置中调整。
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">字幕字体</label>
            <select className="input" value={subtitleFont} onChange={(e) => setSubtitleFont(e.target.value)}>
              {(fontInfo?.fonts ?? []).map((f) => (
                <option key={f.key} value={f.key} disabled={!f.available}>
                  {f.label}{f.available ? "" : "（未安装）"}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">高亮字体</label>
            <select className="input" value={subtitleHighlightFont} onChange={(e) => setSubtitleHighlightFont(e.target.value)}>
              {(fontInfo?.fonts ?? []).map((f) => (
                <option key={f.key} value={f.key} disabled={!f.available}>
                  {f.label}{f.available ? "" : "（未安装）"}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="label">背景音乐</label>
          <label className="inline-flex items-center gap-2 text-sm text-text-muted">
            <input
              type="checkbox"
              checked={useBgm}
              onChange={(e) => setUseBgm(e.target.checked)}
            />
            使用背景音乐库配乐
          </label>
          <div className="text-xs text-text-dim mt-1.5">
            {!useBgm ? (
              <>仅输出人声，不混入背景音乐。</>
            ) : bgmCount === 0 ? (
              <>
                <span className="text-warn">⚠ </span>
                背景音乐库还没有音乐，本次将跳过 BGM。可在下方批量上传。
              </>
            ) : selectedBgm ? (
              <>本次指定：<span className="text-text-muted font-mono">{selectedBgm.split("/").pop()}</span></>
            ) : bgmCategory ? (
              <>本次从「{bgmCategory}」分类随机选择，优先使用已分析卡点。</>
            ) : (
              <>会从背景音乐库的 {bgmCount} 首音乐中随机选一首，配音播放时自动压低，避免盖过人声。</>
            )}
          </div>
          {selectedBgm && (
            <button
              type="button"
              className="mt-2 text-xs text-text-dim hover:text-text-muted"
              onClick={() => setSelectedBgm("")}
            >
              改回随机配乐
            </button>
          )}
        </div>

        <BgmLibrary
          bgm={bgm}
          selectedBgm={selectedBgm}
          selectedCategory={bgmCategory}
          onCategoryChange={setBgmCategory}
          onSelect={(value) => {
            setUseBgm(true);
            setSelectedBgm(value);
          }}
          onChanged={() => mutateBgm()}
        />

        {error && <div className="text-sm text-err">{error}</div>}

        <div className="flex items-center gap-3">
          <button onClick={submit} disabled={submitting} className="btn-primary">
            {submitting ? "提交中…" : "→ 生成脚本"}
          </button>
          <span className="text-xs text-text-dim">
            生成脚本 → 审核确认 → 自动渲染成片
          </span>
        </div>

        <div className="text-xs text-text-dim border-t border-border pt-3 flex items-start gap-2">
          <span className="text-warn">🛡</span>
          <span>
            <span className="text-text-muted">肖像权保护：</span>
            识别为含真人的素材会自动排除，不进入候选。
            可在 <a href="/content/library" className="text-accent hover:underline">素材库</a> 切到「只看真人」复核。
          </span>
        </div>
      </section>

      <section>
        <h2 className="text-base font-medium mb-3">最近生成</h2>
        <div className="space-y-3">
          {(gens ?? []).length === 0 && (
            <div className="card p-4 text-sm text-text-dim">还没有生成过视频。</div>
          )}
          {(gens ?? []).map((g) => (
            <GenCard key={g.id} g={g} onChanged={() => mutate()} />
          ))}
        </div>
      </section>
    </div>
  );
}

/** 成片完成后的封面区：全宽独立卡片，始终可见（无封面时占位 + 说明） */
function GenerationCoverBlock({
  gen,
  onChanged,
}: {
  gen: Generation;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);
  const [coverTitle, setCoverTitle] = useState("");
  const [coverLine2, setCoverLine2] = useState("");
  const [coverLine3, setCoverLine3] = useState("");
  const [coverJobStarted, setCoverJobStarted] = useState(false);
  const hasCover = Boolean(gen.cover_url);
  const { data: coverJobs } = useSWR<Job[]>(
    coverJobStarted || busy ? `/jobs?kind=generate_cover&target_id=${gen.id}&limit=1` : null,
    fetcher,
    { refreshInterval: 2000 },
  );
  const coverJob = coverJobs?.[0];
  const coverWorking = Boolean(coverJob && ["queued", "running"].includes(coverJob.status));

  useEffect(() => {
    if (!coverWorking) return;
    const timer = window.setInterval(onChanged, 2500);
    return () => window.clearInterval(timer);
  }, [coverWorking, onChanged]);

  useEffect(() => {
    if (!coverJob) return;
    if (coverJob.status === "done" || coverJob.status === "failed") {
      setBusy(false);
      onChanged();
    }
  }, [coverJob?.status, onChanged]);

  /** 先请求 ?dl=1（后端 Content-Disposition: attachment），再 Blob 落盘；延迟 revoke，避免刚点就撤销导致只预览不落盘 */
  async function downloadCover() {
    const u = gen.cover_url;
    if (!u) return;
    setDlBusy(true);
    try {
      const abs =
        u.startsWith("http://") || u.startsWith("https://")
          ? u
          : `${window.location.origin}${u.startsWith("/") ? u : `/${u}`}`;
      const urlDl = `${abs}${abs.includes("?") ? "&" : "?"}dl=1`;
      const r = await fetch(urlDl);
      if (!r.ok) {
        const t = await r.text().catch(() => "");
        throw new Error(t?.slice(0, 200) || `下载失败 HTTP ${r.status}`);
      }
      const buf = await r.arrayBuffer();
      const blob = new Blob([buf], { type: "image/jpeg" });
      const name = `gen_${String(gen.id).padStart(5, "0")}_cover.jpg`;
      const a = document.createElement("a");
      const href = URL.createObjectURL(blob);
      a.href = href;
      a.download = name;
      a.rel = "noopener";
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
      }, 2500);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`封面下载失败：${msg}\n请确认本页与后端在同一站点（Next 已 rewrite /files），或用系统浏览器打开前端再试。`);
    } finally {
      setDlBusy(false);
    }
  }

  async function triggerCover() {
    if (!coverTitle.trim()) {
      alert("请填写封面主标题");
      return;
    }
    setBusy(true);
    try {
      const payload: Record<string, string> = { title: coverTitle.trim() };
      payload.line2 = coverLine2.trim();
      payload.line3 = coverLine3.trim();
      await post(`/generations/${gen.id}/regenerate-cover`, payload);
      setCoverJobStarted(true);
      onChanged();
    } catch (e: any) {
      alert(e?.message || String(e));
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-accent/25 bg-accent/5 p-3 space-y-2">
      <div className="text-xs font-semibold text-text-muted">
        TikTok 竖屏封面（1080×1920）
      </div>
      <div className="flex flex-wrap gap-4 items-start">
        <div className="shrink-0 w-[7.75rem]">
          {hasCover ? (
            <img
              src={`${gen.cover_url!}${gen.cover_url!.includes("?") ? "&" : "?"}v=${encodeURIComponent(gen.finished_at || String(gen.id))}`}
              alt="封面预览"
              className="w-full rounded-lg border border-border aspect-[9/16] object-cover bg-black shadow-md"
            />
          ) : (
            <div className="w-full aspect-[9/16] rounded-lg border-2 border-dashed border-border/80 flex flex-col items-center justify-center gap-1.5 px-2 text-center bg-bg/80">
              <span className="text-[11px] text-text-muted font-medium">暂无封面</span>
              <span className="text-[10px] text-text-dim leading-snug">
                渲染结束后自动生成
              </span>
            </div>
          )}
        </div>
        <div className="min-w-[10rem] flex-1 space-y-2">
          <p className="text-[11px] text-text-dim leading-relaxed">
            发布 TikTok 时在封面步骤选「从相册」上传此图。封面主标题必填，第二、三行可不填。
          </p>
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="space-y-1">
              <span className="text-[10px] text-text-muted">主标题 *</span>
              <input
                value={coverTitle}
                onChange={(e) => setCoverTitle(e.target.value)}
                className="w-full rounded-md border border-border bg-bg px-2 py-1.5 text-xs outline-none focus:border-accent"
              />
            </label>
            <label className="space-y-1">
              <span className="text-[10px] text-text-muted">第二行</span>
              <input
                value={coverLine2}
                onChange={(e) => setCoverLine2(e.target.value)}
                className="w-full rounded-md border border-border bg-bg px-2 py-1.5 text-xs outline-none focus:border-accent"
              />
            </label>
            <label className="space-y-1">
              <span className="text-[10px] text-text-muted">第三行</span>
              <input
                value={coverLine3}
                onChange={(e) => setCoverLine3(e.target.value)}
                className="w-full rounded-md border border-border bg-bg px-2 py-1.5 text-xs outline-none focus:border-accent"
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2 items-center">
            {hasCover ? (
              <button
                type="button"
                disabled={dlBusy}
                onClick={downloadCover}
                className="btn-primary text-xs px-3 py-2 rounded-md disabled:opacity-50"
              >
                {dlBusy ? "下载中…" : "下载封面"}
              </button>
            ) : (
              <span className="rounded-md border border-border bg-bg-panel px-3 py-2 text-xs text-text-dim">
                暂无封面可下载
              </span>
            )}
            <button
              type="button"
              disabled={busy || coverWorking}
              onClick={triggerCover}
              className="rounded-md border border-border bg-bg-panel px-3 py-2 text-xs hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {busy || coverWorking ? "生成中…" : hasCover ? "重新生成封面" : "生成封面"}
            </button>
          </div>
          {(busy || coverJob) && (
            <div className="rounded-md border border-border/70 bg-bg/70 p-2 text-xs text-text-muted space-y-1">
              <div className="flex justify-between gap-3">
                <span>
                  {coverWorking
                    ? coverJob?.stage === "cover"
                      ? "封面生成中"
                      : "等待生成封面"
                    : coverJob?.status === "done"
                    ? "封面已更新"
                    : coverJob?.status === "failed"
                    ? "封面生成失败"
                    : "提交封面任务中"}
                </span>
                <span>{Math.round(coverJob?.progress ?? 0)}%</span>
              </div>
              <div className="h-1.5 rounded bg-border/50 overflow-hidden">
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: `${Math.round(coverJob?.progress ?? (busy ? 8 : 0))}%` }}
                />
              </div>
              {coverJob?.log && (
                <div className="line-clamp-2 text-[11px] text-text-dim">
                  {coverJob.log.trim().split("\n").slice(-1)[0]?.replace(/^\[[^\]]+\]\s*/, "")}
                </div>
              )}
              {coverJob?.error && (
                <div className="text-err text-[11px]">{coverJob.error}</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function GenCard({ g, onChanged }: { g: Generation; onChanged: () => void }) {
  const isAwaitingReview = g.status === "awaiting_review";
  const isWorking = ["queued", "scripting", "rendering", "processing"].includes(g.status);
  const isDone = g.status === "done";
  const isFailed = g.status === "failed";

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-start gap-4">
        <div className="w-32 shrink-0">
          {g.output_url ? (
            <video src={g.output_url} controls className="w-full rounded bg-black aspect-[9/16]" />
          ) : (
            <div
              className={
                "aspect-[9/16] rounded flex items-center justify-center text-xs text-center px-2 " +
                (isAwaitingReview
                  ? "bg-warn/15 text-warn"
                  : isFailed
                  ? "bg-err/10 text-err"
                  : "bg-bg-panel text-text-dim")
              }
            >
              {isAwaitingReview
                ? "⏸ 等待审核"
                : g.status === "scripting"
                ? "✍ 写脚本中"
                : g.status === "rendering"
                ? "▶ 渲染中"
                : g.status === "queued"
                ? "⏱ 排队"
                : isFailed
                ? "✗ 失败"
                : "—"}
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-text-dim">#{g.id}</span>
            <StatusBadge status={g.status} />
            {g.game && (
              <span className="badge bg-accent/15 text-accent">🎮 {g.game}</span>
            )}
            <span className="text-xs text-text-muted">
              {g.target_duration}s · {g.style}
            </span>
            {g.bgm_file && (
              <span className="text-xs text-text-dim">
                BGM: {g.bgm_file.split("/").pop()}
              </span>
            )}
            {!g.bgm_file && g.bgm_category && (
              <span className="text-xs text-text-dim">BGM: {g.bgm_category}</span>
            )}
          </div>
          <div className="font-medium text-sm">{g.product_name || "(未命名)"}</div>
          {g.product_info && (
            <div className="text-xs text-text-muted line-clamp-2">{g.product_info}</div>
          )}
          {g.error && <div className="text-xs text-err">{g.error}</div>}
          {isDone && (
            <div className="flex items-center gap-3 pt-1.5 flex-wrap">
              {g.output_url && (
                <a href={g.output_url} download className="text-xs text-accent hover:underline">
                  下载视频
                </a>
              )}
              {g.subtitle_url && (
                <a href={g.subtitle_url} download className="text-xs text-accent hover:underline">
                  下载字幕
                </a>
              )}
              {g.voiceover_url && (
                <a href={g.voiceover_url} download className="text-xs text-accent hover:underline">
                  下载配音
                </a>
              )}
            </div>
          )}
        </div>
      </div>

      {isDone && <GenerationCoverBlock gen={g} onChanged={onChanged} />}

      {isAwaitingReview && g.script_json && (
        <ScriptReviewer gen={g} onChanged={onChanged} />
      )}
      {isWorking && g.status !== "queued" && (
        <div className="text-xs text-text-dim">⏳ 进行中, 列表会自动刷新</div>
      )}
    </div>
  );
}

function ScriptReviewer({ gen, onChanged }: { gen: Generation; onChanged: () => void }) {
  const script = gen.script_json!;
  const sceneVoiceover = (s?: Scene) => s?.voiceover || s?.caption || "";
  // 本地编辑态: 每个场景的 voiceover 文案
  const [drafts, setDrafts] = useState<string[]>(
    () => script.scenes.map((s) => sceneVoiceover(s)),
  );
  // gen 更新（如重新生成脚本后）, 用 script 内容初始化, 用 finished_at/created_at 不太可靠
  // 用 script 的 JSON.stringify 当 key 同步
  const lastSig = useRef<string>("");
  useEffect(() => {
    const sig = JSON.stringify(script.scenes.map((s) => [s.clip_id, sceneVoiceover(s)]));
    if (sig !== lastSig.current) {
      lastSig.current = sig;
      setDrafts(script.scenes.map((s) => sceneVoiceover(s)));
    }
  }, [script]);

  const [busy, setBusy] = useState<"" | "save" | "regen" | "render">("");
  const [err, setErr] = useState<string | null>(null);

  const { data: clipsById } = useSWR<Record<number, Clip>>(
    `/generations/${gen.id}/script-clips`,
    fetcher,
  );

  const dirty = useMemo(
    () => drafts.some((d, i) => d.trim() !== sceneVoiceover(script.scenes[i]).trim()),
    [drafts, script],
  );

  async function saveDrafts(): Promise<boolean> {
    setBusy("save");
    setErr(null);
    try {
      await patch(`/generations/${gen.id}/script`, {
        scenes: script.scenes.map((s, i) => ({
          clip_id: s.clip_id,
          voiceover: (drafts[i] ?? sceneVoiceover(s)).trim(),
        })),
      });
      onChanged();
      return true;
    } catch (e: any) {
      setErr(e.message || String(e));
      return false;
    } finally {
      setBusy("");
    }
  }

  async function regenerate() {
    if (!confirm("重新生成会丢弃当前脚本和你的编辑, 确认?")) return;
    setBusy("regen");
    setErr(null);
    try {
      await post(`/generations/${gen.id}/regenerate-script`, {});
      onChanged();
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy("");
    }
  }

  async function render() {
    if (drafts.some((d) => !d.trim())) {
      setErr("有场景缺少配音文案。请先填写空白文案，或点击重新生成。");
      return;
    }
    if (dirty) {
      const ok = await saveDrafts();
      if (!ok) return;
    }
    setBusy("render");
    setErr(null);
    try {
      await post(`/generations/${gen.id}/render`, {});
      onChanged();
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy("");
    }
  }

  const totalDuration = script.scenes.reduce((sum, s) => sum + (s.duration || 0), 0);
  const hasEmptyVoiceover = drafts.some((d) => !d.trim());

  return (
    <div className="rounded-md border border-warn/30 bg-warn/5 p-4 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-warn">✋ 脚本待你审核</div>
          <div className="text-xs text-text-muted">
            {script.scenes.length} 个场景 · 总计 ~{totalDuration.toFixed(1)}s · 修改下方配音文案后点击渲染
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={regenerate}
            disabled={!!busy}
            className="btn-ghost text-xs"
            title="重新生成一版整体脚本"
          >
            {busy === "regen" ? "重新生成中…" : "↻ 重新生成"}
          </button>
          {dirty && (
            <button
              onClick={saveDrafts}
              disabled={!!busy}
              className="btn-ghost text-xs"
            >
              {busy === "save" ? "保存中…" : "保存"}
            </button>
          )}
          <button
            onClick={render}
            disabled={!!busy}
            className="btn-primary text-xs"
          >
            {busy === "render" ? "提交中…" : "✓ 确认并渲染"}
          </button>
        </div>
      </div>

      {err && <div className="text-xs text-err">{err}</div>}
      {hasEmptyVoiceover && !err && (
        <div className="text-xs text-warn">
          有场景缺少配音文案。请补齐空白处，或点击重新生成。
        </div>
      )}

      <div className="space-y-2">
        {script.scenes.map((s, i) => (
          <SceneRow
            key={`${i}-${s.clip_id}`}
            index={i}
            scene={s}
            clip={clipsById?.[s.clip_id]}
            value={drafts[i] ?? ""}
            onChange={(v) =>
              setDrafts((prev) => prev.map((x, j) => (j === i ? v : x)))
            }
          />
        ))}
      </div>
    </div>
  );
}

function SceneRow({
  index,
  scene,
  clip,
  value,
  onChange,
}: {
  index: number;
  scene: Scene;
  clip?: Clip;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="card bg-bg-panel p-3 flex gap-3">
      <div className="w-16 shrink-0">
        <div className="aspect-[9/16] bg-bg rounded overflow-hidden">
          {clip?.thumbnail_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={clip.thumbnail_url}
              alt=""
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-[10px] text-text-dim">
              片段 #{scene.clip_id}
            </div>
          )}
        </div>
        <div className="text-[10px] text-text-dim text-center mt-1">
          {scene.duration.toFixed(1)}s
        </div>
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-center gap-2 text-[11px] text-text-muted">
          <span className="font-mono">场景 {index + 1}</span>
          <span>·</span>
          <span className="font-mono">片段 #{scene.clip_id}</span>
          {clip?.content_type && (
            <>
              <span>·</span>
              <span className="uppercase tracking-wider">{clip.content_type}</span>
            </>
          )}
          {scene.caption && (
            <>
              <span>·</span>
              <span className="italic">字幕：{scene.caption}</span>
            </>
          )}
        </div>
        <textarea
          className="input text-sm leading-relaxed"
          rows={2}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {clip?.description && (
          <div className="text-[11px] text-text-dim italic line-clamp-1">
            画面：{clip.description}
          </div>
        )}
      </div>
    </div>
  );
}

// ----- BGM 库管理 ---------------------------------------------------------

const BGM_ACCEPT = ".mp3,.m4a,.wav,.ogg,.flac";

function BgmLibrary({
  bgm,
  selectedBgm,
  selectedCategory,
  onCategoryChange,
  onSelect,
  onChanged,
}: {
  bgm?: BgmInfo;
  selectedBgm: string;
  selectedCategory: string;
  onCategoryChange: (value: string) => void;
  onSelect: (value: string) => void;
  onChanged: () => void;
}) {
  const maxMb = bgm?.max_upload_mb ?? 50;
  const categories = bgm?.categories ?? ["未分类", "朋克", "欢快", "紧张", "史诗", "电子", "轻松", "暗黑", "复古", "射击", "RPG"];
  const [uploadCategory, setUploadCategory] = useState(categories[0] ?? "未分类");
  const [filterCategory, setFilterCategory] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [localUpdates, setLocalUpdates] = useState<Record<string, Partial<NonNullable<BgmInfo["files"]>[number]>>>({});
  const [analyzingKey, setAnalyzingKey] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const allFiles = (bgm?.files ?? []).map((f) => ({
    ...f,
    ...(localUpdates[`${f.style}/${f.filename}`] ?? {}),
  }));
  const files = filterCategory ? allFiles.filter((f) => (f.category || "未分类") === filterCategory) : allFiles;

  async function handleUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setErr(null);
    setOkMsg(null);

    // 浏览器端先粗筛一遍, 失败信息比 backend 报 400 更友好
    const oversize = Array.from(files).find((f) => f.size > maxMb * 1024 * 1024);
    if (oversize) {
      setErr(`${oversize.name} 超过 ${maxMb}MB 单文件上限`);
      return;
    }

    setUploading(true);
    setProgress(0);
    try {
      const res = await uploadFiles<{ saved: string[]; errors: string[] }>(
        "/bgm/upload",
        files,
        { fieldName: "files", extraFields: { category: uploadCategory }, onProgress: setProgress }
      );
      onChanged();
      if (res.errors && res.errors.length) {
        setErr(res.errors.join("\n"));
      }
      if (res.saved && res.saved.length) {
        setOkMsg(`已上传 ${res.saved.length} 个文件到背景音乐库`);
      }
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleDelete(style: string, filename: string) {
    if (!confirm(`确定从「${style}」删除 ${filename} 吗？`)) return;
    setErr(null);
    setOkMsg(null);
    try {
      await del(`/bgm/${encodeURIComponent(style)}/${encodeURIComponent(filename)}`);
      onChanged();
      setOkMsg(`已删除 ${filename}`);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }

  async function handleCategory(style: string, filename: string, category: string) {
    setErr(null);
    setOkMsg(null);
    const key = `${style}/${filename}`;
    setLocalUpdates((prev) => ({ ...prev, [key]: { ...(prev[key] ?? {}), category } }));
    try {
      await patch(`/bgm/${encodeURIComponent(style)}/${encodeURIComponent(filename)}`, { category });
      onChanged();
    } catch (e: any) {
      setLocalUpdates((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setErr(String(e?.message ?? e));
    }
  }

  async function handleAnalyze(style: string, filename: string) {
    const key = `${style}/${filename}`;
    setErr(null);
    setOkMsg(null);
    setAnalyzingKey(key);
    setLocalUpdates((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? {}), analysis_status: "analyzing" },
    }));
    try {
      const res = await post<{ meta?: Partial<NonNullable<BgmInfo["files"]>[number]> }>(
        `/bgm/${encodeURIComponent(style)}/${encodeURIComponent(filename)}/analyze`,
        {},
      );
      setLocalUpdates((prev) => ({
        ...prev,
        [key]: {
          ...(prev[key] ?? {}),
          ...(res.meta ?? {}),
          analysis_status: res.meta?.analysis_status ?? "done",
        },
      }));
      onChanged();
      setOkMsg(`已完成 ${filename} 的卡点分析`);
    } catch (e: any) {
      setLocalUpdates((prev) => ({
        ...prev,
        [key]: { ...(prev[key] ?? {}), analysis_status: "failed" },
      }));
      setErr(String(e?.message ?? e));
    } finally {
      setAnalyzingKey("");
    }
  }

  return (
    <div className="rounded-lg border border-border/60 bg-bg-panel/40 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">背景音乐库</div>
        <div className="text-[11px] text-text-dim">
          单首 ≤ {maxMb}MB · 支持 mp3 / m4a / wav / ogg / flac
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className="input h-9 w-32 text-xs"
          value={uploadCategory}
          onChange={(e) => setUploadCategory(e.target.value)}
          disabled={uploading}
        >
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <input
          ref={fileRef}
          type="file"
          accept={BGM_ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => handleUpload(e.target.files)}
        />
        <button
          type="button"
          className="btn btn-secondary text-xs"
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
        >
          {uploading ? `上传中… ${progress}%` : "批量上传背景音乐"}
        </button>
        <select
          className="input h-9 w-36 text-xs"
          value={selectedCategory}
          onChange={(e) => {
            const value = e.target.value;
            onCategoryChange(value);
            setFilterCategory(value);
            if (value) onSelect("");
          }}
        >
          <option value="">生成时：全库随机</option>
          {categories.map((c) => (
            <option key={c} value={c}>生成时：{c}</option>
          ))}
        </select>
        <select
          className="input h-9 w-32 text-xs"
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
        >
          <option value="">全部分类</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {uploading && (
          <div className="flex-1 h-1 rounded-full bg-border/40 overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}
      </div>

      {err && (
        <pre className="whitespace-pre-wrap break-words rounded-md border border-err/30 bg-err/5 p-2 text-xs text-err">
          {err}
        </pre>
      )}
      {okMsg && !err && (
        <div className="text-xs text-ok">{okMsg}</div>
      )}

      {files.length > 0 ? (
        <ul className="divide-y divide-border/40 rounded-md border border-border/40 max-h-48 overflow-y-auto">
          {files.map((f) => (
            <li key={`${f.style}/${f.filename}`} className="flex items-center gap-2 px-2.5 py-1.5">
              <div className="min-w-0 flex-1">
                <div className="text-xs font-mono truncate" title={f.filename}>{f.filename}</div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-text-dim">
                  <span>{f.category || "未分类"}</span>
                  {f.bpm ? <span>{f.bpm} BPM</span> : null}
                  <span>{f.beat_count ?? 0} 个卡点</span>
                  <span>
                    {f.analysis_status === "done"
                      ? "已分析"
                      : f.analysis_status === "analyzing"
                      ? "分析中"
                      : f.analysis_status || "待分析"}
                  </span>
                  {(f.mood_tags ?? []).slice(0, 3).map((t) => (
                    <span key={t} className="rounded bg-bg px-1">{t}</span>
                  ))}
                </div>
              </div>
              <select
                className="input h-8 w-24 text-xs shrink-0"
                value={f.category || "未分类"}
                onChange={(e) => handleCategory(f.style, f.filename, e.target.value)}
              >
                {categories.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <audio controls preload="none" src={f.url} className="h-8 w-48 max-w-[45%]" />
              <button
                type="button"
                className="text-xs text-accent hover:underline shrink-0 disabled:opacity-50"
                disabled={analyzingKey === `${f.style}/${f.filename}`}
                onClick={() => handleAnalyze(f.style, f.filename)}
              >
                {analyzingKey === `${f.style}/${f.filename}` ? "分析中…" : "卡点分析"}
              </button>
              <button
                type="button"
                className={`text-xs shrink-0 ${
                  selectedBgm === `${f.style}/${f.filename}`
                    ? "text-ok"
                    : "text-accent hover:underline"
                }`}
                onClick={() => {
                  onCategoryChange("");
                  onSelect(`${f.style}/${f.filename}`);
                }}
              >
                {selectedBgm === `${f.style}/${f.filename}` ? "本次使用中" : "本次使用"}
              </button>
              <button
                type="button"
                className="text-xs text-err hover:underline shrink-0"
                onClick={() => handleDelete(f.style, f.filename)}
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-xs text-text-dim italic">
          还没有背景音乐。批量上传后，生成视频时会随机选一首使用。
        </div>
      )}
    </div>
  );
}
