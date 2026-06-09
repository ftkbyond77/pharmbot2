"use client";

import { BookOpen, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

interface SourcePanelProps {
  sources:   string[];
  className?: string;
}

// Parse source string → { title, detail }
// รองรับรูปแบบ:
//   "Thai URI Children, p.50-57"
//   "AAFP 2022, P.630"
//   "AAFP 2022 table 2, P.630-631"
//   "Thai URI Children 2562 P.29, 33"
//   "[1]Thai URI Children p.50"       ← มี prefix [N] แล้วตัดออก
function parseSource(raw: string): { title: string; detail: string | null } {
  // ตัด prefix [N] ถ้ามี
  let s = raw.replace(/^\[\d+\]\s*/, "").trim();

  // หา separator: ", " หรือ " P." หรือ " p." หรือ " page "
  const sepPatterns = [
    /,\s*(?=(?:p\.|P\.|page\s|table\s|ตาราง|หน้า))/i,
    /\s+(?=(?:p\.|P\.)[\d])/,
  ];

  for (const pat of sepPatterns) {
    const m = s.search(pat);
    if (m > 0) {
      return {
        title:  s.slice(0, m).trim(),
        detail: s.slice(m).replace(/^,\s*/, "").trim(),
      };
    }
  }

  // ไม่มี separator: ทั้งหมดเป็น title
  return { title: s, detail: null };
}

// ย่อชื่อที่ยาวเกินไป
function shortTitle(t: string): string {
  const abbr: Record<string, string> = {
    "Thai URI Children":          "แนวทาง URI เด็ก (ไทย)",
    "Thai URI Children 2562":     "แนวทาง URI เด็ก 2562",
    "AAFP 2022":                  "AAFP Guideline 2022",
    "AAFP 2021":                  "AAFP Guideline 2021",
  };
  // ลอง exact match ก่อน
  for (const [key, val] of Object.entries(abbr)) {
    if (t.toLowerCase().startsWith(key.toLowerCase())) {
      return val + t.slice(key.length);
    }
  }
  return t;
}

export default function SourcePanel({ sources, className }: SourcePanelProps) {
  if (!sources?.length) return null;

  const parsed = sources.map(s => parseSource(s));

  return (
    <div className={cn(
      "rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3",
      className
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 mb-2.5">
        <BookOpen className="w-3 h-3 text-slate-400" />
        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
          อ้างอิงจาก Guideline
        </span>
      </div>

      {/* Source list */}
      <ul className="space-y-2">
        {parsed.map(({ title, detail }, i) => (
          <li key={i} className="flex items-start gap-2.5">
            {/* index badge */}
            <span className="
              inline-flex items-center justify-center
              w-4 h-4 rounded-full bg-slate-200 text-slate-500
              text-[9px] font-bold shrink-0 mt-0.5
            ">
              {i + 1}
            </span>

            <div className="min-w-0">
              {/* title */}
              <span className="text-[12px] font-semibold text-slate-700 leading-snug block">
                {shortTitle(title)}
              </span>

              {/* detail (page/table) */}
              {detail && (
                <span className="text-[11px] text-slate-400 font-medium leading-snug block mt-0.5">
                  {detail}
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}