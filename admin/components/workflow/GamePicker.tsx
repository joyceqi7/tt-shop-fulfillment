"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetcher, type Game } from "@/lib/workflow-api";

type Props = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  allowEmpty?: boolean;
  emptyLabel?: string;
  className?: string;
};

export function GamePicker({
  value,
  onChange,
  placeholder = "输入游戏名称…",
  allowEmpty = false,
  emptyLabel = "全部游戏",
  className = "",
}: Props) {
  const { data: games } = useSWR<Game[]>("/games", fetcher);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const filtered = (games ?? []).filter((g) =>
    value ? g.name.toLowerCase().includes(value.toLowerCase()) : true,
  );

  return (
    <div ref={wrapRef} className={`relative ${className}`}>
      <input
        className="input"
        placeholder={placeholder}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open && ((allowEmpty && value === "") || filtered.length > 0) && (
        <div className="absolute z-20 mt-1 w-full card max-h-56 overflow-auto scroll-thin shadow-lg">
          {allowEmpty && (
            <button
              type="button"
              onClick={() => {
                onChange("");
                setOpen(false);
              }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-bg-hover text-text-muted"
            >
              ▢ {emptyLabel}
            </button>
          )}
          {filtered.map((g) => (
            <button
              key={g.name}
              type="button"
              onClick={() => {
                onChange(g.name);
                setOpen(false);
              }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-bg-hover flex items-center justify-between"
            >
              <span>{g.name}</span>
              <span className="text-xs text-text-dim">{g.source_count}</span>
            </button>
          ))}
          {value && !filtered.some((g) => g.name === value) && (
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="w-full text-left px-3 py-2 text-sm hover:bg-bg-hover text-accent border-t border-border"
            >
              + 新建游戏「{value}」
            </button>
          )}
        </div>
      )}
    </div>
  );
}
