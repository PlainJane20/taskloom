import { CheckCircle2, CircleAlert, RefreshCw, RotateCcw, Save, Settings2, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import type { BridgeStatus } from "../hooks/useAgentBridge";
import type { AppSettings, HealthReport } from "../types";

export function SettingsDashboard({
  settings, health, bridgeStatus, onSave, onCheck, onReplayWelcome,
}: {
  settings: AppSettings;
  health: HealthReport | null;
  bridgeStatus: BridgeStatus;
  onSave: (settings: AppSettings) => void;
  onCheck: () => Promise<HealthReport>;
  onReplayWelcome: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setDraft(settings), [settings]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSave({
      ...draft,
      workspacePath: draft.workspacePath.trim(),
      ollamaUrl: draft.ollamaUrl.trim(),
      ollamaModel: draft.ollamaModel.trim(),
      openaiModel: draft.openaiModel.trim(),
      onboardingComplete: true,
    });
    setNotice("Settings saved. Taskloom is reconnecting the local engine.");
  }

  async function checkEnvironment() {
    setChecking(true); setNotice(null);
    try { await onCheck(); }
    catch (cause) { setNotice(cause instanceof Error ? cause.message : String(cause)); }
    finally { setChecking(false); }
  }

  return (
    <section className="mx-auto max-w-[1600px]">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-bold"><Settings2 size={20} className="text-amber-300" /> Settings & health</h2>
          <p className="mt-1 text-sm text-slate-400">Configure the local workspace and model without editing files or shell variables.</p>
        </div>
        <button type="button" onClick={() => void checkEnvironment()} disabled={checking || bridgeStatus !== "connected"} className="flex items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold hover:bg-slate-800 disabled:opacity-40">
          <RefreshCw size={16} className={checking ? "animate-spin" : ""} /> Run health checks
        </button>
      </div>

      {notice && <p role="status" className="mb-5 rounded-lg border border-cyan-900 bg-cyan-950/30 px-4 py-3 text-sm text-cyan-200">{notice}</p>}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,.8fr)]">
        <form onSubmit={submit} className="space-y-5 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
          <div>
            <h3 className="font-bold">Local configuration</h3>
            <p className="mt-1 text-xs text-slate-500">Settings are stored only on this Mac. API keys are never stored here.</p>
          </div>
          <label className="block text-sm font-semibold">Workspace folder
            <input value={draft.workspacePath} onChange={(event) => setDraft({ ...draft, workspacePath: event.target.value })} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm" placeholder="Use Taskloom's recommended workspace" />
            <span className="mt-1 block text-xs font-normal text-slate-500">Leave blank to use Documents/TaskloomWorkspace in packaged builds.</span>
          </label>
          <label className="block text-sm font-semibold">Default AI provider
            <select value={draft.defaultProvider} onChange={(event) => setDraft({ ...draft, defaultProvider: event.target.value as AppSettings["defaultProvider"] })} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2">
              <option value="ollama">Ollama — private and local</option>
              <option value="openai">OpenAI — cloud API</option>
            </select>
          </label>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm font-semibold">Ollama model
              <input required value={draft.ollamaModel} onChange={(event) => setDraft({ ...draft, ollamaModel: event.target.value })} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm" />
            </label>
            <label className="block text-sm font-semibold">OpenAI model
              <input required value={draft.openaiModel} onChange={(event) => setDraft({ ...draft, openaiModel: event.target.value })} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm" />
            </label>
          </div>
          <label className="block text-sm font-semibold">Ollama API endpoint
            <input required type="url" value={draft.ollamaUrl} onChange={(event) => setDraft({ ...draft, ollamaUrl: event.target.value })} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm" />
          </label>
          {draft.defaultProvider === "openai" && <p className="rounded-lg border border-amber-900/70 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">For safety, configure <code>OPENAI_API_KEY</code> in the environment that launches Taskloom. The key is not written to Taskloom storage.</p>}
          <div className="flex flex-wrap gap-3">
            <button className="flex items-center gap-2 rounded-lg bg-amber-300 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-amber-200"><Save size={16} /> Save and reconnect</button>
            <button type="button" onClick={onReplayWelcome} className="flex items-center gap-2 rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold hover:bg-slate-800"><RotateCcw size={16} /> Replay welcome tour</button>
          </div>
        </form>

        <aside className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
          <div className="flex items-center justify-between gap-3">
            <div><h3 className="font-bold">Environment readiness</h3><p className="mt-1 text-xs text-slate-500">Last checked {health ? new Date(health.checkedAt).toLocaleString() : "when the engine connects"}</p></div>
            {health && <span className={`rounded-full border px-3 py-1 text-xs font-bold ${health.ready ? "border-emerald-800 bg-emerald-950 text-emerald-300" : "border-rose-800 bg-rose-950 text-rose-300"}`}>{health.ready ? "READY" : "ACTION NEEDED"}</span>}
          </div>
          {health?.workspace && <p className="mt-4 break-all rounded-lg bg-slate-950 px-3 py-2 font-mono text-xs text-cyan-300">{health.workspace}</p>}
          <div className="mt-4 space-y-3">
            {(health?.checks ?? []).map((check) => <div key={check.id} className="flex gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
              {check.status === "ready" ? <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-emerald-400" /> : check.status === "warning" ? <CircleAlert size={18} className="mt-0.5 shrink-0 text-amber-300" /> : <CircleAlert size={18} className="mt-0.5 shrink-0 text-rose-400" />}
              <div><p className="text-sm font-semibold">{check.label}{!check.required && <span className="ml-2 text-[10px] font-normal uppercase text-slate-600">optional</span>}</p><p className="mt-1 text-xs leading-5 text-slate-400">{check.detail}</p></div>
            </div>)}
            {!health && <div className="grid min-h-48 place-items-center text-center text-sm text-slate-500"><div><ShieldCheck className="mx-auto mb-2" />Waiting for the local engine…</div></div>}
          </div>
        </aside>
      </div>
    </section>
  );
}
