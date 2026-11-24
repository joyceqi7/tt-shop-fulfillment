"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import useSWR from "swr";
import { fetcher, post, del, patch, type Source } from "@/lib/workflow-api";
import { workflowUploadStore } from "@/lib/workflow-upload-store";
import { StatusBadge } from "@/components/workflow/Badge";
import { GamePicker } from "@/components/workflow/GamePicker";

type Tab = "url" | "upload";

export default function IngestPage() {
  const [tab, setTab] = useState<Tab>("url");
  const [urlsText, setUrlsText] = useState("");
  const [game, setGame] = useState("");
  const [cropBottom, setCropBottom] = useState(0);
  const [cropTop, setCropTop] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = useSyncExternalStore(
    workflowUploadStore.subscribe,
    workflowUploadStore.getSnapshot,
    workflowUploadStore.getSnapshot,
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const { data: sources, mutate } = useSWR<Source[]>("/sources?limit=30", fetcher, {
    refreshInterval: 3000,
  });

  useEffect(() => {
    if (upload.status === "done" && upload.completedAt) {
      mutate();
    }
  }, [mutate, upload.completedAt, upload.status]);

  async function submitUrls() {
    setError(null);
    const urls = urlsText
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.startsWith("http"));
    if (urls.length === 0) {
      setError("请粘贴至少一条 http(s) 开头的链接");
      return;
    }
    setSubmitting(true);
    try {
      await post("/sources/ingest", {
        urls,
        game: game.trim() || undefined,
        crop_top: cropTop,
        crop_bottom: cropBottom,
      });
      setUrlsText("");
      mutate();
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function submitUpload() {
    setError(null);
    if (upload.files.length === 0) {
      setError("请先选择视频文件");
      return;
    }
    workflowUploadStore.start({
      ...(game.trim() ? { game: game.trim() } : {}),
      crop_top: cropTop,
      crop_bottom: cropBottom,
    });
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function addFiles(fileList: FileList | null) {
    if (!fileList) return;
    const accepted: File[] = [];
    for (const f of Array.from(fileList)) {
      if (f.type.startsWith("video/") || /\.(mp4|mov|m4v|mkv|webm|avi|flv|ts)$/i.test(f.name)) {
        accepted.push(f);
      }
    }
    workflowUploadStore.addFiles(accepted);
  }

  function removeFile(idx: number) {
    workflowUploadStore.removeFile(idx);
  }

  async function onDelete(id: number) {
    if (!confirm(`删除 source #${id} 及其所有片段?`)) return;
    await del(`/sources/${id}`);
    mutate();
  }

  async function onRetry(id: number) {
    setError(null);
    try {
      await post(`/sources/${id}/retry`, {});
      mutate();
    } catch (e: any) {
      setError(e.message || String(e));
    }
  }

  const totalSize = upload.files.reduce((s, f) => s + f.size, 0);
  const uploading = upload.status === "uploading";

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">素材采集</h1>
        <p className="text-sm text-text-muted mt-1">
          支持粘贴视频链接自动抓取，或直接上传本地视频文件，后续处理流程完全一致。
        </p>
      </header>

      <section className="card overflow-hidden">
        <div className="flex border-b border-border">
          <TabBtn active={tab === "url"} onClick={() => setTab("url")}>
            ▸ URL 抓取
          </TabBtn>
          <TabBtn active={tab === "upload"} onClick={() => setTab("upload")}>
            ↑ 本地上传
          </TabBtn>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <label className="label">对应游戏</label>
            <GamePicker value={game} onChange={setGame} />
            <div className="text-xs text-text-dim mt-1.5">
              这批素材属于哪个游戏？后续可以按游戏筛选和生成视频。留空也行，之后还能补。
            </div>
          </div>

          {tab === "url" ? (
            <>
              <div>
                <label className="label">视频链接（一行一个）</label>
                <textarea
                  className="input font-mono text-xs"
                  rows={6}
                  placeholder={"https://www.douyin.com/video/xxxx\nhttps://www.youtube.com/watch?v=xxxx"}
                  value={urlsText}
                  onChange={(e) => setUrlsText(e.target.value)}
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">顶部裁剪 (px)</label>
                  <input
                    type="number"
                    className="input"
                    value={cropTop}
                    onChange={(e) => setCropTop(Number(e.target.value) || 0)}
                  />
                </div>
                <div>
                  <label className="label">底部裁剪 (px) — 抖音水印</label>
                  <input
                    type="number"
                    className="input"
                    value={cropBottom}
                    onChange={(e) => setCropBottom(Number(e.target.value) || 0)}
                  />
                </div>
              </div>

              {error && <div className="text-sm text-err">{error}</div>}

              <div className="flex items-center gap-3">
                <button onClick={submitUrls} disabled={submitting} className="btn-primary">
                  {submitting ? "提交中…" : "开始处理"}
                </button>
                <span className="text-xs text-text-dim">
                  自动完成：下载 · 去水印 · 转写文字 · 切分镜头 · 智能打标
                </span>
              </div>
            </>
          ) : (
            <>
              <div
                onDragEnter={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={(e) => {
                  e.preventDefault();
                  setDragging(false);
                }}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  addFiles(e.dataTransfer.files);
                }}
                onClick={() => fileInputRef.current?.click()}
                className={
                  "border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors " +
                  (dragging
                    ? "border-accent bg-accent/10"
                    : "border-border hover:border-border-strong hover:bg-bg-hover")
                }
              >
                <div className="text-sm">
                  <span className="text-accent font-medium">点击选择</span>，或将视频拖到这里
                </div>
                <div className="text-xs text-text-dim mt-2">
                  支持 mp4 / mov / mkv / webm / avi / flv，单文件最大 2GB，可多选
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept="video/*,.mp4,.mov,.m4v,.mkv,.webm,.avi,.flv,.ts"
                  className="hidden"
                  onChange={(e) => addFiles(e.target.files)}
                />
              </div>

              {upload.files.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs text-text-muted">
                    已选 {upload.files.length} 个文件 · 共 {formatBytes(totalSize)}
                    {uploading && <span className="ml-2 text-accent">上传进行中，切换模块也会继续</span>}
                  </div>
                  <div className="card divide-y divide-border max-h-56 overflow-auto scroll-thin">
                    {upload.files.map((f, i) => (
                      <div key={i} className="p-2.5 flex items-center gap-3 text-sm">
                        <span className="text-text-dim">▶</span>
                        <span className="flex-1 truncate" title={f.name}>{f.name}</span>
                        <span className="text-xs text-text-dim shrink-0">{formatBytes(f.size)}</span>
                        <button
                          onClick={() => removeFile(i)}
                          className="text-text-dim hover:text-err text-xs"
                          disabled={uploading}
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">顶部裁剪 (px)</label>
                  <input
                    type="number"
                    className="input"
                    value={cropTop}
                    onChange={(e) => setCropTop(Number(e.target.value) || 0)}
                  />
                </div>
                <div>
                  <label className="label">底部裁剪 (px)</label>
                  <input
                    type="number"
                    className="input"
                    value={cropBottom}
                    onChange={(e) => setCropBottom(Number(e.target.value) || 0)}
                  />
                </div>
              </div>

              {uploading && upload.progress > 0 && (
                <div>
                  <div className="flex justify-between text-xs text-text-muted mb-1">
                    <span>上传中</span>
                    <span>{upload.progress}%</span>
                  </div>
                  <div className="h-1.5 bg-bg rounded">
                    <div
                      className="h-full bg-accent rounded transition-all"
                      style={{ width: `${upload.progress}%` }}
                    />
                  </div>
                </div>
              )}

              {(error || upload.error) && (
                <pre className="whitespace-pre-wrap break-words rounded-md border border-err/30 bg-err/5 p-2 text-xs text-err">
                  {error || upload.error}
                </pre>
              )}
              {upload.status === "done" && (
                <div className="flex items-center gap-3 text-sm text-ok">
                  <span>上传完成，已进入后台处理队列。</span>
                  <button
                    type="button"
                    onClick={() => workflowUploadStore.clear()}
                    className="text-xs text-text-muted hover:text-text"
                  >
                    清除已完成文件
                  </button>
                </div>
              )}

              <div className="flex items-center gap-3">
                <button onClick={submitUpload} disabled={uploading || upload.files.length === 0} className="btn-primary">
                  {uploading ? "上传中…" : `上传并处理 (${upload.files.length})`}
                </button>
                <span className="text-xs text-text-dim">
                  上传完成后自动进入处理流程，可在上方填写裁剪参数去除黑边或水印
                </span>
              </div>
            </>
          )}
        </div>
      </section>

      <section>
        <h2 className="text-base font-medium mb-3">最近采集</h2>
        <div className="card divide-y divide-border">
          {(sources ?? []).length === 0 && (
            <div className="p-4 text-sm text-text-dim">还没有素材。</div>
          )}
          {(sources ?? []).map((s) => (
            <div key={s.id} className="p-3 flex items-center gap-4">
              <div className="w-20 h-32 bg-bg rounded overflow-hidden shrink-0">
                {s.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={s.thumbnail_url}
                    alt=""
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-text-dim text-xs">
                    无缩略图
                  </div>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="font-mono text-xs text-text-dim">#{s.id}</span>
                  <span className="badge bg-bg-hover text-text-muted">{s.platform || "?"}</span>
                  <StatusBadge status={s.status} />
                  <GameTag source={s} onChanged={() => mutate()} />
                </div>
                <div className="text-sm truncate" title={s.original_title || s.url}>
                  {s.original_title || s.url}
                </div>
                <div className="text-xs text-text-muted mt-0.5 truncate">
                  {s.author && <span>作者 {s.author} · </span>}
                  {s.duration ? `${s.duration.toFixed(1)}s · ` : ""}
                  {s.platform === "local" ? (
                    <span className="text-text-dim">本地上传</span>
                  ) : (
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:text-text"
                    >
                      原链接
                    </a>
                  )}
                </div>
                <SourceJobProgress source={s} />
                {(s.error || s.job_error) && (
                  <div className="text-xs text-err mt-1 break-words">
                    {humanizeSourceError(s.error || s.job_error || "")}
                  </div>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {s.status === "failed" && (
                  <button onClick={() => onRetry(s.id)} className="btn-secondary text-xs">
                    重试
                  </button>
                )}
                <button onClick={() => onDelete(s.id)} className="btn-danger text-xs">
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function TabBtn({
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
        "px-5 py-3 text-sm font-medium transition-colors border-b-2 " +
        (active
          ? "border-accent text-text"
          : "border-transparent text-text-muted hover:text-text")
      }
    >
      {children}
    </button>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

const STAGE_LABELS: Record<string, string> = {
  local: "读取本地文件",
  download: "下载视频",
  watermark: "清理画面",
  asr: "识别语音",
  scenes: "切分镜头",
  tag: "智能打标",
  done: "入库完成",
};

function SourceJobProgress({ source }: { source: Source }) {
  const active =
    source.status === "queued" ||
    source.status === "processing" ||
    source.job_status === "queued" ||
    source.job_status === "running";
  if (!active || source.status === "failed") return null;

  const progress = Math.max(0, Math.min(100, Math.round(source.job_progress || 0)));
  const stage = source.job_stage ? STAGE_LABELS[source.job_stage] || source.job_stage : "等待开始";

  return (
    <div className="mt-2 max-w-xl">
      <div className="mb-1 flex items-center justify-between text-[11px] text-text-muted">
        <span>{stage}</span>
        <span>{progress}%</span>
      </div>
      <div className="h-1.5 rounded bg-bg">
        <div
          className="h-full rounded bg-accent transition-all"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

function humanizeSourceError(raw: string): string {
  const text = raw.trim();
  if (!text) return "素材处理失败";
  if (/sign in to confirm|not a bot/i.test(text)) {
    return "YouTube 要求登录或人机验证：请让管理员配置 YouTube cookies，或先用本地上传救急。";
  }
  if (/Steam 商品页没有可抓取|Steam trailer 没有|Could not find any videos|Steam/i.test(text)) {
    return "这个 Steam 页面没有可直接抓取的视频：请换成有预告片的 Steam 商品页，或把视频文件下载后走本地上传。";
  }
  if (/cookies 文件不存在|cookies 路径不是文件/i.test(text)) {
    return "cookies 文件配置有误：请确认服务器上的 cookies.txt 路径存在，并重启 workflow 服务。";
  }
  if (/requested format is not available/i.test(text)) {
    return "YouTube 没有返回当前可下载格式：请先点重试；如果仍失败，让管理员更新素材服务后再试，急用时先本地上传。";
  }
  if (/remote components|challenge solving failed|Only images/i.test(text)) {
    return "YouTube 解析组件不可用：请让管理员检查 workflow 是否能访问 GitHub 组件，急用时先本地上传。";
  }
  if (/No such file|not found/i.test(text)) {
    return "素材文件不存在或已被清理：请重新上传。";
  }
  return text;
}

function GameTag({ source, onChanged }: { source: Source; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(source.game || "");
  const [saving, setSaving] = useState(false);

  async function save(next: string) {
    setSaving(true);
    try {
      await patch(`/sources/${source.id}`, { game: next });
      onChanged();
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
        <div className="w-44">
          <GamePicker value={val} onChange={setVal} placeholder="设置游戏…" />
        </div>
        <button
          onClick={() => save(val)}
          disabled={saving}
          className="text-xs text-accent hover:text-accent-hover px-1"
        >
          {saving ? "…" : "✓"}
        </button>
        <button
          onClick={() => {
            setVal(source.game || "");
            setEditing(false);
          }}
          className="text-xs text-text-dim hover:text-text px-1"
        >
          ✕
        </button>
      </div>
    );
  }

  return source.game ? (
    <button
      onClick={() => setEditing(true)}
      className="badge bg-accent/15 text-accent hover:bg-accent/25"
      title="点击修改对应游戏"
    >
      🎮 {source.game}
    </button>
  ) : (
    <button
      onClick={() => setEditing(true)}
      className="badge bg-bg-hover text-text-dim hover:text-text border border-dashed border-border"
    >
      + 设置游戏
    </button>
  );
}
