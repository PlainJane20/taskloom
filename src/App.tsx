import { ShieldCheck } from "lucide-react";
import { KanbanBoard } from "./components/KanbanBoard";
import { useAgentBridge } from "./hooks/useAgentBridge";
import taskloomIcon from "../src-tauri/icons/128x128.png";

export default function App() {
  const bridge = useAgentBridge();

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-7 text-slate-100">
      <header className="mx-auto mb-7 flex max-w-[1600px] items-center justify-between">
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
      {bridge.error && <p role="alert" className="mx-auto mb-4 max-w-[1600px] rounded-lg border border-rose-900 bg-rose-950/50 px-4 py-3 text-sm text-rose-300">{bridge.error}</p>}
      <KanbanBoard bridge={bridge} />
    </main>
  );
}
