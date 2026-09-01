import { useState, type FormEvent } from "react";
import { AlertTriangle, CirclePlay, Plus, X } from "lucide-react";
import type { AgentBridge } from "../hooks/useAgentBridge";
import type { AgentTask, TaskStatus } from "../types";
import { ApprovalModal, type ApprovalDecisionPayload } from "./ApprovalModal";

const columns: { status: TaskStatus; title: string; accent: string }[] = [
  { status: "backlog", title: "Backlog", accent: "bg-slate-400" },
  { status: "active", title: "Active", accent: "bg-cyan-400" },
  { status: "needs_approval", title: "Needs Approval", accent: "bg-amber-400" },
  { status: "completed", title: "Completed", accent: "bg-emerald-400" },
];

function TaskCard({ task, run, disabled }: { task: AgentTask; run: (id: string) => void; disabled: boolean }) {
  return (
    <article className="rounded-xl border border-slate-700 bg-slate-800/80 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-semibold text-slate-100">{task.title}</h3>
        {task.status === "backlog" && <button aria-label={`Run ${task.title}`} disabled={disabled} onClick={() => run(task.id)} className="rounded-md p-1 text-cyan-300 hover:bg-slate-700 disabled:opacity-40"><CirclePlay size={19} /></button>}
      </div>
      <p className="mt-2 line-clamp-3 text-sm leading-5 text-slate-400">{task.prompt}</p>
      {task.filePath && <code className="mt-3 block truncate rounded bg-slate-950 px-2 py-1 text-[11px] text-slate-400">{task.filePath}</code>}
      {task.error && <p className="mt-2 flex gap-1 text-xs text-rose-300"><AlertTriangle size={14} /> {task.error}</p>}
    </article>
  );
}

export function KanbanBoard({ bridge }: { bridge: AgentBridge }) {
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setFormError(null);
    try {
      await bridge.createTask({
        title: String(data.get("title")), prompt: String(data.get("prompt")),
        filePath: String(data.get("filePath")), provider: String(data.get("provider")) as "openai" | "ollama",
      });
      setShowForm(false);
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  }

  async function run(taskId: string) {
    setBusy(true);
    setFormError(null);
    try { await bridge.runTask(taskId); }
    catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function decide(message: ApprovalDecisionPayload) {
    setBusy(true);
    try { await bridge.decideApproval(message.payload.requestId, message.payload.decision); }
    catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  return (
    <section className="mx-auto max-w-[1600px]">
      <div className="mb-5 flex items-center justify-between">
        <div><h2 className="text-lg font-semibold">Agent tasks</h2><p className="text-sm text-slate-500">Every file mutation pauses for your approval.</p></div>
        <button onClick={() => setShowForm(true)} disabled={bridge.status !== "connected"} className="flex items-center gap-2 rounded-lg bg-cyan-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"><Plus size={17} /> New task</button>
      </div>
      {formError && <p role="alert" className="mb-4 rounded-lg border border-rose-900 bg-rose-950/40 px-4 py-2 text-sm text-rose-300">{formError}</p>}
      <div className="grid gap-4 lg:grid-cols-4">
        {columns.map((column) => {
          const items = bridge.tasks.filter((task) => task.status === column.status || (column.status === "backlog" && task.status === "failed"));
          return <div key={column.status} className="min-h-[460px] rounded-xl border border-slate-800 bg-slate-900/60 p-3">
            <div className="mb-3 flex items-center gap-2 px-1"><span className={`h-2 w-2 rounded-full ${column.accent}`} /><h3 className="text-sm font-semibold">{column.title}</h3><span className="ml-auto rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">{items.length}</span></div>
            <div className="space-y-3">{items.map((task) => <TaskCard key={task.id} task={task} run={(id) => void run(id)} disabled={busy} />)}</div>
          </div>;
        })}
      </div>

      {showForm && <div className="fixed inset-0 z-40 grid place-items-center bg-slate-950/80 p-5">
        <form onSubmit={(event) => void submit(event)} className="w-full max-w-lg space-y-4 rounded-2xl border border-slate-700 bg-slate-900 p-6">
          <div className="flex justify-between"><h2 className="text-lg font-bold">Create agent task</h2><button type="button" aria-label="Close" onClick={() => setShowForm(false)}><X /></button></div>
          <label className="block text-sm">Title<input required name="title" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Update onboarding copy" /></label>
          <label className="block text-sm">Instruction<textarea required name="prompt" rows={4} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Make the introduction clearer and more concise." /></label>
          <label className="block text-sm">Workspace-relative file<input required name="filePath" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="docs/onboarding.md" /></label>
          <label className="block text-sm">Provider<select name="provider" defaultValue="ollama" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2"><option value="ollama">Ollama (local)</option><option value="openai">OpenAI</option></select></label>
          <button disabled={busy} className="w-full rounded-lg bg-cyan-400 py-2 font-bold text-slate-950 disabled:opacity-50">Add to backlog</button>
        </form>
      </div>}
      {bridge.approval && <ApprovalModal request={bridge.approval} onDecision={decide} busy={busy} />}
    </section>
  );
}
