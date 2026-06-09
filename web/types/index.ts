// ── Chat ──────────────────────────────────────────────────────

export type MessageRole = "user" | "assistant";

export interface Message {
  id:        string;
  role:      MessageRole;
  text:      string;
  timestamp: string;
  // bot-only fields (undefined for user messages)
  responseType?:       "clarify" | "recommendation" | "refer";
  diagnosis?:          DiagnosisItem[];
  sources?:            string[];
  redFlags?:           string[];
  referToDoctor?:      boolean;
  clarifyingQuestion?: string | null;
}

// ── Diagnosis ─────────────────────────────────────────────────

// 5 ระดับ — backend ส่งมา 3 ค่า (high/medium/low) แต่ component รองรับทั้ง 5
export type Confidence = "very_high" | "high" | "medium" | "low" | "very_low";

export interface DiagnosisItem {
  name:       string;
  confidence: Confidence | string; // string เผื่อ backend ส่งค่าอื่น
  reasoning?: string;
}

// ── API ───────────────────────────────────────────────────────

export interface ChatRequest {
  message:    string;
  session_id?: string;
}

export interface ChatResponse {
  session_id:           string;
  type:                 "clarify" | "recommendation" | "refer";
  message:              string;
  diagnosis:            DiagnosisItem[];
  recommendation:       string | null;
  sources:              string[];
  red_flags:            string[];
  refer_to_doctor:      boolean;
  clarifying_question:  string | null;
  // v2 extras
  first_line_drug?:      string | null;
  alternatives?:         string[];
  diagnosis_flow?:       string | null;
  antibiotic_indicated?: boolean;
  pushback_message?:     string | null;
  supportive_care?:      string[];
  when_to_see_doctor?:   string | null;
  clinical_scores?:      Record<string, unknown> | null;
  augmented_notes?:      string | null;
}