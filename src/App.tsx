import { ArchiveRestore, Columns3, PlugZap, Route, Settings2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { AutomationDashboard } from "./components/AutomationDashboard";
import { KanbanBoard } from "./components/KanbanBoard";
import { IntegrationsDashboard } from "./components/IntegrationsDashboard";
import { OnboardingModal } from "./components/OnboardingModal";
import { SettingsDashboard } from "./components/SettingsDashboard";
import { SnapshotsDashboard } from "./components/SnapshotsDashboard";
import { useAgentBridge } from "./hooks/useAgentBridge";
import { loadSettings, saveSettings } from "./settings";
import type { AppSettings } from "./types";
import taskloomIcon from "../src-tauri/icons/128x128.png";

export default function App() {
  const [settings, setSettings] = useState<AppSettings>(() => loadSettings());
  const bridge = useAgentBridge(settings);
  const [view, setView] = useState<"board" | "automations" | "integrations" | "snapshots" | "settings">("automations");

  function updateSettings(next: AppSettings) {
    saveSettings(next);
    setSettings(next);
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-7 text-slate-100">
      <header className="mx-auto mb-5 flex max-w-[1600px] items-center justify-between">
        <div className="flex items-center gap-3">
          <img src={taskloomIcon} alt="Taskloom" className="h-10 w-10 rounded-xl shadow-lg shadow-cyan-950/40" />
          <div>
            <h1 className="text-xl font-bold tracking-tight">Taskloom</h1>
            <p className="text-sm text-slate-400">Local-first visual agent orchestrator</p>
          </div>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs">
          <span className={`h-2 w-2 rounded-full ${bridge.status === "connected" ? "bg-emerald-400" : bridge.status === "error" ? "bg-rose-400" : "bg-amber-400"}`} />
          <ShieldCheck size={14} className="text-slate-400" />
          Engine {bridge.status}
        </div>
      </header>
      <nav aria-label="Workspace views" className="mx-auto mb-7 flex max-w-[1600px] gap-1 rounded-xl border border-slate-800 bg-slate-900/60 p-1">
        <button onClick={() => setView("automations")} className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${view === "automations" ? "bg-violet-400 text-slate-950" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`}><Route size={16} /> Automations</button>
        <button onClick={() => setView("board")} className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${view === "board" ? "bg-cyan-400 text-slate-950" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`}><Columns3 size={16} /> Task board</button>
        <button onClick={() => setView("integrations")} className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${view === "integrations" ? "bg-emerald-400 text-slate-950" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`}><PlugZap size={16} /> Integrations</button>
        <button onClick={() => setView("snapshots")} className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${view === "snapshots" ? "bg-fuchsia-300 text-slate-950" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`}><ArchiveRestore size={16} /> Recovery</button>
        <button onClick={() => setView("settings")} className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition ${view === "settings" ? "bg-amber-300 text-slate-950" : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"}`}><Settings2 size={16} /> Settings</button>
      </nav>
      {bridge.error && <p role="alert" className="mx-auto mb-4 max-w-[1600px] rounded-lg border border-rose-900 bg-rose-950/50 px-4 py-3 text-sm text-rose-300">{bridge.error}</p>}
      {view === "automations" ? <AutomationDashboard bridge={bridge} /> : view === "integrations" ? <IntegrationsDashboard bridge={bridge} /> : view === "snapshots" ? <SnapshotsDashboard bridge={bridge} /> : view === "settings" ? <SettingsDashboard settings={settings} health={bridge.health} bridgeStatus={bridge.status} onSave={updateSettings} onCheck={bridge.runHealthCheck} onReplayWelcome={() => updateSettings({ ...settings, onboardingComplete: false })} /> : <KanbanBoard bridge={bridge} defaultProvider={settings.defaultProvider} />}
      {!settings.onboardingComplete && <OnboardingModal
        settings={settings}
        health={bridge.health}
        onComplete={() => updateSettings({ ...settings, onboardingComplete: true })}
        onCustomize={() => {
          updateSettings({ ...settings, onboardingComplete: true });
          setView("settings");
        }}
      />}
    </main>
  );
}
