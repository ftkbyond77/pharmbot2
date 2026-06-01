"use client";

import { Activity, Plus, RotateCcw, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
  onNewChat: () => void;
  sessionId: string | null;
  messageCount: number;
  clarifyRound: number;
}

export default function Sidebar({
  isOpen,
  onClose,
  onNewChat,
  sessionId,
  messageCount,
  clarifyRound,
}: SidebarProps) {
  return (
    <>
      {/* Backdrop (mobile) */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/20 backdrop-blur-sm z-40 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside className={cn(
        "fixed inset-y-0 left-0 w-72 bg-white border-r border-slate-100 flex flex-col z-50",
        "shadow-[4px_0_24px_rgba(0,0,0,0.04)] transition-transform duration-300",
        "lg:relative lg:translate-x-0",
        isOpen ? "translate-x-0" : "-translate-x-full"
      )}>

        {/* ── Brand ───────────────────────────────────── */}
        <div className="p-6 border-b border-slate-50 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-[16px] flex items-center justify-center shadow-lg shadow-blue-200/50">
              <Activity className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="font-bold text-slate-900 text-[15px] tracking-tight">PharmBot</h1>
              <p className="text-[9px] font-bold text-blue-500 uppercase tracking-[0.2em] mt-0.5">
                Clinical AI
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="lg:hidden p-2 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-xl transition-all"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ── Actions ─────────────────────────────────── */}
        <div className="p-5 space-y-2.5">
          <button
            onClick={onNewChat}
            className="w-full flex items-center gap-3 px-4 py-2.5 bg-blue-600 text-white rounded-xl
                       font-bold text-[13px] hover:bg-blue-700 transition-all shadow-md shadow-blue-200
                       active:scale-[0.98]"
          >
            <Plus className="w-4 h-4" />
            บทสนทนาใหม่
          </button>

          {sessionId && (
            <button
              onClick={onNewChat}
              className="w-full flex items-center gap-3 px-4 py-2.5 bg-slate-50 text-slate-600 rounded-xl
                         font-bold text-[13px] hover:bg-slate-100 transition-all border border-slate-200/60
                         active:scale-[0.98]"
            >
              <RotateCcw className="w-4 h-4" />
              ล้างและเริ่มใหม่
            </button>
          )}
        </div>

        {/* ── Session stats ────────────────────────────── */}
        {sessionId && (
          <div className="mx-5 p-4 bg-slate-50 rounded-2xl border border-slate-100 space-y-3">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
              Session ปัจจุบัน
            </p>
            <div className="grid grid-cols-2 gap-3">
              <StatBox label="ข้อความ" value={messageCount} />
              <StatBox label="รอบถาม" value={clarifyRound} />
            </div>
            <p className="text-[10px] text-slate-400 font-mono truncate" title={sessionId}>
              ID: {sessionId.slice(0, 8)}…
            </p>
          </div>
        )}

        {/* ── Footer info ──────────────────────────────── */}
        <div className="mt-auto p-5 border-t border-slate-50">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-[14px] bg-gradient-to-br from-slate-700 to-slate-900 flex items-center justify-center text-white text-xs font-bold shadow-sm">
              Rx
            </div>
            <div>
              <p className="text-[13px] font-bold text-slate-800">เภสัชกร AI</p>
              <p className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                Phase 1 • RAG + LangGraph
              </p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}

function StatBox({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white rounded-xl p-3 border border-slate-100 text-center">
      <p className="text-xl font-bold text-slate-900">{value}</p>
      <p className="text-[10px] font-semibold text-slate-400 mt-0.5">{label}</p>
    </div>
  );
}