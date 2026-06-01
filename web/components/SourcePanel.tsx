"use client";

import { BookOpen } from "lucide-react";
import { cn } from "@/lib/utils";

interface SourcePanelProps {
  sources: string[];
  className?: string;
}

export default function SourcePanel({ sources, className }: SourcePanelProps) {
  if (!sources?.length) return null;

  return (
    <div className={cn("rounded-xl border border-slate-100 bg-slate-50/60 px-4 py-3", className)}>
      <div className="flex items-center gap-2 mb-2">
        <BookOpen className="w-3 h-3 text-slate-400" />
        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">แหล่งที่มา</span>
      </div>
      <ul className="space-y-1">
        {sources.map((src, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className="text-[10px] font-bold text-slate-400 mt-0.5 shrink-0">[{i + 1}]</span>
            <span className="text-[12px] text-slate-600 font-medium leading-relaxed">{src}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}