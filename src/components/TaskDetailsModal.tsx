import { useEffect, useState, type FormEvent } from "react";
import { Archive, Clock3, FileText, Save, Terminal, X } from "lucide-react";
import type { AgentTask, LLMProvider } from "../types";
import type { TraceEntry } from "./TraceModal";

export function TaskDetailsModal({ task, busy, onClose, onSave, onArchive, onShowTraces }: {
  task: AgentTask;
  busy: boolean;
  onClose: () => void;
  onSave: (input: Pick<AgentTask, "title" | "prompt" | "filePath" | "provider">) => Promise<void>;
  onArchive: () => void;
  onShowTraces: (taskTitle: string, entries: TraceEntry[]) => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [prompt, setPrompt] = useState(task.prompt);
  const [filePath, setFilePath] = useState(task.filePath || "");
  const [provider, setProvider] = useState<LLMProvider>(task.provider || "ollama");
  const editable = !["active", "blocked", "needs_approval"].includes(task.status);
  const traces = task.worklogs.flatMap((entry) => entry.trace ? [{ trace: entry.trace, message: entry.message, createdAt: entry.createdAt }] : []);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSave({ title, prompt, filePath: filePath || null, provider });
  }

  return <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/85 p-5" role="dialog" aria-modal="true" aria-labelledby="task-details-title">
    <section className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
      <header className="flex items-start justify-between border-b border-slate-700 px-6 py-5">
        <div className="flex gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-cyan-950 text-cyan-300"><FileText /></span>
          <div><h2 id="task-details-title" className="text-lg font-bold">Task details</h2><p className="mt-1 text-sm text-slate-400">{task.id} · version {task.version}</p></div>
        </div>
        <button aria-label="Close task details" onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-white"><X /></button>
      </header>
      <div className="grid min-h-0 overflow-y-auto lg:grid-cols-[1.15fr_0.85fr]">
        <form onSubmit={(event) => void submit(event)} className="space-y-4 border-b border-slate-700 p-6 lg:border-b-0 lg:border-r">
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-slate-800 px-3 py-1 capitalize text-slate-300">{task.status.replace(/_/g, " ")}</span>
            <span className="rounded-full bg-slate-800 px-3 py-1 capitalize text-slate-300">{task.source}</span>
            {task.confidenceScore != null && <span className="rounded-full bg-slate-800 px-3 py-1 text-slate-300">{Math.round(task.confidenceScore * 100)}% confidence</span>}
          </div>
          <label className="block text-sm font-semibold">Title<input aria-label="Task title" required disabled={!editable || busy} value={title} onChange={(event) => setTitle(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-normal disabled:opacity-60" /></label>
          <label className="block text-sm font-semibold">Instruction<textarea aria-label="Task instruction" required disabled={!editable || busy} rows={7} value={prompt} onChange={(event) => setPrompt(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-normal disabled:opacity-60" /></label>
          <label className="block text-sm font-semibold">Workspace-relative file<input aria-label="Task file path" disabled={!editable || busy || task.source === "provider"} value={filePath} onChange={(event) => setFilePath(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs font-normal disabled:opacity-60" /></label>
          <label className="block text-sm font-semibold">Provider<select aria-label="Task provider" disabled={!editable || busy} value={provider} onChange={(event) => setProvider(event.target.value as LLMProvider)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-normal disabled:opacity-60"><option value="ollama">Ollama (local)</option><option value="openai">OpenAI</option></select></label>
          {!editable && <p className="rounded-lg border border-amber-900 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">Pause or finish this task before editing or archiving it.</p>}
          <div className="flex flex-wrap justify-between gap-3 pt-2">
            <button type="button" disabled={!editable || busy} onClick={onArchive} className="flex items-center gap-2 rounded-lg border border-rose-900 px-4 py-2 text-sm font-semibold text-rose-300 hover:bg-rose-950/40 disabled:opacity-40"><Archive size={16} /> Archive</button>
            <button disabled={!editable || busy} className="flex items-center gap-2 rounded-lg bg-cyan-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-cyan-300 disabled:opacity-40"><Save size={16} /> Save changes</button>
          </div>
        </form>
        <section className="p-6">
          <div className="mb-4 flex items-center justify-between gap-3"><h3 className="font-bold">Activity history</h3>{traces.length > 0 && <button type="button" onClick={() => onShowTraces(task.title, traces)} className="flex items-center gap-1.5 text-xs font-semibold text-cyan-300 hover:text-cyan-200"><Terminal size={14} /> View traces</button>}</div>
          <div className="space-y-3">
            {task.worklogs.length === 0 && <p className="rounded-xl border border-dashed border-slate-700 p-5 text-center text-sm text-slate-500">No activity has been recorded yet.</p>}
            {[...task.worklogs].reverse().map((entry) => <article key={entry.id} className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
              <div className="flex items-center justify-between gap-3"><strong className="text-xs capitalize text-slate-300">{entry.kind.replace(/_/g, " ")}</strong><span className="flex items-center gap-1 text-[10px] text-slate-500"><Clock3 size={11} /> {new Date(entry.createdAt).toLocaleString()}</span></div>
              <p className="mt-2 text-xs leading-5 text-slate-400">{entry.message}</p>
            </article>)}
          </div>
        </section>
      </div>
    </section>
  </div>;
}
