"use client";

const SUGGESTIONS = [
  "ไอเรื้อรังมา 2 สัปดาห์",
  "แพ้ยา amoxicillin ใช้ยาอะไรแทน?",
  "ปวดหัว มีไข้ต่ำๆ",
  "ท้องเสีย คลื่นไส้ 1 วัน",
  "ผื่นคันหลังกินยา",
];

export default function SuggestedQuestions({ onSelect }: { onSelect: (q: string) => void }) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
      {SUGGESTIONS.map((q) => (
        <button
          key={q}
          onClick={() => onSelect(q)}
          className="shrink-0 px-3.5 py-1.5 rounded-full bg-white border border-slate-200 text-[12px] font-semibold text-slate-600
                     hover:border-blue-400 hover:text-blue-600 hover:bg-blue-50 transition-all whitespace-nowrap shadow-sm"
        >
          {q}
        </button>
      ))}
    </div>
  );
}