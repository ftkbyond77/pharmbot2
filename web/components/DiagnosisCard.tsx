"use client";

import { DiagnosisItem } from "@/types";
import { cn } from "@/lib/utils";
import { Stethoscope, AlertTriangle } from "lucide-react";

interface DiagnosisCardProps {
  items:     DiagnosisItem[];
  redFlags?: string[];
  className?: string;
}

// 5 ระดับ: map จาก confidence string → label + style
// backend ส่งมา 3 ระดับ (high / medium / low)
// เพิ่มรองรับ "very_high" และ "very_low" ไว้เผื่ออนาคต
type Level = "very_high" | "high" | "medium" | "low" | "very_low";

function toLevel(raw: string): Level {
  const r = raw.toLowerCase().replace(/[\s-]/g, "_");
  if (r === "very_high")   return "very_high";
  if (r === "high")        return "high";
  if (r === "medium")      return "medium";
  if (r === "low")         return "low";
  if (r === "very_low")    return "very_low";
  return "low"; // fallback
}

interface LevelCfg {
  label:  string;
  dot:    string;   // tailwind bg class for dot
  badge:  string;   // badge text + bg
  bar:    string;   // width class for optional mini-bar
}

const LEVEL_MAP: Record<Level, LevelCfg> = {
  very_high: {
    label: "สูงมาก",
    dot:   "bg-rose-600",
    badge: "bg-rose-100 text-rose-700 border-rose-200",
    bar:   "w-full",
  },
  high: {
    label: "สูง",
    dot:   "bg-rose-400",
    badge: "bg-rose-50 text-rose-600 border-rose-100",
    bar:   "w-[80%]",
  },
  medium: {
    label: "ปานกลาง",
    dot:   "bg-amber-400",
    badge: "bg-amber-50 text-amber-600 border-amber-100",
    bar:   "w-[50%]",
  },
  low: {
    label: "ต่ำ",
    dot:   "bg-slate-300",
    badge: "bg-slate-50 text-slate-500 border-slate-200",
    bar:   "w-[25%]",
  },
  very_low: {
    label: "ต่ำมาก",
    dot:   "bg-slate-200",
    badge: "bg-slate-50 text-slate-400 border-slate-100",
    bar:   "w-[10%]",
  },
};

// ไม่แสดง bar เป็น % แต่ใช้ mini filled-dots แทน (5 จุด = 5 ระดับ)
const LEVEL_DOTS: Record<Level, number> = {
  very_high: 5,
  high:      4,
  medium:    3,
  low:       2,
  very_low:  1,
};

export default function DiagnosisCard({ items, redFlags = [], className }: DiagnosisCardProps) {
  if (!items?.length && !redFlags?.length) return null;

  return (
    <div className={cn(
      "rounded-2xl border border-slate-100 bg-white overflow-hidden shadow-sm",
      className
    )}>

      {/* Red flags banner */}
      {redFlags.length > 0 && (
        <div className="bg-rose-50 border-b border-rose-100 px-4 py-3 flex gap-2.5 items-start">
          <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-[11px] font-bold text-rose-600 uppercase tracking-wider mb-1.5">
              พบสัญญาณอันตราย
            </p>
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
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
              การวินิจฉัยเบื้องต้น
            </span>
          </div>

          <ul className="space-y-3">
            {items.map((item, i) => {
              const level = toLevel(item.confidence ?? "low");
              const cfg   = LEVEL_MAP[level];
              const dots  = LEVEL_DOTS[level];

              return (
                <li key={i} className="flex items-start justify-between gap-3">
                  {/* name + dot */}
                  <div className="flex items-center gap-2 min-w-0 pt-0.5">
                    <span className={cn("w-2 h-2 rounded-full shrink-0", cfg.dot)} />
                    <span className="text-[13px] font-semibold text-slate-800 leading-snug">
                      {item.name}
                    </span>
                  </div>

                  {/* badge + dots */}
                  <div className="flex items-center gap-2 shrink-0">
                    {/* 5-dot indicator */}
                    <div className="flex items-center gap-[3px]">
                      {[1, 2, 3, 4, 5].map(n => (
                        <span
                          key={n}
                          className={cn(
                            "w-1.5 h-1.5 rounded-full transition-all",
                            n <= dots ? cfg.dot : "bg-slate-100"
                          )}
                        />
                      ))}
                    </div>
                    {/* label badge */}
                    <span className={cn(
                      "text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap",
                      cfg.badge
                    )}>
                      {cfg.label}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>

          {/* legend */}
          <div className="mt-3 pt-2.5 border-t border-slate-50 flex items-center gap-1.5 flex-wrap">
            <span className="text-[9px] font-bold text-slate-300 uppercase tracking-widest mr-1">
              โอกาสเป็น
            </span>
            {(["very_high","high","medium","low","very_low"] as Level[]).map(l => (
              <span key={l} className={cn(
                "text-[9px] font-bold px-1.5 py-0.5 rounded-full border",
                LEVEL_MAP[l].badge
              )}>
                {LEVEL_MAP[l].label}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}