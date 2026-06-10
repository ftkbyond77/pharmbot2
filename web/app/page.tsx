"use client";

import { useState, useCallback } from "react";
import Sidebar from "@/components/Sidebar";
import ChatWindow from "@/components/ChatWindow";
import { Message, ResponseType } from "@/types";
import { sendMessage, clearSession } from "@/lib/api";
import { generateId } from "@/lib/utils";

export default function HomePage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [clarifyRound, setClarifyRound] = useState(0);

  // ── send message ────────────────────────────────────────────
  const handleSendMessage = useCallback(async (text: string) => {
    // append user message immediately
    const userMsg: Message = {
      id: generateId(),
      role: "user",
      text,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    try {
      const res = await sendMessage({
        message: text,
        session_id: sessionId ?? undefined,
      });

      // persist session id from first response
      if (!sessionId) setSessionId(res.session_id);
      if (res.type === "clarify") {
        setClarifyRound((r) => r + 1);
      }

      const botMsg: Message = {
        id: generateId(),
        role: "assistant",
        text: res.message,
        timestamp: new Date().toISOString(),
        responseType: res.type as ResponseType,
        diagnosis:   res.diagnosis,
        sources:     res.sources,
        redFlags:    res.red_flags,
        referToDoctor:      res.refer_to_doctor,
        clarifyingQuestion: res.clarifying_question,
      };

      setMessages((prev) => [...prev, botMsg]);
    } catch (err) {
      const errMsg: Message = {
        id: generateId(),
        role: "assistant",
        text: "ขออภัยครับ เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง",
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  // ── new chat ─────────────────────────────────────────────────
  const handleNewChat = useCallback(async () => {
    if (sessionId) await clearSession(sessionId).catch(() => {});
    setMessages([]);
    setSessionId(null);
    setClarifyRound(0);
    setIsSidebarOpen(false);
  }, [sessionId]);

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 font-sans">
      <Sidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        onNewChat={handleNewChat}
        sessionId={sessionId}
        messageCount={messages.length}
        clarifyRound={clarifyRound}
      />
      <main className="flex-1 flex flex-col overflow-hidden">
        <ChatWindow
          messages={messages}
          isLoading={isLoading}
          onSendMessage={handleSendMessage}
          onToggleSidebar={() => setIsSidebarOpen((o) => !o)}
        />
      </main>
    </div>
  );
}