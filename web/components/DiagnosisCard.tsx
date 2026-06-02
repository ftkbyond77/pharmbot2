"use client";

import { DiagnosisItem, Confidence } from "@/types";
import { cn } from "@/lib/utils";
import { Stethoscope, AlertTriangle } from "lucide-react";

interface DiagnosisCardProps {
  items: DiagnosisItem[];
  redFlags?: string[];
  className?: string;
}

const CONFIDENCE_CONFIG: Record<
  Confidence,
  { label: string; bar: string; badge: string; dot: string }
> = {
  high:   { label: "ความเป็นไปได้สูง",   bar: "w-[85%]",  badge: "bg-rose-50 text-rose-600 border-rose-100",    dot: "bg-rose-500"   },
  medium: { label: "ความเป็นไปได้ปานกลาง", bar: "w-[55%]",  badge: "bg-amber-50 text-amber-600 border-amber-100",  dot: "bg-amber-400"  },
  low:    { label: "ความเป็นไปได้ต่ำ",    bar: "w-[25%]",  badge: "bg-slate-50 text-slate-500 border-slate-200",  dot: "bg-slate-300"  },
};

export default function DiagnosisCard({ items, redFlags = [], className }: DiagnosisCardProps) {
  if (!items?.length && !redFlags?.length) return null;

  return (
    <div className={cn("rounded-2xl border border-slate-100 bg-white overflow-hidden shadow-sm", className)}>

      {/* Red flags banner */}
      {redFlags.length > 0 && (
        <div className="bg-rose-50 border-b border-rose-100 px-4 py-3 flex gap-2.5 items-start">
          <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-[11px] font-bold text-rose-600 uppercase tracking-wider mb-1.5">พบสัญญาณอันตราย</p>
            <ul className="space-y-0.5">
              {redFlags.map((f, i) => (
                <li key={i} className="text-[13px] text-rose-700 font-medium">• {f}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* DDx list */}
      {items.length > 0 && (
        <div className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Stethoscope className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">การวินิจฉัยเบื้องต้น</span>
          </div>
          <ul className="space-y-3">
            {items.map((item, i) => {
              const cfg = CONFIDENCE_CONFIG[item.confidence] ?? CONFIDENCE_CONFIG.low;
              return (
                <li key={i} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={cn("w-2 h-2 rounded-full shrink-0", cfg.dot)} />
                      <span className="text-[14px] font-semibold text-slate-800 truncate">{item.name}</span>
                    </div>
                    <span className={cn(
                      "text-[10px] font-bold px-2 py-0.5 rounded-full border shrink-0",
                      cfg.badge
                    )}>
                      {cfg.label}
                    </span>
                  </div>
                  {/* confidence bar */}
                  <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
                    <div className={cn(
                      "h-full rounded-full transition-all duration-700",
                      item.confidence === "high"   ? "bg-rose-400"   :
                      item.confidence === "medium" ? "bg-amber-400"  : "bg-slate-300",
                      cfg.bar
                    )} />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}