import {
  AlertTriangle, ArrowDownToLine, CheckCircle2, ExternalLink, Github, History,
  PlugZap, RefreshCw, ShieldCheck,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import type { AgentBridge } from "../hooks/useAgentBridge";
import type { SyncDirection } from "../types";

export function IntegrationsDashboard({ bridge }: { bridge: AgentBridge }) {
  const [repository, setRepository] = useState("");
  const [syncDirection, setSyncDirection] = useState<SyncDirection>("bidirectional");
  const [autoClose, setAutoClose] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(label: string, operation: () => Promise<void>) {
    setBusy(label);
    setNotice(null);
    setError(null);
    try {
      await operation();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function connect(event: FormEvent) {
    event.preventDefault();
    await run("connect", async () => {
      const connection = await bridge.createProviderConnection({
        provider: "github", repository, syncDirection, autoClose,
      });
      await bridge.testProviderConnection(connection.id);
      setRepository("");
      setNotice(`Connected ${connection.repository}.`);
    });
  }

  return (
    <section className="mx-auto max-w-[1600px] space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold">Integrations</h2>
          <p className="mt-1 text-sm text-slate-400">
            Synchronize external work without storing provider credentials in Taskloom.
          </p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-emerald-900/70 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-300">
          <ShieldCheck size={15} /> Authentication delegated to GitHub CLI
        </div>
      </div>

      {(notice || error) && (
        <p role={error ? "alert" : "status"} className={`rounded-xl border px-4 py-3 text-sm ${
          error ? "border-rose-900 bg-rose-950/40 text-rose-300" : "border-emerald-900 bg-emerald-950/30 text-emerald-300"
        }`}>{error ?? notice}</p>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,.65fr)]">
        <div className="space-y-5">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
            <div className="mb-5 flex items-center gap-3">
              <span className="rounded-xl bg-slate-800 p-2 text-slate-100"><Github size={21} /></span>
              <div>
                <h3 className="font-semibold">Connect GitHub Issues</h3>
                <p className="text-sm text-slate-400">Use the account already authenticated with <code>gh auth login</code>.</p>
              </div>
            </div>
            <form onSubmit={connect} className="grid gap-4 lg:grid-cols-[1fr_220px_auto] lg:items-end">
              <label className="grid gap-1.5 text-sm font-medium">
                Repository
                <input required value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="owner/repository" className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 font-mono text-sm outline-none focus:border-violet-400" />
              </label>
              <label className="grid gap-1.5 text-sm font-medium">
                Sync direction
                <select value={syncDirection} onChange={(event) => setSyncDirection(event.target.value as SyncDirection)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm outline-none focus:border-violet-400">
                  <option value="bidirectional">Two-way</option>
                  <option value="inbound">Import only</option>
                  <option value="outbound">Complete only</option>
                </select>
              </label>
              <button disabled={busy !== null} className="flex items-center justify-center gap-2 rounded-lg bg-violet-400 px-4 py-2.5 font-semibold text-slate-950 disabled:opacity-50">
                <PlugZap size={17} /> {busy === "connect" ? "Connecting…" : "Connect"}
              </button>
              <label className="flex items-center gap-2 text-sm text-slate-300 lg:col-span-3">
                <input type="checkbox" checked={autoClose} onChange={(event) => setAutoClose(event.target.checked)} className="h-4 w-4 accent-violet-400" />
                Close linked GitHub Issues when their Taskloom cards are completed
              </label>
            </form>
          </div>

          <div>
            <h3 className="mb-3 flex items-center gap-2 text-lg font-semibold"><PlugZap size={18} className="text-cyan-400" /> Connections <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">{bridge.providerConnections.length}</span></h3>
            {bridge.providerConnections.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-800 px-6 py-12 text-center text-sm text-slate-500">Connect a repository to import GitHub Issues as governed Taskloom cards.</div>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {bridge.providerConnections.map((connection) => (
                  <article key={connection.id} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h4 className="flex items-center gap-2 font-semibold"><Github size={17} /> {connection.repository}</h4>
                        <p className="mt-1 text-xs capitalize text-slate-500">{connection.syncDirection.replace("bidirectional", "two-way")} · auto-close {connection.autoClose ? "on" : "off"}</p>
                      </div>
                      <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${connection.status === "connected" ? "bg-emerald-950 text-emerald-300" : connection.status === "error" ? "bg-rose-950 text-rose-300" : "bg-slate-800 text-slate-300"}`}>{connection.status.replace("_", " ")}</span>
                    </div>
                    {connection.error && <p className="mt-3 text-xs text-rose-300">{connection.error}</p>}
                    {connection.lastSyncAt && <p className="mt-3 text-xs text-slate-500">Last sync {new Date(connection.lastSyncAt).toLocaleString()}</p>}
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button disabled={busy !== null} onClick={() => void run(`test:${connection.id}`, async () => { await bridge.testProviderConnection(connection.id); setNotice(`${connection.repository} is reachable.`); })} className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold hover:bg-slate-800 disabled:opacity-50"><RefreshCw size={14} /> Test</button>
                      {connection.syncDirection !== "outbound" && <button disabled={busy !== null || connection.status !== "connected"} onClick={() => void run(`import:${connection.id}`, async () => { const result = await bridge.syncProviderInbound(connection.id); setNotice(`Imported ${result.imported}, updated ${result.updated}, unchanged ${result.unchanged}.`); })} className="flex items-center gap-1.5 rounded-lg bg-cyan-400 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-50"><ArrowDownToLine size={14} /> Import issues</button>}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </div>

        <aside className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <h3 className="mb-4 flex items-center gap-2 font-semibold"><History size={18} className="text-violet-300" /> Sync history</h3>
          {bridge.syncEvents.length === 0 ? <p className="py-10 text-center text-sm text-slate-500">Connection tests and sync runs will appear here.</p> : (
            <ol className="space-y-3">
              {bridge.syncEvents.slice(0, 12).map((event) => (
                <li key={event.id} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                  <div className="flex items-start gap-2">
                    {event.status === "completed" ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-400" /> : event.status === "conflict" || event.status === "failed" ? <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-400" /> : <RefreshCw size={16} className="mt-0.5 shrink-0 text-cyan-400" />}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-slate-200">{event.message}</p>
                      <p className="mt-1 text-xs capitalize text-slate-500">{event.direction} · {event.status}{event.attemptCount ? ` · attempt ${event.attemptCount}` : ""}</p>
                    </div>
                  </div>
                  {event.status === "conflict" && event.taskId && <button onClick={() => { if (window.confirm("Close the GitHub Issue despite its newer remote changes?")) void run(`force:${event.id}`, async () => { await bridge.syncTaskOutbound(event.taskId!, true); setNotice("Conflict overridden and outbound sync retried."); }); }} className="mt-3 flex items-center gap-1.5 text-xs font-semibold text-amber-300 hover:text-amber-200"><ExternalLink size={13} /> Close anyway</button>}
                </li>
              ))}
            </ol>
          )}
        </aside>
      </div>
    </section>
  );
}
