// ── Chat ──────────────────────────────────────────────────────

export type MessageRole = "user" | "assistant";

export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  timestamp: string;
  // bot-only fields (undefined for user messages)
  responseType?: "clarify" | "recommendation" | "refer";
  diagnosis?: DiagnosisItem[];
  sources?: string[];
  redFlags?: string[];
  referToDoctor?: boolean;
  clarifyingQuestion?: string | null;
}

// ── Diagnosis ─────────────────────────────────────────────────

export type Confidence = "high" | "medium" | "low";

export interface DiagnosisItem {
  name: string;
  confidence: Confidence;
}

// ── API ───────────────────────────────────────────────────────

export interface ChatRequest {
  message: string;
  session_id?: string;
}

export interface ChatResponse {
  session_id: string;
  type: "clarify" | "recommendation" | "refer";
  message: string;
  diagnosis: DiagnosisItem[];
  recommendation: string | null;
  sources: string[];
  red_flags: string[];
  refer_to_doctor: boolean;
  clarifying_question: string | null;
}