import { Check, Route, ShieldCheck, X } from "lucide-react";
import type { PlanApprovalRequest } from "../types";

interface PlanApprovalModalProps {
  request: PlanApprovalRequest;
  onDecision: (decision: "approve" | "reject") => void | Promise<void>;
  busy?: boolean;
}

export function PlanApprovalModal({ request, onDecision, busy = false }: PlanApprovalModalProps) {
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="plan-approval-title" className="fixed inset-0 z-50 grid place-items-center bg-slate-950/85 p-5 backdrop-blur-sm">
      <div className="w-full max-w-2xl rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-violet-400/10 p-3 text-violet-300"><Route /></div>
          <div>
            <h2 id="plan-approval-title" className="text-xl font-bold">Approve automation plan</h2>
            <p className="mt-1 text-sm text-slate-400">{request.summary}</p>
          </div>
        </div>

        <div className="mt-5 rounded-xl border border-slate-700 bg-slate-950/60 p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Goal</p>
          <p className="mt-1 text-sm text-slate-200">{request.goal}</p>
          <p className="mt-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Target</p>
          <code className="mt-1 block text-sm text-cyan-300">{request.targetFile}</code>
        </div>

        <ol className="mt-5 space-y-2">
          {request.steps.map((step, index) => (
            <li key={`${step.name}-${index}`} className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-violet-400/10 text-xs font-bold text-violet-300">{index + 1}</span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-slate-200">{step.name}</p>
                <p className="text-xs text-slate-500">{step.agentName} · {step.kind.replace("_", " ")}</p>
              </div>
            </li>
          ))}
        </ol>

        <div className="mt-5 flex items-center gap-2 rounded-lg border border-amber-800/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
          <ShieldCheck size={16} /> Approved file writes are still snapshotted and confined to the workspace.
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button disabled={busy} onClick={() => void onDecision("reject")} className="flex items-center gap-2 rounded-lg border border-slate-600 px-4 py-2 text-sm font-semibold hover:bg-slate-800 disabled:opacity-50"><X size={16} /> Reject plan</button>
          <button disabled={busy} onClick={() => void onDecision("approve")} className="flex items-center gap-2 rounded-lg bg-violet-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-violet-300 disabled:opacity-50"><Check size={16} /> Approve &amp; run</button>
        </div>
      </div>
    </div>
  );
}
