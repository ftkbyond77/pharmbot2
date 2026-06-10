// web/types/index.ts — v2
// Changes vs v1:
// - ResponseType เพิ่ม "conversational" | "diagnosis_explain"
// - Message.responseType อัพเดท union type ให้ครบ
// - ChatResponse.type อัพเดท union type + เพิ่ม response_type field
// - เพิ่ม DiagnosisExplainItem interface สำหรับ diagnosis_explain mode

// ── Chat ──────────────────────────────────────────────────────

export type MessageRole = "user" | "assistant";

// response types ทั้งหมดที่ backend ส่งมา
export type ResponseType =
  | "clarify"           // ระหว่างซักประวัติ
  | "normal"            // clinical recommendation เต็ม (มี DDx + sources)
  | "conversational"    // followup / chit_chat / off_topic / unknown (ไม่มี DDx)
  | "diagnosis_explain" // user ขอดู flow การวินิจฉัย (มี DDx + reasoning)
  | "refer";            // red flag → ส่งต่อแพทย์

export interface Message {
  id:        string;
  role:      MessageRole;
  text:      string;
  timestamp: string;
  // bot-only fields (undefined for user messages)
  responseType?:       ResponseType;
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

  // v2: type ครอบคลุมทุก response mode
  type: ResponseType;

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
  clinical_rationale?:   string[];      // สำหรับ diagnosis_explain mode
  antibiotic_indicated?: boolean;
  pushback_message?:     string | null;
  supportive_care?:      string[];
  when_to_see_doctor?:   string | null;
  clinical_scores?:      Record<string, unknown> | null;
  augmented_notes?:      string | null;
}

// ── Type guards ────────────────────────────────────────────────

/** true เมื่อ response ควรแสดง DDx card + Sources */
export function shouldShowDiagnosis(responseType: ResponseType | undefined): boolean {
  return responseType === "normal" || responseType === "diagnosis_explain";
}

/** true เมื่อ response เป็นแค่ข้อความสนทนา ไม่ต้อง render clinical panel */
export function isConversational(responseType: ResponseType | undefined): boolean {
  return responseType === "conversational" || responseType === "clarify";
}