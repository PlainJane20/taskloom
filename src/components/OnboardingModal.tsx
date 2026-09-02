import { ArrowRight, Bot, CheckCircle2, FolderLock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import taskloomIcon from "../../src-tauri/icons/128x128.png";
import type { AppSettings, HealthReport } from "../types";

export function OnboardingModal({
  settings, health, onComplete, onCustomize,
}: {
  settings: AppSettings;
  health: HealthReport | null;
  onComplete: () => void;
  onCustomize: () => void;
}) {
  const [step, setStep] = useState(0);
  return <div className="fixed inset-0 z-[100] grid place-items-center overflow-y-auto bg-slate-950/95 p-5" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
    <div className="w-full max-w-3xl rounded-3xl border border-slate-700 bg-slate-900 p-7 shadow-2xl shadow-cyan-950/30">
      {step === 0 ? <>
        <img src={taskloomIcon} alt="Taskloom logo" className="h-16 w-16 rounded-2xl shadow-lg shadow-cyan-950/50" />
        <h2 id="onboarding-title" className="mt-5 text-3xl font-bold">Welcome to Taskloom</h2>
        <p className="mt-3 max-w-2xl text-slate-400">Coordinate local AI agents visually while every sensitive file change remains reviewable, reversible, and under your control.</p>
        <div className="mt-7 grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4"><FolderLock className="text-cyan-300" /><h3 className="mt-3 font-bold">Local workspace</h3><p className="mt-1 text-xs leading-5 text-slate-400">Agent file access stays confined to the folder you choose.</p></div>
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4"><Bot className="text-violet-300" /><h3 className="mt-3 font-bold">Multi-agent flows</h3><p className="mt-1 text-xs leading-5 text-slate-400">Planner, Builder, and Reviewer collaborate through governed steps.</p></div>
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4"><ShieldCheck className="text-emerald-300" /><h3 className="mt-3 font-bold">Human approval</h3><p className="mt-1 text-xs leading-5 text-slate-400">Inspect diffs and execution traces before changes are applied.</p></div>
        </div>
        <button autoFocus onClick={() => setStep(1)} className="mt-7 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-400 py-3 font-bold text-slate-950 hover:bg-cyan-300">Check my setup <ArrowRight size={18} /></button>
      </> : <>
        <span className={`inline-flex rounded-2xl p-3 ${health?.ready ? "bg-emerald-950 text-emerald-300" : "bg-amber-950 text-amber-300"}`}><CheckCircle2 size={28} /></span>
        <h2 id="onboarding-title" className="mt-5 text-2xl font-bold">Your local environment</h2>
        <p className="mt-2 text-sm text-slate-400">Taskloom uses <strong className="text-slate-200">{settings.defaultProvider === "ollama" ? `${settings.ollamaModel} through Ollama` : settings.openaiModel}</strong> by default.</p>
        <div className="mt-5 space-y-2">
          {(health?.checks ?? []).filter((check) => check.required).map((check) => <div key={check.id} className="flex items-center justify-between gap-4 rounded-lg border border-slate-800 bg-slate-950/60 px-4 py-3"><span className="text-sm font-semibold">{check.label}</span><span className={`text-xs font-bold uppercase ${check.status === "ready" ? "text-emerald-300" : "text-amber-300"}`}>{check.status}</span></div>)}
          {!health && <p className="rounded-lg border border-slate-800 px-4 py-5 text-center text-sm text-slate-500">Connecting to the local engine…</p>}
        </div>
        <div className="mt-7 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <button onClick={onCustomize} className="rounded-xl border border-slate-700 px-5 py-3 text-sm font-semibold hover:bg-slate-800">Customize settings</button>
          <button onClick={onComplete} className="rounded-xl bg-emerald-400 px-5 py-3 text-sm font-bold text-slate-950 hover:bg-emerald-300">Use recommended setup</button>
        </div>
      </>}
    </div>
  </div>;
}
