import { diffLines, type Change } from "diff";
import { Check, FileDiff, X } from "lucide-react";
import type { ApprovalRequest } from "../types";

export interface ApprovalDecisionPayload {
  type: "approval_decision";
  payload: { requestId: string; decision: "approve" | "reject" };
}

interface ApprovalModalProps {
  request: ApprovalRequest;
  onDecision: (message: ApprovalDecisionPayload) => void | Promise<void>;
  busy?: boolean;
}

function DiffPane({ title, changes, side }: { title: string; changes: Change[]; side: "before" | "after" }) {
  const visible = changes.filter((change) => side === "before" ? !change.added : !change.removed);
  return (
    <section className="min-w-0 flex-1 overflow-hidden rounded-lg border border-slate-700 bg-slate-950">
      <h3 className="border-b border-slate-700 bg-slate-900 px-4 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400">{title}</h3>
      <pre className="max-h-[50vh] overflow-auto p-3 text-xs leading-5 text-slate-300">
        {visible.length === 0 && <span className="text-slate-600">(empty file)</span>}
        {visible.map((change, index) => (
          <span
            key={`${index}-${change.value.length}`}
            className={`block whitespace-pre-wrap ${change.added ? "bg-emerald-950/60 text-emerald-200" : change.removed ? "bg-rose-950/60 text-rose-200" : ""}`}
          >{change.value}</span>
        ))}
      </pre>
    </section>
  );
}

export function ApprovalModal({ request, onDecision, busy = false }: ApprovalModalProps) {
  const changes = diffLines(request.before, request.after);
  const decide = (decision: "approve" | "reject") => onDecision({
    type: "approval_decision",
    payload: { requestId: request.requestId, decision },
  });

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="approval-title" className="fixed inset-0 z-50 grid place-items-center bg-slate-950/80 p-5 backdrop-blur-sm">
      <div className="w-full max-w-6xl rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl">
        <div className="mb-4 flex items-start gap-3">
          <div className="rounded-lg bg-amber-400/10 p-2 text-amber-300"><FileDiff /></div>
          <div>
            <h2 id="approval-title" className="text-lg font-bold">Review agent change</h2>
            <p className="mt-1 text-sm text-slate-400">{request.summary}</p>
            <code className="mt-1 block text-xs text-cyan-300">{request.filePath}</code>
          </div>
        </div>
        <div className="flex flex-col gap-3 md:flex-row">
          <DiffPane title="Before" changes={changes} side="before" />
          <DiffPane title="After" changes={changes} side="after" />
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button disabled={busy} onClick={() => void decide("reject")} className="flex items-center gap-2 rounded-lg border border-slate-600 px-4 py-2 text-sm font-semibold hover:bg-slate-800 disabled:opacity-50"><X size={16} /> Reject</button>
          <button disabled={busy} onClick={() => void decide("approve")} className="flex items-center gap-2 rounded-lg bg-emerald-400 px-4 py-2 text-sm font-bold text-slate-950 hover:bg-emerald-300 disabled:opacity-50"><Check size={16} /> Approve &amp; apply</button>
        </div>
      </div>
    </div>
  );
}
