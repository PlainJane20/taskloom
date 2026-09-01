import { useMemo, useState, type FormEvent } from "react";
import { AlertTriangle, CirclePlay, ExternalLink, GitCommit, Pause, Play, Plus, ShieldAlert, Square, Terminal, X } from "lucide-react";
import type { AgentBridge } from "../hooks/useAgentBridge";
import type { AgentSession, AgentTask, ExecutionTrace, TaskStatus } from "../types";
import { ApprovalModal, type ApprovalDecisionPayload } from "./ApprovalModal";
import { TraceModal } from "./TraceModal";

const columns: { statuses: TaskStatus[]; title: string; accent: string }[] = [
  { statuses: ["draft"], title: "Drafts / Pending Review", accent: "bg-violet-400" },
  { statuses: ["backlog", "failed", "cancelled"], title: "Backlog", accent: "bg-slate-400" },
  { statuses: ["active", "blocked"], title: "Active", accent: "bg-cyan-400" },
  { statuses: ["needs_approval"], title: "Needs Approval", accent: "bg-amber-400" },
  { statuses: ["completed"], title: "Completed", accent: "bg-emerald-400" },
];

type GroupBy = "session" | "agent" | "branch" | "none";

function groupKey(task: AgentTask, groupBy: GroupBy): string {
  if (groupBy === "session") return task.sessionId || "unassigned";
  if (groupBy === "agent") return task.agentId || "unassigned";
  if (groupBy === "branch") return task.branchName || "unassigned";
  return "all";
}

function directory(filePath?: string | null): string | null {
  if (!filePath) return null;
  const pieces = filePath.split("/");
  return pieces.length > 1 ? pieces.slice(0, -1).join("/") : ".";
}

function collisionTaskIds(tasks: AgentTask[]): Set<string> {
  const active = tasks.filter((task) => ["active", "blocked", "needs_approval"].includes(task.status));
  const byDirectory = new Map<string, AgentTask[]>();
  for (const task of active) {
    const target = directory(task.filePath);
    if (target && task.agentId) byDirectory.set(target, [...(byDirectory.get(target) || []), task]);
  }
  const collisions = new Set<string>();
  for (const candidates of byDirectory.values()) {
    if (new Set(candidates.map((task) => task.agentId)).size > 1) {
      for (const task of candidates) collisions.add(task.id);
    }
  }
  return collisions;
}

function statusStyle(status?: AgentSession["status"]): string {
  if (status === "active") return "border-emerald-900 bg-emerald-950/40 text-emerald-300";
  if (status === "waiting_for_human") return "border-amber-900 bg-amber-950/40 text-amber-300";
  if (status === "error_stuck") return "border-rose-900 bg-rose-950/40 text-rose-300";
  return "border-slate-700 bg-slate-800 text-slate-400";
}

function TaskCard({ task, run, disabled, collision, showTrace }: {
  task: AgentTask; run: (id: string) => void; disabled: boolean; collision: boolean;
  showTrace: (trace: ExecutionTrace) => void;
}) {
  const trace = [...(task.worklogs || [])].reverse().find((entry) => entry.trace)?.trace;
  const progress = task.progressTotal > 1 ? `${task.progressCurrent} of ${task.progressTotal} subtasks done` : null;
  return (
    <article className={`rounded-xl border bg-slate-800/80 p-4 shadow-sm ${collision ? "border-rose-700" : task.status === "draft" ? "border-violet-800" : "border-slate-700"}`}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-semibold text-slate-100">{task.title}</h3>
        {task.status === "backlog" && task.filePath && <button aria-label={`Run ${task.title}`} disabled={disabled} onClick={() => run(task.id)} className="rounded-md p-1 text-cyan-300 hover:bg-slate-700 disabled:opacity-40"><CirclePlay size={19} /></button>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
        {task.agentId && <span className="rounded bg-slate-700 px-2 py-1 text-slate-300">{task.agentId}</span>}
        {task.confidenceScore != null && <span className={`rounded px-2 py-1 ${task.confidenceScore < 0.7 ? "bg-violet-950 text-violet-300" : "bg-emerald-950 text-emerald-300"}`}>{Math.round(task.confidenceScore * 100)}% confidence</span>}
        {collision && <span className="flex items-center gap-1 rounded bg-rose-950 px-2 py-1 text-rose-300"><ShieldAlert size={11} /> collision</span>}
      </div>
      <p className="mt-2 line-clamp-3 text-sm leading-5 text-slate-400">{task.prompt}</p>
      {progress && <div className="mt-3">
        <div className="mb-1 flex justify-between text-[10px] text-slate-400"><span>{progress}</span><span>{Math.round((task.progressCurrent / task.progressTotal) * 100)}%</span></div>
        <div className="h-1.5 overflow-hidden rounded-full bg-slate-950"><div className="h-full bg-cyan-400" style={{ width: `${(task.progressCurrent / task.progressTotal) * 100}%` }} /></div>
      </div>}
      {task.filePath && <code className="mt-3 block truncate rounded bg-slate-950 px-2 py-1 text-[11px] text-slate-400">{task.filePath}</code>}
      {(task.links || []).length > 0 && <div className="mt-3 flex flex-wrap gap-2">
        {task.links.map((link) => link.url
          ? <a key={link.id} href={link.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-cyan-300 hover:text-cyan-200"><ExternalLink size={12} /> {link.label || link.kind}</a>
          : <span key={link.id} className="flex items-center gap-1 text-xs text-slate-400"><GitCommit size={12} /> {link.label || link.gitSha}</span>)}
      </div>}
      {trace && <button onClick={() => showTrace(trace)} className="mt-3 flex items-center gap-1.5 text-xs font-semibold text-cyan-300 hover:text-cyan-200"><Terminal size={14} /> View execution trace</button>}
      {task.error && <p className="mt-2 flex gap-1 text-xs text-rose-300"><AlertTriangle size={14} /> {task.error}</p>}
    </article>
  );
}

export function KanbanBoard({ bridge }: { bridge: AgentBridge }) {
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>("session");
  const [trace, setTrace] = useState<ExecutionTrace | null>(null);
  const collisions = useMemo(() => collisionTaskIds(bridge.tasks), [bridge.tasks]);
  const lanes = useMemo(() => {
    const result = new Map<string, AgentTask[]>();
    for (const task of bridge.tasks) {
      const key = groupKey(task, groupBy);
      result.set(key, [...(result.get(key) || []), task]);
    }
    if (!result.size) result.set("all", []);
    return [...result.entries()];
  }, [bridge.tasks, groupBy]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true); setFormError(null);
    try {
      await bridge.createTask({ title: String(data.get("title")), prompt: String(data.get("prompt")), filePath: String(data.get("filePath")), provider: String(data.get("provider")) as "openai" | "ollama" });
      setShowForm(false);
    } catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function run(taskId: string) {
    setBusy(true); setFormError(null);
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

  async function control(sessionId: string, action: "pause" | "resume" | "kill") {
    setBusy(true); setFormError(null);
    try { await bridge.controlSession(sessionId, action); }
    catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  return (
    <section className="mx-auto max-w-[1800px]">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div><h2 className="text-lg font-semibold">Governed agent work</h2><p className="text-sm text-slate-500">Confidence-gated tasks, clustered progress, and inspectable execution history.</p></div>
        <div className="flex items-center gap-3">
          <label className="text-xs text-slate-400">Swimlanes <select aria-label="Group task swimlanes" value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)} className="ml-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200"><option value="session">Session</option><option value="agent">Agent</option><option value="branch">Branch</option><option value="none">None</option></select></label>
          <button onClick={() => setShowForm(true)} disabled={bridge.status !== "connected"} className="flex items-center gap-2 rounded-lg bg-cyan-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-cyan-300 disabled:opacity-40"><Plus size={17} /> New task</button>
        </div>
      </div>
      {formError && <p role="alert" className="mb-4 rounded-lg border border-rose-900 bg-rose-950/40 px-4 py-2 text-sm text-rose-300">{formError}</p>}
      <div className="space-y-6">
        {lanes.map(([key, tasks]) => {
          const session = groupBy === "session" ? bridge.sessions.find((item) => item.id === key) : undefined;
          const hasCollision = tasks.some((task) => collisions.has(task.id));
          const label = key === "all" ? "All work" : key === "unassigned" ? "Manual & unassigned" : key;
          return <section key={key} className="rounded-2xl border border-slate-800 bg-slate-950/30 p-3">
            <header className="mb-3 flex flex-wrap items-center gap-3 px-1">
              <h3 className="font-bold text-slate-200">{label}</h3>
              {session && <span className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${statusStyle(session.status)}`}>{session.status.replace(/_/g, " ")}</span>}
              {session?.branchName && <code className="text-xs text-slate-500">{session.branchName}</code>}
              {session && <div className="flex gap-1">
                {session.status === "active" && session.controlCapabilities.includes("pause") && <button aria-label={`Pause ${session.id}`} disabled={busy} onClick={() => void control(session.id, "pause")} className="rounded p-1.5 text-amber-300 hover:bg-slate-800 disabled:opacity-40"><Pause size={14} /></button>}
                {session.status === "idle" && session.controlCapabilities.includes("resume") && <button aria-label={`Resume ${session.id}`} disabled={busy} onClick={() => void control(session.id, "resume")} className="rounded p-1.5 text-emerald-300 hover:bg-slate-800 disabled:opacity-40"><Play size={14} /></button>}
                {session.status !== "completed" && session.controlCapabilities.includes("kill") && <button aria-label={`Stop ${session.id}`} disabled={busy} onClick={() => void control(session.id, "kill")} className="rounded p-1.5 text-rose-300 hover:bg-slate-800 disabled:opacity-40"><Square size={14} /></button>}
              </div>}
              {hasCollision && <span className="ml-auto flex items-center gap-1.5 rounded-lg border border-rose-900 bg-rose-950/40 px-3 py-1.5 text-xs text-rose-300"><ShieldAlert size={14} /> Multiple agents are touching the same directory</span>}
            </header>
            <div className="grid gap-3 xl:grid-cols-5">
              {columns.map((column) => {
                const items = tasks.filter((task) => column.statuses.includes(task.status));
                return <div key={column.title} className="min-h-64 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
                  <div className="mb-3 flex items-center gap-2 px-1"><span className={`h-2 w-2 rounded-full ${column.accent}`} /><h4 className="text-xs font-semibold">{column.title}</h4><span className="ml-auto rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">{items.length}</span></div>
                  <div className="space-y-3">{items.map((task) => <TaskCard key={task.id} task={task} run={(id) => void run(id)} disabled={busy} collision={collisions.has(task.id)} showTrace={setTrace} />)}</div>
                </div>;
              })}
            </div>
          </section>;
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
      {trace && <TraceModal trace={trace} onClose={() => setTrace(null)} />}
      {bridge.approval && <ApprovalModal request={bridge.approval} onDecision={decide} busy={busy} />}
    </section>
  );
}
