import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Clock3, Fingerprint, Terminal, X } from "lucide-react";
import type { ExecutionTrace } from "../types";

export interface TraceEntry {
  trace: ExecutionTrace;
  message: string;
  createdAt: string;
}

export function TraceModal({ taskTitle, entries, onClose }: {
  taskTitle: string;
  entries: TraceEntry[];
  onClose: () => void;
}) {
  const [index, setIndex] = useState(entries.length - 1);
  const entry = entries[index];
  const trace = entry.trace;
  const succeeded = trace.exitCode === 0;

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/85 p-5" role="dialog" aria-modal="true" aria-labelledby="trace-title">
      <section className="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
        <header className="flex items-start justify-between border-b border-slate-700 px-6 py-5">
          <div className="flex gap-3">
            <span className="grid h-11 w-11 place-items-center rounded-xl bg-cyan-950 text-cyan-300"><Terminal /></span>
            <div>
              <h2 id="trace-title" className="text-lg font-bold">Execution history</h2>
              <p className="mt-1 text-sm text-slate-400">{taskTitle} · trace {index + 1} of {entries.length}</p>
            </div>
          </div>
          <button autoFocus aria-label="Close execution history" onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white"><X /></button>
        </header>
        <div className="overflow-y-auto p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${succeeded ? "border-emerald-900 bg-emerald-950/30 text-emerald-300" : "border-rose-900 bg-rose-950/30 text-rose-300"}`}>
              {succeeded ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
              Exit code {trace.exitCode ?? "unknown"}{trace.truncated ? " · content truncated to safety limit" : ""}
            </div>
            {entries.length > 1 && <nav className="flex items-center gap-2" aria-label="Execution trace history">
              <button aria-label="Previous execution trace" disabled={index === 0} onClick={() => setIndex((current) => current - 1)} className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:bg-slate-800 disabled:opacity-30"><ChevronLeft size={17} /></button>
              <span className="min-w-16 text-center text-xs text-slate-400">{index + 1} / {entries.length}</span>
              <button aria-label="Next execution trace" disabled={index === entries.length - 1} onClick={() => setIndex((current) => current + 1)} className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:bg-slate-800 disabled:opacity-30"><ChevronRight size={17} /></button>
            </nav>}
          </div>
          <div className="mb-5 grid gap-3 rounded-xl border border-slate-800 bg-slate-950/50 p-4 text-xs text-slate-400 sm:grid-cols-2">
            <span className="flex items-center gap-2"><Clock3 size={14} /> {trace.completedAt || entry.createdAt}</span>
            <span className="flex items-center gap-2 truncate" title={trace.contentSha256 || undefined}><Fingerprint size={14} /> SHA-256 {trace.contentSha256?.slice(0, 16) || "not recorded"}</span>
            <p className="sm:col-span-2 text-slate-300">{entry.message}</p>
          </div>
          <TraceBlock title="Command" value={trace.commandExecuted || "(not recorded)"} />
          <TraceBlock title="stdout" value={trace.stdout || "(empty)"} tone="text-emerald-200" />
          <TraceBlock title="stderr" value={trace.stderr || "(empty)"} tone="text-rose-200" />
        </div>
      </section>
    </div>
  );
}

function TraceBlock({ title, value, tone = "text-slate-200" }: { title: string; value: string; tone?: string }) {
  return <section className="mb-4">
    <h3 className="mb-2 text-xs font-bold uppercase tracking-widest text-slate-500">{title}</h3>
    <pre className={`max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs leading-5 ${tone}`}>{value}</pre>
  </section>;
}
