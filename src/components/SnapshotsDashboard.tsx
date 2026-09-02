import { ArchiveRestore, CheckCircle2, FileClock, Search, ShieldCheck, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentBridge } from "../hooks/useAgentBridge";
import type { FileSnapshot, SnapshotPreview } from "../types";

type DateFilter = "all" | "day" | "week" | "month";

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function reasonLabel(reason: string): string {
  return reason === "pre_restore" ? "Restore safety point" : "Before agent write";
}

export function SnapshotsDashboard({ bridge }: { bridge: AgentBridge }) {
  const [query, setQuery] = useState("");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [preview, setPreview] = useState<SnapshotPreview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const agents = useMemo(() => Array.from(new Set(
    bridge.snapshots.map((snapshot) => snapshot.agentId).filter((value): value is string => Boolean(value)),
  )).sort(), [bridge.snapshots]);

  const visible = useMemo(() => {
    const now = Date.now();
    const age = dateFilter === "day" ? 86_400_000 : dateFilter === "week" ? 604_800_000 : dateFilter === "month" ? 2_592_000_000 : Infinity;
    const normalized = query.trim().toLowerCase();
    return bridge.snapshots.filter((snapshot) => {
      const created = new Date(snapshot.createdAt).getTime();
      const matchesDate = age === Infinity || (!Number.isNaN(created) && now - created <= age);
      const matchesAgent = agentFilter === "all" || snapshot.agentId === agentFilter;
      const matchesQuery = !normalized || snapshot.filePath.toLowerCase().includes(normalized)
        || snapshot.taskId?.toLowerCase().includes(normalized)
        || snapshot.agentId?.toLowerCase().includes(normalized);
      return matchesDate && matchesAgent && Boolean(matchesQuery);
    });
  }, [agentFilter, bridge.snapshots, dateFilter, query]);

  async function inspect(snapshot: FileSnapshot) {
    setBusy(true); setError(null); setNotice(null); setConfirming(false);
    try { setPreview(await bridge.previewSnapshot(snapshot.snapshotId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function restore() {
    if (!preview) return;
    setBusy(true); setError(null);
    try {
      await bridge.restoreSnapshot(preview.snapshotId, preview.currentSha256);
      await bridge.refreshSnapshots();
      setNotice(`Restored ${preview.filePath}. A new safety snapshot preserves the version you replaced.`);
      setPreview(null); setConfirming(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setConfirming(false);
    } finally { setBusy(false); }
  }

  return <section className="mx-auto max-w-[1600px]">
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div><h2 className="flex items-center gap-2 text-xl font-bold"><ArchiveRestore size={21} className="text-fuchsia-300" /> Recovery center</h2><p className="mt-1 text-sm text-slate-400">Inspect and restore workspace files from Taskloom's automatic safety snapshots.</p></div>
      <button type="button" onClick={() => void bridge.refreshSnapshots()} disabled={busy} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold hover:bg-slate-800 disabled:opacity-40">Refresh snapshots</button>
    </div>
    {notice && <p role="status" className="mb-5 rounded-lg border border-emerald-800 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-200">{notice}</p>}
    {error && <p role="alert" className="mb-5 rounded-lg border border-rose-800 bg-rose-950/30 px-4 py-3 text-sm text-rose-200">{error}</p>}
    <div className="mb-6 grid gap-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-4 md:grid-cols-[minmax(240px,1fr)_180px_200px]">
      <label className="relative"><span className="sr-only">Search snapshots</span><Search size={16} className="absolute left-3 top-3 text-slate-500" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search files, tasks, or agents" className="w-full rounded-lg border border-slate-700 bg-slate-950 py-2 pl-9 pr-3 text-sm" /></label>
      <label><span className="sr-only">Snapshot age</span><select value={dateFilter} onChange={(event) => setDateFilter(event.target.value as DateFilter)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"><option value="all">All dates</option><option value="day">Last 24 hours</option><option value="week">Last 7 days</option><option value="month">Last 30 days</option></select></label>
      <label><span className="sr-only">Snapshot agent</span><select value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm"><option value="all">All agents</option>{agents.map((agent) => <option key={agent}>{agent}</option>)}</select></label>
    </div>
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,.65fr)]">
      <div className="space-y-3">
        {visible.map((snapshot) => <article key={snapshot.snapshotId} className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="min-w-0"><p className="truncate font-mono text-sm font-semibold text-cyan-300">{snapshot.filePath}</p><div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-400"><span>{formatDate(snapshot.createdAt)}</span><span className="rounded bg-slate-800 px-2 py-0.5">{reasonLabel(snapshot.reason)}</span>{snapshot.agentId && <span>Agent: {snapshot.agentId}</span>}{snapshot.taskId && <span>Task: {snapshot.taskId}</span>}</div></div>
          <button type="button" onClick={() => void inspect(snapshot)} disabled={busy} className="flex items-center gap-2 rounded-lg border border-fuchsia-800 px-3 py-2 text-sm font-semibold text-fuchsia-200 hover:bg-fuchsia-950/40 disabled:opacity-40"><FileClock size={16} /> Compare</button>
        </article>)}
        {!visible.length && <div className="grid min-h-64 place-items-center rounded-2xl border border-dashed border-slate-800 text-center text-sm text-slate-500"><div><FileClock className="mx-auto mb-3" />{bridge.snapshots.length ? "No snapshots match these filters." : "Snapshots appear after Taskloom applies its first guarded file change."}</div></div>}
      </div>
      <aside className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5"><h3 className="flex items-center gap-2 font-bold"><ShieldCheck size={18} className="text-emerald-400" /> Restore audit</h3><p className="mt-1 text-xs text-slate-500">Every confirmed recovery execution is recorded locally.</p><div className="mt-4 space-y-3">{bridge.snapshotRestoreEvents.map((event) => <div key={event.id} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"><p className="flex items-center gap-2 text-sm font-semibold"><CheckCircle2 size={15} className={event.status === "completed" ? "text-emerald-400" : "text-rose-400"} /> {event.filePath}</p><p className="mt-1 text-xs text-slate-500">{event.status} · {formatDate(event.completedAt ?? event.createdAt)}</p></div>)}{!bridge.snapshotRestoreEvents.length && <p className="py-10 text-center text-sm text-slate-600">No restores yet.</p>}</div></aside>
    </div>
    {preview && <div role="dialog" aria-modal="true" aria-labelledby="snapshot-preview-title" className="fixed inset-0 z-50 grid place-items-center bg-slate-950/85 p-4"><div className="flex max-h-[92vh] w-full max-w-6xl flex-col rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
      <header className="flex items-start justify-between gap-4 border-b border-slate-800 p-5"><div><h3 id="snapshot-preview-title" className="text-xl font-bold">Compare snapshot</h3><p className="mt-1 font-mono text-sm text-cyan-300">{preview.filePath}</p></div><button aria-label="Close snapshot preview" onClick={() => { setPreview(null); setConfirming(false); }}><X /></button></header>
      <div className="grid min-h-0 flex-1 gap-px overflow-hidden bg-slate-700 md:grid-cols-2"><div className="min-h-0 bg-slate-950"><p className="border-b border-slate-800 px-4 py-2 text-xs font-bold uppercase tracking-wider text-fuchsia-300">Snapshot · {formatDate(preview.createdAt)}</p><pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap p-4 text-xs leading-5 text-emerald-200">{preview.snapshotContent || "(file did not exist)"}</pre></div><div className="min-h-0 bg-slate-950"><p className="border-b border-slate-800 px-4 py-2 text-xs font-bold uppercase tracking-wider text-cyan-300">Current workspace</p><pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap p-4 text-xs leading-5 text-slate-300">{preview.currentExists ? preview.currentContent : "(file does not exist)"}</pre></div></div>
      <footer className="border-t border-slate-800 p-5">{confirming ? <div className="flex flex-wrap items-center justify-between gap-4"><p className="max-w-2xl text-sm text-amber-200"><strong>Confirm restore:</strong> Taskloom will first snapshot the current file, then atomically replace it with this version.</p><div className="flex gap-3"><button onClick={() => setConfirming(false)} className="rounded-lg border border-slate-700 px-4 py-2 font-semibold">Cancel</button><button onClick={() => void restore()} disabled={busy} className="rounded-lg bg-emerald-400 px-4 py-2 font-bold text-slate-950 disabled:opacity-40">{busy ? "Restoring…" : "Confirm restore"}</button></div></div> : <div className="flex justify-end"><button onClick={() => setConfirming(true)} className="flex items-center gap-2 rounded-lg bg-fuchsia-300 px-4 py-2 font-bold text-slate-950"><ArchiveRestore size={17} /> Restore this version</button></div>}</footer>
    </div></div>}
  </section>;
}
