"use client";

import React, { useRef, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUp, User, Menu, AlertTriangle } from "lucide-react";

import type { Message } from "@/types";
import { shouldShowDiagnosis } from "@/types";
import { cn, formatTime } from "@/lib/utils";
import DiagnosisCard from "@/components/DiagnosisCard";
import SourcePanel from "@/components/SourcePanel";
import SuggestedQuestions from "@/components/SuggestedQuestions";

interface ChatWindowProps {
  messages: Message[];
  isLoading: boolean;
  onSendMessage: (text: string) => void;
  onToggleSidebar: () => void;
}

export default function ChatWindow({
  messages,
  isLoading,
  onSendMessage,
  onToggleSidebar,
}: ChatWindowProps) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 128)}px`;
  }, [input]);

  const handleSubmit = (e: React.FormEvent | React.KeyboardEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;
    onSendMessage(input.trim());
    setInput("");
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-gradient-to-b from-[#f8fbff] to-white overflow-hidden">

      {/* ── Header ─────────────────────────────────────── */}
      <header className="h-16 border-b border-slate-100/80 flex items-center px-4 sm:px-8 bg-white/60 backdrop-blur-xl sticky top-0 z-10 shrink-0">
        <div className="flex items-center gap-3 w-full">
          <button
            onClick={onToggleSidebar}
            className="lg:hidden p-2 text-slate-500 hover:bg-slate-100 rounded-xl transition-all"
          >
            <Menu className="w-5 h-5" />
          </button>

          {/* Avatar */}
          <div className="relative">
            <div className="w-9 h-9 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl flex items-center justify-center shadow-md shadow-blue-200/50">
              <span className="text-white text-sm font-bold">Rx</span>
            </div>
            <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-500 border-2 border-white rounded-full" />
          </div>

          <div>
            <p className="text-[14px] font-bold text-slate-800 leading-none">PharmBot</p>
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mt-0.5">
              เภสัชกร AI • Online
            </p>
          </div>
        </div>
      </header>

      {/* ── Messages ───────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto px-4 sm:px-10 pt-8 pb-40 space-y-6">

        {/* empty state */}
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center px-4 animate-in fade-in slide-in-from-bottom-6 duration-700 max-w-md mx-auto">
            <div className="w-20 h-20 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-[28px] flex items-center justify-center mb-6 shadow-2xl shadow-blue-200/60">
              <span className="text-white text-3xl font-bold">Rx</span>
            </div>
            <h2 className="text-2xl font-bold text-slate-900 tracking-tight mb-2">สวัสดีครับ!</h2>
            <p className="text-[14px] text-slate-500 font-medium leading-relaxed">
              ผมคือ PharmBot — เภสัชกร AI <br />
              บอกอาการหรือถามเรื่องยาได้เลยครับ
            </p>
          </div>
        )}

        {/* message list */}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "flex gap-3 max-w-3xl animate-in fade-in slide-in-from-bottom-3 duration-400",
              msg.role === "user" ? "flex-row-reverse ml-auto" : "mr-auto"
            )}
          >
            {/* avatar */}
            <div className={cn(
              "w-9 h-9 rounded-[14px] flex items-center justify-center shrink-0 mt-1 shadow-sm border overflow-hidden",
              msg.role === "user"
                ? "bg-slate-800 border-slate-700"
                : "bg-white border-slate-100"
            )}>
              {msg.role === "user" ? (
                <User className="w-4 h-4 text-white" />
              ) : (
                <span className="text-blue-600 font-bold text-xs">Rx</span>
              )}
            </div>

            {/* bubble + extras */}
            <div className={cn(
              "flex flex-col gap-2 min-w-0 max-w-[85%] sm:max-w-[75%]",
              msg.role === "user" ? "items-end" : "items-start"
            )}>
              {/* refer banner */}
              {msg.responseType === "refer" && (
                <div className="flex items-center gap-2 bg-rose-50 border border-rose-200 rounded-xl px-3 py-2 w-full">
                  <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0" />
                  <span className="text-[12px] font-bold text-rose-600">แนะนำพบแพทย์</span>
                </div>
              )}

              {/* main bubble */}
              <div className={cn(
                "px-5 py-3.5 rounded-[24px] text-[14px] leading-relaxed shadow-sm",
                msg.role === "user"
                  ? "bg-blue-600 text-white rounded-tr-sm"
                  : "bg-white border border-slate-100 text-slate-700 rounded-tl-sm"
              )}>
                {msg.role === "user" ? (
                  <p className="whitespace-pre-wrap font-medium">{msg.text}</p>
                ) : (
                  <div className="prose prose-sm max-w-none prose-slate prose-p:my-1 prose-headings:font-bold">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.text}
                    </ReactMarkdown>
                  </div>
                )}
              </div>

              {msg.role === "assistant" &&
                shouldShowDiagnosis(msg.responseType) &&       
                ((msg.diagnosis?.length ?? 0) > 0 || (msg.redFlags?.length ?? 0) > 0) && (
                  <DiagnosisCard
                    items={msg.diagnosis ?? []}
                    redFlags={msg.redFlags ?? []}
                    className="w-full"
                  />
                )}

              {msg.role === "assistant" &&
                shouldShowDiagnosis(msg.responseType) &&        
                (msg.sources?.length ?? 0) > 0 && (
                  <SourcePanel sources={msg.sources!} className="w-full" />
                )}

              {/* timestamp */}
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest px-1">
                {formatTime(msg.timestamp)}
              </span>
            </div>
          </div>
        ))}

        {/* typing indicator */}
        {isLoading && (
          <div className="flex gap-3 max-w-3xl mr-auto animate-in fade-in duration-300">
            <div className="w-9 h-9 rounded-[14px] bg-white border border-slate-100 flex items-center justify-center shrink-0 mt-1 shadow-sm">
              <span className="text-blue-600 font-bold text-xs">Rx</span>
            </div>
            <div className="bg-white border border-slate-100 rounded-[24px] rounded-tl-sm px-5 py-3.5 shadow-sm flex items-center gap-1.5">
              <span className="w-2 h-2 bg-blue-400 rounded-full animate-bounce [animation-delay:0ms]" />
              <span className="w-2 h-2 bg-blue-400 rounded-full animate-bounce [animation-delay:150ms]" />
              <span className="w-2 h-2 bg-blue-400 rounded-full animate-bounce [animation-delay:300ms]" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* ── Input area ─────────────────────────────────── */}
      <div className="sticky bottom-0 bg-gradient-to-t from-white via-white/95 to-transparent pt-6 pb-6 px-4 sm:px-6 z-20 shrink-0">
        <div className="max-w-3xl mx-auto space-y-3">

          {/* suggested questions — only when no messages yet */}
          {messages.length === 0 && (
            <SuggestedQuestions onSelect={(q) => onSendMessage(q)} />
          )}

          {/* input box */}
          <div className="flex items-end gap-2 bg-white border border-slate-200/80 rounded-[24px] p-2.5 shadow-[0_8px_32px_rgba(0,0,0,0.06)] focus-within:ring-2 focus-within:ring-blue-500/20 focus-within:border-blue-400/60 transition-all">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) handleSubmit(e);
              }}
              placeholder="อธิบายอาการหรือถามเรื่องยาได้เลยครับ..."
              rows={1}
              className="flex-1 bg-transparent resize-none border-none focus:ring-0 outline-none py-2 px-3 text-[14px] font-medium text-slate-700 placeholder:text-slate-400 min-h-[40px] max-h-32"
            />
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || isLoading}
              className="w-10 h-10 flex items-center justify-center rounded-full bg-blue-600 text-white
                         hover:bg-blue-700 disabled:bg-slate-100 disabled:text-slate-300
                         transition-all shadow-md shadow-blue-500/20 active:scale-90 shrink-0"
            >
              <ArrowUp className="w-5 h-5" />
            </button>
          </div>

          <p className="text-center text-[9px] text-slate-400 font-bold uppercase tracking-[0.2em]">
            PharmBot ให้ข้อมูลเบื้องต้นเท่านั้น • ปรึกษาแพทย์สำหรับการวินิจฉัยที่แน่นอน
          </p>
        </div>
      </div>
    </div>
  );
}