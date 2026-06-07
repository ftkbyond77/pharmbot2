"use client";

import { useState, useCallback, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface TestCase {
  id: string;
  category: string;
  input: string;
  expected_output: string;
  reference?: string;
}
interface RunResult extends TestCase {
  bot_response?: string;
  resp_type?: string;
  elapsed?: number;
  error?: string;
  score?: number;
  verdict?: "PASS" | "PARTIAL" | "FAIL" | "ERROR" | "pending" | "running";
  reasoning?: string;
}
type FilterCat = "all" | "easy" | "medium" | "hard" | "negative" | "incomplete";

const CAT_LABEL: Record<string, string> = {
  easy:"ง่าย", medium:"กลาง", hard:"ยาก", negative:"Negative", incomplete:"Incomplete",
};
const CAT_CHIP: Record<string,string> = {
  easy:       "bg-sky-50    text-sky-700    border-sky-200",
  medium:     "bg-violet-50 text-violet-700 border-violet-200",
  hard:       "bg-orange-50 text-orange-700 border-orange-200",
  negative:   "bg-rose-50   text-rose-700   border-rose-200",
  incomplete: "bg-amber-50  text-amber-700  border-amber-200",
};
const VERDICT_CHIP: Record<string,string> = {
  PASS:    "bg-emerald-50 text-emerald-700 border-emerald-200",
  PARTIAL: "bg-amber-50   text-amber-700   border-amber-200",
  FAIL:    "bg-red-50     text-red-600     border-red-200",
  ERROR:   "bg-red-50     text-red-600     border-red-200",
  running: "bg-blue-50    text-blue-600    border-blue-200",
  pending: "bg-slate-50   text-slate-400   border-slate-200",
};

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") + "/api/v1";
const JUDGE_BATCH = 5;
const JUDGE_DELAY = 16_000;

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }
function scoreColor(s?: number) {
  if (s === undefined) return "text-slate-400";
  return s >= 7 ? "text-emerald-600" : s >= 4 ? "text-amber-600" : "text-red-500";
}

function ScoreDial({ score }: { score?: number }) {
  const s = score ?? 0;
  const r = 22, cx = 28, cy = 28;
  const c = 2 * Math.PI * r;
  const col = s >= 7 ? "#10b981" : s >= 4 ? "#f59e0b" : "#ef4444";
  return (
    <svg width="56" height="56" viewBox="0 0 56 56">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#f1f5f9" strokeWidth="5"/>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={col} strokeWidth="5"
        strokeDasharray={c} strokeDashoffset={c * (1 - s / 10)}
        strokeLinecap="round" transform={`rotate(-90 ${cx} ${cy})`}/>
      <text x={cx} y={cy+1} textAnchor="middle" dominantBaseline="middle"
        fontSize="12" fontWeight="700" fill={score !== undefined ? col : "#94a3b8"}>
        {score !== undefined ? s.toFixed(0) : "—"}
      </text>
    </svg>
  );
}

export default function TestCaseDashboard() {
  const [cases,      setCases]      = useState<Record<string, RunResult[]>>({});
  const [loaded,     setLoaded]     = useState(false);
  const [running,    setRunning]    = useState(false);
  const [filter,     setFilter]     = useState<FilterCat>("all");
  const [expanded,   setExpanded]   = useState<Set<string>>(new Set());
  const [log,        setLog]        = useState<string[]>([]);
  const [showLog,    setShowLog]    = useState(false);
  const [results,    setResults]    = useState<Record<string, RunResult>>({});
  const [judgeModel, setJudgeModel] = useState("");
  const [isDone,     setIsDone]     = useState(false);
  const abortRef = useRef(false);

  const addLog = (msg: string) => setLog(p => [...p.slice(-150), msg]);

  // ── load ────────────────────────────────────────────────────────────────
  const loadCases = useCallback(async () => {
    try {
      addLog("กำลังโหลด test cases...");
      const [cr, cfgr] = await Promise.all([
        fetch(`${API_BASE}/test-cases`),
        fetch(`${API_BASE}/test-cases/config`),
      ]);
      if (!cr.ok) throw new Error(`HTTP ${cr.status}`);
      const data = await cr.json();
      const cfg  = cfgr.ok ? await cfgr.json() : {};
      setCases({ positive: data.positive ?? [], negative: data.negative ?? [], incomplete: data.incomplete ?? [] });
      setJudgeModel(cfg.model ?? "");
      setLoaded(true);
      const total = (data.positive?.length ?? 0) + (data.negative?.length ?? 0) + (data.incomplete?.length ?? 0);
      addLog(`โหลดสำเร็จ ${total} cases | Model: ${cfg.model ?? "?"}${cfg.api_key_set ? "" : " | ⚠️ GEMINI_API_KEY ไม่พบใน .env"}`);
    } catch (e: any) {
      addLog(`Error: ${e.message}`);
    }
  }, []);

  // ── download results ────────────────────────────────────────────────────
  const downloadResults = useCallback(() => {
    const allCases: RunResult[] = [
      ...(cases.positive  ?? []),
      ...(cases.negative  ?? []),
      ...(cases.incomplete ?? []),
    ];
    const rows = allCases.map(tc => {
      const r = results[tc.id] ?? {};
      return {
        id:              tc.id,
        category:        tc.category,
        input:           tc.input,
        expected_output: tc.expected_output,
        bot_response:    (r as RunResult).bot_response ?? "",
        resp_type:       (r as RunResult).resp_type ?? "",
        elapsed_s:       (r as RunResult).elapsed?.toFixed(2) ?? "",
        score:           (r as RunResult).score ?? "",
        verdict:         (r as RunResult).verdict ?? "",
        reasoning:       (r as RunResult).reasoning ?? "",
        reference:       tc.reference ?? "",
      };
    });

    // CSV
    const headers = Object.keys(rows[0] ?? {});
    const escape  = (v: unknown) => `"${String(v).replace(/"/g, '""')}"`;
    const csv = [
      headers.join(","),
      ...rows.map(r => headers.map(h => escape((r as any)[h])).join(",")),
    ].join("\n");

    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `pharmbot_results_${new Date().toISOString().slice(0,19).replace(/:/g,"-")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [cases, results]);

  // ── run all ──────────────────────────────────────────────────────────────
  const runAll = useCallback(async () => {
    if (running) return;
    setRunning(true);
    setIsDone(false);
    abortRef.current = false;
    setResults({});
    setLog([]);

    const allCases: RunResult[] = [
      ...(cases.positive ?? []),
      ...(cases.negative ?? []),
      ...(cases.incomplete ?? []),
    ];

    const apiResults: RunResult[] = [];
    for (const tc of allCases) {
      if (abortRef.current) break;
      setResults(prev => ({ ...prev, [tc.id]: { ...tc, verdict: "running" } }));
      const t0 = Date.now();
      try {
        const r   = await fetch(`${API_BASE}/chat`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: tc.input, session_id: crypto.randomUUID() }),
        });
        const d   = await r.json();
        const res = { ...tc, bot_response: d.message ?? "", resp_type: d.type ?? "?",
                      elapsed: (Date.now()-t0)/1000, verdict: "pending" as const };
        apiResults.push(res);
        setResults(prev => ({ ...prev, [tc.id]: res }));
        addLog(`${tc.id} → ${d.type} (${res.elapsed.toFixed(1)}s)`);
      } catch (e: any) {
        const res = { ...tc, error: e.message, verdict: "ERROR" as const, score: 0, reasoning: e.message };
        apiResults.push(res);
        setResults(prev => ({ ...prev, [tc.id]: res }));
        addLog(`${tc.id} → Error: ${e.message}`);
      }
    }

    const toJudge = apiResults.filter(r => r.bot_response && !r.error);
    const batches = [];
    for (let i = 0; i < toJudge.length; i += JUDGE_BATCH) batches.push(toJudge.slice(i, i+JUDGE_BATCH));

    for (let bi = 0; bi < batches.length; bi++) {
      if (abortRef.current) break;
      addLog(`Judge batch ${bi+1}/${batches.length} (${batches[bi].length} cases)...`);
      try {
        const res = await fetch(`${API_BASE}/test-cases/judge`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cases: batches[bi].map(c => ({
            id: c.id, category: c.category, input: c.input,
            expected_output: c.expected_output, bot_response: c.bot_response ?? "",
            reference: c.reference,
          })) }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const jj: Array<{id:string;score:number;verdict:string;reasoning:string}> = await res.json();
        jj.forEach(j => {
          setResults(prev => ({ ...prev, [j.id]: { ...prev[j.id], ...j } as RunResult }));
          addLog(`  ${j.id}: ${j.verdict} (${j.score}/10)`);
        });
      } catch (e: any) { addLog(`  Judge error: ${e.message}`); }
      if (bi < batches.length-1) {
        addLog(`  รอ ${JUDGE_DELAY/1000}s...`);
        await sleep(JUDGE_DELAY);
      }
    }

    setRunning(false);
    setIsDone(true);
    addLog("เสร็จสิ้น");
  }, [running, cases]);

  // ── stats ────────────────────────────────────────────────────────────────
  const allResultsList = Object.values(results);
  const judged    = allResultsList.filter(r => typeof r.score==="number" && r.score>=0 && r.verdict!=="running" && r.verdict!=="pending");
  const avgScore  = judged.length ? judged.reduce((a,r)=>a+(r.score??0),0)/judged.length : null;
  const passCount = judged.filter(r=>r.verdict==="PASS").length;
  const partial   = judged.filter(r=>r.verdict==="PARTIAL").length;
  const failCount = judged.filter(r=>r.verdict==="FAIL"||r.verdict==="ERROR").length;

  const allCases: RunResult[] = [
    ...(cases.positive  ?? []).map(c => ({...c,...(results[c.id]??{})})),
    ...(cases.negative  ?? []).map(c => ({...c,...(results[c.id]??{})})),
    ...(cases.incomplete ?? []).map(c => ({...c,...(results[c.id]??{})})),
  ];
  const filtered = filter==="all" ? allCases : allCases.filter(c=>c.category===filter);

  const catStats = (["easy","medium","hard","negative","incomplete"] as FilterCat[]).map(cat => {
    const cj    = judged.filter(r=>r.category===cat);
    const total = allCases.filter(c=>c.category===cat).length;
    const avg   = cj.length ? cj.reduce((a,r)=>a+(r.score??0),0)/cj.length : null;
    return { cat, total, judgedCount: cj.length, avg };
  });

  const toggle = (id: string) =>
    setExpanded(prev => { const n=new Set(prev); n.has(id)?n.delete(id):n.add(id); return n; });

  return (
    <div className="flex flex-col h-screen bg-slate-50">

      {/* Header */}
      <header className="h-14 bg-white border-b border-slate-100 flex items-center px-6 gap-4 shrink-0 sticky top-0 z-20">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-lg flex items-center justify-center shadow-sm">
            <span className="text-white text-xs font-bold">Rx</span>
          </div>
          <div>
            <p className="text-[13px] font-bold text-slate-800 leading-none">Test Dashboard</p>
            <p className="text-[10px] text-slate-400 font-medium mt-0.5">
              PharmBot · LLM-as-Judge{judgeModel ? ` · ${judgeModel}` : ""}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 ml-auto">
          <a href="/" className="text-[12px] text-slate-500 hover:text-blue-600 px-3 py-1.5 rounded-lg hover:bg-slate-100 transition-all">
            ← Chat
          </a>

          {/* Download button — shows after run completes */}
          {isDone && (
            <button onClick={downloadResults}
              className="text-[12px] px-3 py-1.5 rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 font-medium transition-all flex items-center gap-1.5">
              <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              Download CSV
            </button>
          )}

          <button onClick={() => setShowLog(p=>!p)}
            className={cn("text-[12px] px-3 py-1.5 rounded-lg border transition-all",
              showLog ? "bg-slate-800 text-white border-slate-800" : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50")}>
            Log
          </button>

          {!loaded ? (
            <button onClick={loadCases}
              className="text-[12px] px-4 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 font-medium transition-all">
              โหลด Test Cases
            </button>
          ) : (
            <button onClick={running ? () => { abortRef.current=true; } : runAll}
              className={cn("text-[12px] px-5 py-1.5 rounded-lg font-semibold transition-all",
                running ? "bg-amber-500 hover:bg-amber-600 text-white" : "bg-blue-600 hover:bg-blue-700 text-white")}>
              {running ? "⏹ หยุด" : "▶ Run All"}
            </button>
          )}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">

        {/* Sidebar */}
        <aside className="w-60 shrink-0 bg-white border-r border-slate-100 overflow-y-auto flex flex-col gap-5 p-4">
          <div className="flex flex-col items-center pt-2">
            <ScoreDial score={avgScore ?? undefined}/>
            <p className={cn("text-xl font-bold mt-1 leading-none", scoreColor(avgScore ?? undefined))}>
              {avgScore !== null ? avgScore.toFixed(1) : "—"}
              <span className="text-[11px] text-slate-400 font-normal"> /10</span>
            </p>
            <p className="text-[10px] text-slate-400 mt-1">
              {loaded ? `${judged.length} / ${allCases.length} judged` : "ยังไม่โหลด"}
            </p>
          </div>

          <div>
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2">Summary</p>
            <div className="grid grid-cols-3 gap-1">
              {[
                { label:"Pass",    val:passCount, cls:"text-emerald-600" },
                { label:"Partial", val:partial,   cls:"text-amber-600"   },
                { label:"Fail",    val:failCount, cls:"text-red-500"     },
              ].map(s => (
                <div key={s.label} className="bg-slate-50 rounded-lg p-2 text-center border border-slate-100">
                  <p className={cn("text-base font-bold", s.cls)}>{loaded ? s.val : "—"}</p>
                  <p className="text-[9px] text-slate-400">{s.label}</p>
                </div>
              ))}
            </div>
          </div>

          <div>
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2">By Category</p>
            <div className="space-y-2.5">
              {catStats.map(s => (
                <div key={s.cat}>
                  <div className="flex justify-between mb-0.5">
                    <span className="text-[11px] text-slate-600">{CAT_LABEL[s.cat]} ({s.judgedCount}/{s.total})</span>
                    <span className={cn("text-[11px] font-semibold", scoreColor(s.avg ?? undefined))}>
                      {s.avg !== null ? s.avg!.toFixed(1) : "—"}
                    </span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full rounded-full bg-gradient-to-r from-blue-400 to-indigo-500 transition-all duration-700"
                      style={{ width: `${s.avg !== null ? (s.avg!/10)*100 : 0}%` }}/>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2">Filter</p>
            <div className="flex flex-wrap gap-1.5">
              {(["all","easy","medium","hard","negative","incomplete"] as FilterCat[]).map(cat => (
                <button key={cat} onClick={() => setFilter(cat)}
                  className={cn("px-2.5 py-1 rounded-full text-[11px] font-medium border transition-all",
                    filter===cat ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-slate-500 border-slate-200 hover:border-blue-300 hover:text-blue-600")}>
                  {cat==="all" ? "All" : CAT_LABEL[cat]}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-2">Config</p>
            <div className="bg-slate-50 rounded-lg p-2.5 border border-slate-100 space-y-1">
              <p className="text-[10px] text-slate-500">
                <span className="font-medium text-slate-700">API:</span>{" "}
                <span className="font-mono text-[9px]">{API_BASE}</span>
              </p>
              <p className="text-[10px] text-slate-500">
                <span className="font-medium text-slate-700">Model:</span>{" "}
                {judgeModel || <span className="text-slate-400">— โหลดก่อน</span>}
              </p>
              <p className="text-[10px] text-emerald-600 font-medium mt-1">API Key อ่านจาก .env</p>
            </div>
          </div>
        </aside>

        {/* Main */}
        <main className="flex-1 overflow-y-auto p-5 space-y-2">
          {!loaded && (
            <div className="flex flex-col items-center justify-center h-64 gap-3 text-slate-400">
              <div className="text-5xl opacity-20">⚗</div>
              <p className="text-[13px]">กด &quot;โหลด Test Cases&quot; เพื่อเริ่ม</p>
              <p className="text-[11px] opacity-60">backend: <code>uvicorn api.main:app --reload</code></p>
            </div>
          )}

          {loaded && filtered.map(tc => {
            const r = results[tc.id] ?? (tc as RunResult);
            const exp = expanded.has(tc.id);
            const verdict = (r.verdict ?? "pending") as string;

            return (
              <div key={tc.id}
                className={cn("bg-white border rounded-xl overflow-hidden transition-all",
                  verdict==="running" ? "border-blue-300 shadow-sm shadow-blue-100" : "border-slate-100 hover:border-slate-200")}>

                <button className="w-full flex items-center gap-3 px-4 py-3 text-left" onClick={() => toggle(tc.id)}>
                  <span className="text-[11px] font-bold text-blue-600 font-mono w-28 truncate shrink-0">{tc.id}</span>
                  <span className="flex-1 text-[12px] text-slate-500 truncate min-w-0">{tc.input}</span>
                  <div className="flex items-center gap-2 shrink-0">
                    {verdict==="running" && <span className="w-3 h-3 rounded-full border-2 border-blue-400 border-t-transparent animate-spin"/>}
                    <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-semibold border", CAT_CHIP[tc.category] ?? "bg-slate-50 text-slate-500 border-slate-200")}>
                      {CAT_LABEL[tc.category] ?? tc.category}
                    </span>
                    {r.resp_type && (
                      <span className="px-2 py-0.5 rounded-full text-[10px] bg-slate-50 text-slate-500 border border-slate-200">{r.resp_type}</span>
                    )}
                    <span className={cn("px-2.5 py-0.5 rounded-full text-[10px] font-bold border", VERDICT_CHIP[verdict] ?? VERDICT_CHIP.pending)}>
                      {verdict==="pending"?"—":verdict==="running"?"...":verdict}
                    </span>
                    <span className={cn("text-[13px] font-bold w-12 text-right tabular-nums", scoreColor(r.score))}>
                      {r.score !== undefined ? `${r.score}/10` : "—"}
                    </span>
                    <span className={cn("text-[10px] text-slate-400 transition-transform", exp?"rotate-180":"")}>▼</span>
                  </div>
                </button>

                {exp && (
                  <div className="border-t border-slate-100 px-4 py-4 space-y-4">
                    <div>
                      <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Input</p>
                      <div className="bg-slate-50 rounded-lg p-3 text-[12px] text-slate-700 leading-relaxed border-l-2 border-indigo-300">{tc.input}</div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Expected</p>
                        <div className="bg-emerald-50 rounded-lg p-3 text-[12px] text-slate-700 leading-relaxed border-l-2 border-emerald-400 max-h-52 overflow-y-auto">
                          {tc.expected_output}
                          {tc.reference && <p className="text-[10px] text-slate-400 mt-2 pt-2 border-t border-emerald-200">Ref: {tc.reference}</p>}
                        </div>
                      </div>
                      <div>
                        <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">
                          Bot Response{r.elapsed !== undefined && <span className="text-slate-300 ml-1">· {r.elapsed.toFixed(1)}s</span>}
                        </p>
                        <div className="bg-blue-50 rounded-lg p-3 text-[12px] text-slate-700 leading-relaxed border-l-2 border-blue-400 max-h-52 overflow-y-auto">
                          {r.bot_response ? (
                            <div className="prose prose-sm max-w-none prose-slate prose-p:my-0.5">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{r.bot_response}</ReactMarkdown>
                            </div>
                          ) : r.error ? <span className="text-red-500 text-[11px]">Error: {r.error}</span>
                            : <span className="text-slate-400">—</span>}
                        </div>
                      </div>
                    </div>
                    {r.reasoning && (
                      <div>
                        <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">
                          Judge Reasoning
                          <span className={cn("ml-2 px-2 py-0.5 rounded-full text-[10px] font-bold border", VERDICT_CHIP[r.verdict ?? "pending"])}>
                            {r.verdict} · {r.score}/10
                          </span>
                        </p>
                        <div className="bg-amber-50 rounded-lg p-3 text-[12px] text-slate-700 leading-relaxed border-l-2 border-amber-400">{r.reasoning}</div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </main>
      </div>

      {/* Log */}
      {showLog && (
        <div className="fixed bottom-0 right-0 w-96 max-h-56 bg-slate-900 text-slate-300 text-[11px] font-mono rounded-tl-xl border-t border-l border-slate-700 overflow-y-auto p-3 z-50">
          <div className="flex justify-between items-center mb-2 sticky top-0 bg-slate-900 pb-1 border-b border-slate-700">
            <span className="text-slate-400 font-bold">Log</span>
            <button onClick={() => setLog([])} className="text-slate-600 hover:text-slate-400 text-[10px]">ล้าง</button>
          </div>
          {log.length === 0
            ? <div className="text-slate-600">Log จะแสดงที่นี่...</div>
            : log.map((l,i) => <div key={i} className="leading-relaxed py-px">{l}</div>)}
        </div>
      )}
    </div>
  );
}