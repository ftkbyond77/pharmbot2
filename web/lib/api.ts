import type { ChatRequest, ChatResponse } from "@/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export async function sendMessage(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.detail ?? `API error ${res.status}`);
  }

  return res.json() as Promise<ChatResponse>;
}

export async function clearSession(sessionId: string): Promise<void> {
  await fetch(`${BASE_URL}/chat/${sessionId}`, { method: "DELETE" });
}

export async function getHistory(sessionId: string) {
  const res = await fetch(`${BASE_URL}/chat/${sessionId}/history`);
  if (!res.ok) return null;
  return res.json();
}