import { useMemo, useState, type FormEvent, type MouseEvent } from "react";
import { open } from "@tauri-apps/plugin-shell";
import { AlertTriangle, Archive, CheckCircle2, CirclePlay, ExternalLink, GitCommit, ListTree, Pause, Play, Plus, Search, ShieldAlert, Square, Terminal, X } from "lucide-react";
import type { AgentBridge } from "../hooks/useAgentBridge";
import type { AgentSession, AgentTask, LLMProvider, TaskStatus } from "../types";
import { ApprovalModal, type ApprovalDecisionPayload } from "./ApprovalModal";
import { TraceModal, type TraceEntry } from "./TraceModal";
import { TaskDetailsModal } from "./TaskDetailsModal";

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

async function openExternalLink(event: MouseEvent<HTMLAnchorElement>, url: string) {
  if (!("__TAURI_INTERNALS__" in window)) return;

  event.preventDefault();
  await open(url);
}

function TaskCard({ task, session, run, complete, requestControl, disabled, collision, showTraces, openDetails }: {
  task: AgentTask; run: (id: string) => void; complete: (task: AgentTask) => void;
  session?: AgentSession;
  requestControl: (session: AgentSession, action: "pause" | "resume" | "kill") => void;
  disabled: boolean; collision: boolean;
  showTraces: (taskTitle: string, entries: TraceEntry[]) => void;
  openDetails: (taskId: string) => void;
}) {
  const traceEntries = (task.worklogs || []).flatMap((entry) => entry.trace ? [{
    trace: entry.trace, message: entry.message, createdAt: entry.createdAt,
  }] : []);
  const progress = task.progressTotal > 1 ? `${task.progressCurrent} of ${task.progressTotal} subtasks done` : null;
  const importedIssue = task.source === "provider" && task.links.some((link) => link.kind === "issue");
  return (
    <article className={`rounded-xl border bg-slate-800/80 p-4 shadow-sm ${collision ? "border-rose-700" : task.status === "draft" ? "border-violet-800" : "border-slate-700"}`}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-semibold text-slate-100">{task.title}</h3>
        <div className="flex shrink-0 gap-1">
          <button aria-label={`Open details for ${task.title}`} title="Task details" onClick={() => openDetails(task.id)} className="rounded-md p-1 text-slate-400 hover:bg-slate-700 hover:text-cyan-300"><ListTree size={18} /></button>
          {task.status === "backlog" && task.filePath && <button aria-label={`Run ${task.title}`} disabled={disabled} onClick={() => run(task.id)} className="rounded-md p-1 text-cyan-300 hover:bg-slate-700 disabled:opacity-40"><CirclePlay size={19} /></button>}
          {task.status === "backlog" && importedIssue && <button aria-label={`Mark ${task.title} complete`} title="Mark complete and close the linked issue" disabled={disabled} onClick={() => complete(task)} className="rounded-md p-1 text-emerald-300 hover:bg-slate-700 disabled:opacity-40"><CheckCircle2 size={19} /></button>}
        </div>
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
          ? <a key={link.id} href={link.url} target="_blank" rel="noreferrer" onClick={(event) => void openExternalLink(event, link.url!)} className="flex items-center gap-1 text-xs text-cyan-300 hover:text-cyan-200"><ExternalLink size={12} /> {link.label || link.kind}</a>
          : <span key={link.id} className="flex items-center gap-1 text-xs text-slate-400"><GitCommit size={12} /> {link.label || link.gitSha}</span>)}
      </div>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {traceEntries.length > 0 && <button onClick={() => showTraces(task.title, traceEntries)} className="flex items-center gap-1.5 rounded-md px-1 py-1 text-xs font-semibold text-cyan-300 hover:bg-slate-700 hover:text-cyan-200"><Terminal size={14} /> View {traceEntries.length === 1 ? "execution trace" : `${traceEntries.length} execution traces`}</button>}
        {session?.status === "active" && session.controlCapabilities.includes("pause") && <button aria-label={`Pause agent for ${task.title}`} title="Pause agent" disabled={disabled} onClick={() => requestControl(session, "pause")} className="rounded-md p-1.5 text-amber-300 hover:bg-slate-700 disabled:opacity-40"><Pause size={14} /></button>}
        {session?.status === "idle" && session.controlCapabilities.includes("resume") && <button aria-label={`Resume agent for ${task.title}`} title="Resume agent" disabled={disabled} onClick={() => requestControl(session, "resume")} className="rounded-md p-1.5 text-emerald-300 hover:bg-slate-700 disabled:opacity-40"><Play size={14} /></button>}
        {session?.status !== "completed" && session?.controlCapabilities.includes("kill") && <button aria-label={`Stop agent for ${task.title}`} title="Stop agent" disabled={disabled} onClick={() => requestControl(session, "kill")} className="rounded-md p-1.5 text-rose-300 hover:bg-slate-700 disabled:opacity-40"><Square size={14} /></button>}
      </div>
      {task.error && <p className="mt-2 flex gap-1 text-xs text-rose-300"><AlertTriangle size={14} /> {task.error}</p>}
    </article>
  );
}

export function KanbanBoard({ bridge, defaultProvider = "ollama" }: { bridge: AgentBridge; defaultProvider?: LLMProvider }) {
  const [showForm, setShowForm] = useState(false);
  const [pendingCompletion, setPendingCompletion] = useState<AgentTask | null>(null);
  const [pendingControl, setPendingControl] = useState<{ session: AgentSession; action: "pause" | "resume" | "kill" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [completionNotice, setCompletionNotice] = useState<{ tone: "success" | "warning"; message: string } | null>(null);
  const [groupBy, setGroupBy] = useState<GroupBy>("session");
  const [traceHistory, setTraceHistory] = useState<{ taskTitle: string; entries: TraceEntry[] } | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pendingArchive, setPendingArchive] = useState<AgentTask[]>([]);
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const collisions = useMemo(() => collisionTaskIds(bridge.tasks), [bridge.tasks]);
  const filteredTasks = useMemo(() => bridge.tasks.filter((task) => {
    const needle = query.trim().toLowerCase();
    const matchesQuery = !needle || [task.title, task.prompt, task.filePath, ...task.links.map((link) => link.label)].some((value) => value?.toLowerCase().includes(needle));
    const matchesSource = sourceFilter === "all" || task.source === sourceFilter;
    const matchesStatus = statusFilter === "all"
      || (statusFilter === "open" && ["draft", "backlog", "active"].includes(task.status))
      || (statusFilter === "attention" && ["blocked", "needs_approval", "failed"].includes(task.status))
      || (statusFilter === "completed" && task.status === "completed");
    return matchesQuery && matchesSource && matchesStatus;
  }), [bridge.tasks, query, sourceFilter, statusFilter]);
  const selectedTask = selectedTaskId ? bridge.tasks.find((task) => task.id === selectedTaskId) ?? null : null;
  const lanes = useMemo(() => {
    const result = new Map<string, AgentTask[]>();
    for (const task of filteredTasks) {
      const key = groupKey(task, groupBy);
      result.set(key, [...(result.get(key) || []), task]);
    }
    if (!result.size) result.set("all", []);
    return [...result.entries()];
  }, [filteredTasks, groupBy]);

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

  async function complete(task: AgentTask) {
    setPendingCompletion(null);
    setBusy(true); setFormError(null); setCompletionNotice(null);
    try {
      const events = await bridge.completeTask(task.id);
      const problem = events.find((event) => event.status !== "completed");
      if (problem) {
        setCompletionNotice({ tone: "warning", message: `Task completed, but provider sync needs attention: ${problem.message}` });
      } else if (events.length > 0) {
        setCompletionNotice({ tone: "success", message: events.map((event) => event.message).join(" · ") });
      } else {
        setCompletionNotice({ tone: "success", message: "Task marked complete." });
      }
    } catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function decide(message: ApprovalDecisionPayload) {
    setBusy(true);
    try { await bridge.decideApproval(message.payload.requestId, message.payload.decision); }
    catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function control(sessionId: string, action: "pause" | "resume" | "kill") {
    setPendingControl(null); setBusy(true); setFormError(null); setCompletionNotice(null);
    try {
      await bridge.controlSession(sessionId, action);
      setCompletionNotice({ tone: "success", message: `Agent session ${sessionId} ${action === "kill" ? "stopped" : action === "pause" ? "paused" : "resumed"}.` });
    }
    catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  function requestControl(session: AgentSession, action: "pause" | "resume" | "kill") {
    if (action === "kill") setPendingControl({ session, action });
    else void control(session.id, action);
  }

  async function saveTask(task: AgentTask, input: Pick<AgentTask, "title" | "prompt" | "filePath" | "provider">) {
    setBusy(true); setFormError(null); setCompletionNotice(null);
    try {
      await bridge.editTask({ id: task.id, version: task.version, ...input });
      setCompletionNotice({ tone: "success", message: `Saved ${input.title}.` });
    } catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function archivePendingTasks() {
    const tasks = pendingArchive;
    setPendingArchive([]); setBusy(true); setFormError(null); setCompletionNotice(null);
    try {
      const archived = await bridge.archiveTasks(tasks.map((task) => task.id));
      setSelectedTaskId(null);
      setCompletionNotice({ tone: "success", message: `Archived ${archived.length} ${archived.length === 1 ? "task" : "tasks"}. The records remain in local storage.` });
    } catch (cause) { setFormError(cause instanceof Error ? cause.message : String(cause)); }
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
      {completionNotice && <p role="status" className={`mb-4 rounded-lg border px-4 py-2 text-sm ${completionNotice.tone === "success" ? "border-emerald-900 bg-emerald-950/40 text-emerald-300" : "border-amber-900 bg-amber-950/40 text-amber-300"}`}>{completionNotice.message}</p>}
      <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
        <label className="relative min-w-64 flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-2.5 text-slate-500" /><span className="sr-only">Search tasks</span><input aria-label="Search tasks" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search titles, instructions, files, and links" className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-9 pr-3 text-sm" /></label>
        <select aria-label="Filter tasks by source" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"><option value="all">All sources</option><option value="manual">Manual</option><option value="workflow">Workflow</option><option value="mcp">MCP</option><option value="provider">External provider</option></select>
        <select aria-label="Filter tasks by status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"><option value="all">All statuses</option><option value="open">Open work</option><option value="attention">Needs attention</option><option value="completed">Completed</option></select>
        <span className="text-xs text-slate-500">{filteredTasks.length} of {bridge.tasks.length}</span>
        <button type="button" disabled={busy || bridge.tasks.every((task) => task.status !== "completed")} onClick={() => setPendingArchive(bridge.tasks.filter((task) => task.status === "completed"))} className="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-40"><Archive size={16} /> Archive completed</button>
      </div>
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
                {session.status === "active" && session.controlCapabilities.includes("pause") && <button aria-label={`Pause ${session.id}`} disabled={busy} onClick={() => requestControl(session, "pause")} className="rounded p-1.5 text-amber-300 hover:bg-slate-800 disabled:opacity-40"><Pause size={14} /></button>}
                {session.status === "idle" && session.controlCapabilities.includes("resume") && <button aria-label={`Resume ${session.id}`} disabled={busy} onClick={() => requestControl(session, "resume")} className="rounded p-1.5 text-emerald-300 hover:bg-slate-800 disabled:opacity-40"><Play size={14} /></button>}
                {session.status !== "completed" && session.controlCapabilities.includes("kill") && <button aria-label={`Stop ${session.id}`} disabled={busy} onClick={() => requestControl(session, "kill")} className="rounded p-1.5 text-rose-300 hover:bg-slate-800 disabled:opacity-40"><Square size={14} /></button>}
              </div>}
              {hasCollision && <span className="ml-auto flex items-center gap-1.5 rounded-lg border border-rose-900 bg-rose-950/40 px-3 py-1.5 text-xs text-rose-300"><ShieldAlert size={14} /> Multiple agents are touching the same directory</span>}
            </header>
            <div className="grid gap-3 xl:grid-cols-5">
              {columns.map((column) => {
                const items = tasks.filter((task) => column.statuses.includes(task.status));
                return <div key={column.title} className="min-h-64 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
                  <div className="mb-3 flex items-center gap-2 px-1"><span className={`h-2 w-2 rounded-full ${column.accent}`} /><h4 className="text-xs font-semibold">{column.title}</h4><span className="ml-auto rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">{items.length}</span></div>
                  <div className="space-y-3">{items.map((task) => <TaskCard key={task.id} task={task} session={bridge.sessions.find((item) => item.id === task.sessionId)} run={(id) => void run(id)} complete={setPendingCompletion} requestControl={requestControl} disabled={busy} collision={collisions.has(task.id)} showTraces={(taskTitle, entries) => setTraceHistory({ taskTitle, entries })} openDetails={setSelectedTaskId} />)}</div>
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
          <label className="block text-sm">Provider<select name="provider" defaultValue={defaultProvider} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2"><option value="ollama">Ollama (local)</option><option value="openai">OpenAI</option></select></label>
          <button disabled={busy} className="w-full rounded-lg bg-cyan-400 py-2 font-bold text-slate-950 disabled:opacity-50">Add to backlog</button>
        </form>
      </div>}
      {pendingCompletion && <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/85 p-5" role="dialog" aria-modal="true" aria-labelledby="complete-task-title">
        <div className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
          <div className="flex items-start gap-3">
            <span className="rounded-xl bg-emerald-950 p-2 text-emerald-300"><CheckCircle2 size={24} /></span>
            <div>
              <h2 id="complete-task-title" className="text-lg font-bold text-slate-100">Complete linked task?</h2>
              <p className="mt-2 text-sm leading-6 text-slate-400">
                Mark <strong className="text-slate-200">{pendingCompletion.title}</strong> complete and close <strong className="text-cyan-300">{pendingCompletion.links.find((link) => link.kind === "issue")?.label || "the linked issue"}</strong>?
              </p>
            </div>
          </div>
          <p className="mt-4 rounded-lg border border-amber-900/70 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">Taskloom will record the outbound synchronization result. Provider conflicts remain visible and can be retried.</p>
          <div className="mt-6 flex justify-end gap-3">
            <button type="button" disabled={busy} onClick={() => setPendingCompletion(null)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-40">Cancel</button>
            <button type="button" disabled={busy} onClick={() => void complete(pendingCompletion)} className="flex items-center gap-2 rounded-lg bg-emerald-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-emerald-300 disabled:opacity-40"><CheckCircle2 size={17} /> Mark complete</button>
          </div>
        </div>
      </div>}
      {pendingControl?.action === "kill" && <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/85 p-5" role="dialog" aria-modal="true" aria-labelledby="stop-agent-title">
        <div className="w-full max-w-md rounded-2xl border border-rose-900 bg-slate-900 p-6 shadow-2xl">
          <div className="flex items-start gap-3">
            <span className="rounded-xl bg-rose-950 p-2 text-rose-300"><ShieldAlert size={24} /></span>
            <div>
              <h2 id="stop-agent-title" className="text-lg font-bold text-slate-100">Stop this agent session?</h2>
              <p className="mt-2 text-sm leading-6 text-slate-400">Session <strong className="text-slate-200">{pendingControl.session.id}</strong> will be asked to stop cooperatively. It cannot resume after acknowledging this request.</p>
            </div>
          </div>
          <p className="mt-4 rounded-lg border border-amber-900/70 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">Taskloom records the control state, but an external agent must poll and honor the request.</p>
          <div className="mt-6 flex justify-end gap-3">
            <button type="button" disabled={busy} onClick={() => setPendingControl(null)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-40">Cancel</button>
            <button type="button" disabled={busy} onClick={() => void control(pendingControl.session.id, "kill")} className="flex items-center gap-2 rounded-lg bg-rose-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-rose-300 disabled:opacity-40"><Square size={16} /> Stop agent</button>
          </div>
        </div>
      </div>}
      {traceHistory && <TraceModal taskTitle={traceHistory.taskTitle} entries={traceHistory.entries} onClose={() => setTraceHistory(null)} />}
      {selectedTask && <TaskDetailsModal task={selectedTask} busy={busy} onClose={() => setSelectedTaskId(null)} onSave={(input) => saveTask(selectedTask, input)} onArchive={() => setPendingArchive([selectedTask])} onShowTraces={(taskTitle, entries) => { setSelectedTaskId(null); setTraceHistory({ taskTitle, entries }); }} />}
      {pendingArchive.length > 0 && <div className="fixed inset-0 z-[60] grid place-items-center bg-slate-950/85 p-5" role="dialog" aria-modal="true" aria-labelledby="archive-task-title">
        <div className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
          <div className="flex items-start gap-3"><span className="rounded-xl bg-rose-950 p-2 text-rose-300"><Archive size={24} /></span><div><h2 id="archive-task-title" className="text-lg font-bold">Archive {pendingArchive.length === 1 ? "this task" : `${pendingArchive.length} completed tasks`}?</h2><p className="mt-2 text-sm leading-6 text-slate-400">Archived tasks leave the active board but remain recoverable in Taskloom's local SQLite history.</p></div></div>
          <div className="mt-6 flex justify-end gap-3"><button type="button" disabled={busy} onClick={() => setPendingArchive([])} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:bg-slate-800 disabled:opacity-40">Cancel</button><button type="button" disabled={busy} onClick={() => void archivePendingTasks()} className="flex items-center gap-2 rounded-lg bg-rose-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-rose-300 disabled:opacity-40"><Archive size={16} /> Archive</button></div>
        </div>
      </div>}
      {bridge.approval && <ApprovalModal request={bridge.approval} onDecision={decide} busy={busy} />}
    </section>
  );
}
