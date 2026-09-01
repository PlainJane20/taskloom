import { useState, type FormEvent } from "react";
import {
  Activity, Bot, CheckCircle2, Circle, CircleStop, Clock3, Plus, Route,
  ShieldCheck, Sparkles, X, XCircle,
} from "lucide-react";
import type { AgentBridge } from "../hooks/useAgentBridge";
import type { AgentCapability, ApprovalMode, Workflow, WorkflowRun } from "../types";
import { ApprovalModal, type ApprovalDecisionPayload } from "./ApprovalModal";
import { PlanApprovalModal } from "./PlanApprovalModal";

const modeLabels: Record<ApprovalMode, { label: string; tone: string; description: string }> = {
  observe: { label: "Observe", tone: "text-sky-300 bg-sky-400/10", description: "Generate results without writing files." },
  approve_changes: { label: "Approve changes", tone: "text-amber-300 bg-amber-400/10", description: "Review each proposed file mutation." },
  approve_plan: { label: "Approve plan", tone: "text-violet-300 bg-violet-400/10", description: "Approve once, then run the guarded plan." },
  trusted: { label: "Trusted", tone: "text-emerald-300 bg-emerald-400/10", description: "Run automatically with snapshots and path guards." },
};

function runTone(status: WorkflowRun["status"]): string {
  if (status === "completed") return "text-emerald-300";
  if (status === "failed" || status === "cancelled") return "text-rose-300";
  if (status === "needs_approval") return "text-amber-300";
  return "text-cyan-300";
}

function RunStatusIcon({ status }: { status: WorkflowRun["status"] }) {
  if (status === "completed") return <CheckCircle2 size={16} />;
  if (status === "failed" || status === "cancelled") return <XCircle size={16} />;
  if (status === "running") return <Activity size={16} className="animate-pulse" />;
  if (status === "needs_approval") return <ShieldCheck size={16} />;
  return <Clock3 size={16} />;
}

function WorkflowCard({ workflow, bridge, onRun }: { workflow: Workflow; bridge: AgentBridge; onRun: (workflow: Workflow) => void }) {
  const mode = modeLabels[workflow.approvalMode];
  return (
    <article className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 shadow-lg shadow-slate-950/20">
      <div className="flex items-start justify-between gap-3">
        <div><h3 className="font-bold text-slate-100">{workflow.name}</h3><p className="mt-1 text-sm text-slate-400">{workflow.description}</p></div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${mode.tone}`}>{mode.label}</span>
      </div>
      <div className="mt-5 flex items-center overflow-x-auto pb-1">
        {workflow.steps.map((step, index) => {
          const agent = bridge.agents.find((candidate) => candidate.id === step.agentId);
          return <div key={step.id} className="flex shrink-0 items-center">
            {index > 0 && <div className="mx-2 h-px w-5 bg-slate-700" />}
            <div className="rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2">
              <p className="text-xs font-semibold text-slate-200">{step.name}</p>
              <p className="text-[10px] text-slate-500">{agent?.name ?? "Unknown agent"}</p>
            </div>
          </div>;
        })}
      </div>
      <div className="mt-4 flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">{mode.description}</p>
        <button onClick={() => onRun(workflow)} disabled={bridge.status !== "connected"} className="flex shrink-0 items-center gap-2 rounded-lg bg-cyan-400 px-3 py-2 text-xs font-bold text-slate-950 hover:bg-cyan-300 disabled:opacity-40"><Sparkles size={15} /> Run workflow</button>
      </div>
    </article>
  );
}

export function AutomationDashboard({ bridge }: { bridge: AgentBridge }) {
  const [showAgentForm, setShowAgentForm] = useState(false);
  const [showWorkflowForm, setShowWorkflowForm] = useState(false);
  const [runTarget, setRunTarget] = useState<Workflow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function submitAgent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const capabilities = data.getAll("capabilities") as AgentCapability[];
    await perform(async () => {
      await bridge.createAgent({
        name: String(data.get("name")), role: String(data.get("role")),
        instructions: String(data.get("instructions")),
        provider: String(data.get("provider")) as "ollama" | "openai",
        capabilities: capabilities.length ? capabilities : ["analysis"],
      });
      setShowAgentForm(false);
    });
  }

  async function submitWorkflow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const planner = String(data.get("planner"));
    const builder = String(data.get("builder"));
    const reviewer = String(data.get("reviewer"));
    await perform(async () => {
      await bridge.createWorkflow({
        name: String(data.get("name")), description: String(data.get("description")),
        approvalMode: String(data.get("approvalMode")) as ApprovalMode,
        steps: [
          { id: "plan", name: "Plan", agentId: planner, kind: "analysis", instruction: "Create a concise execution plan.", dependsOn: [] },
          { id: "implement", name: "Implement", agentId: builder, kind: "file_edit", instruction: "Implement the goal in the target file.", dependsOn: ["plan"] },
          { id: "validate", name: "Validate", agentId: reviewer, kind: "validate", instruction: "Verify the target file exists and is not empty.", dependsOn: ["implement"] },
          { id: "review", name: "Review", agentId: reviewer, kind: "analysis", instruction: "Review the result and summarize remaining risks.", dependsOn: ["validate"] },
        ],
      });
      setShowWorkflowForm(false);
    });
  }

  async function submitRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!runTarget) return;
    const data = new FormData(event.currentTarget);
    await perform(async () => {
      await bridge.runWorkflow(runTarget.id, String(data.get("goal")), String(data.get("targetFile")));
      setRunTarget(null);
    });
  }

  async function decideFile(message: ApprovalDecisionPayload) {
    await perform(() => bridge.decideApproval(message.payload.requestId, message.payload.decision));
  }

  return (
    <section className="mx-auto max-w-[1600px] space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div><h2 className="text-xl font-bold">Automation studio</h2><p className="mt-1 text-sm text-slate-500">Coordinate specialized agents with explicit dependencies and risk-based policies.</p></div>
        <div className="flex gap-2">
          <button onClick={() => setShowAgentForm(true)} className="flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-sm font-semibold hover:bg-slate-900"><Bot size={16} /> New agent</button>
          <button onClick={() => setShowWorkflowForm(true)} className="flex items-center gap-2 rounded-lg bg-violet-400 px-3 py-2 text-sm font-bold text-slate-950 hover:bg-violet-300"><Plus size={16} /> New workflow</button>
        </div>
      </div>
      {error && <p role="alert" className="rounded-lg border border-rose-900 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">{error}</p>}

      <div>
        <div className="mb-3 flex items-center gap-2"><Bot size={17} className="text-cyan-300" /><h3 className="font-semibold">Agent team</h3><span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs text-slate-400">{bridge.agents.length}</span></div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {bridge.agents.map((agent) => <article key={agent.id} className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="flex items-center justify-between"><h4 className="font-semibold">{agent.name}</h4><span className="h-2 w-2 rounded-full bg-emerald-400" /></div>
            <p className="mt-1 text-xs text-slate-400">{agent.role}</p>
            <div className="mt-3 flex flex-wrap gap-1">{agent.capabilities.map((item) => <span key={item} className="rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-400">{item.replace("_", " ")}</span>)}</div>
            <p className="mt-3 text-[11px] text-slate-500">{agent.provider === "ollama" ? "Local · Ollama" : "Cloud · OpenAI"}</p>
          </article>)}
        </div>
      </div>

      <div>
        <div className="mb-3 flex items-center gap-2"><Route size={17} className="text-violet-300" /><h3 className="font-semibold">Workflows</h3></div>
        <div className="grid gap-4 xl:grid-cols-2">{bridge.workflows.map((workflow) => <WorkflowCard key={workflow.id} workflow={workflow} bridge={bridge} onRun={setRunTarget} />)}</div>
      </div>

      <div>
        <div className="mb-3 flex items-center gap-2"><Activity size={17} className="text-emerald-300" /><h3 className="font-semibold">Recent runs</h3></div>
        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/50">
          {bridge.workflowRuns.length === 0 && <p className="px-5 py-8 text-center text-sm text-slate-500">Run a workflow to see its durable execution history.</p>}
          {bridge.workflowRuns.slice(0, 10).map((run) => {
            const workflow = bridge.workflows.find((item) => item.id === run.workflowId);
            return <article key={run.id} className="border-b border-slate-800 px-4 py-4 last:border-0">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="text-sm font-semibold">{workflow?.name ?? "Workflow"}</p><p className="mt-1 max-w-3xl truncate text-xs text-slate-500">{run.goal}</p></div>
                <div className={`flex items-center gap-2 text-xs font-semibold ${runTone(run.status)}`}><RunStatusIcon status={run.status} /> {run.status.replace("_", " ")}</div>
              </div>
              <div className="mt-3 flex items-center gap-1 overflow-x-auto">
                {run.steps.map((step, index) => <div key={step.id} className="flex shrink-0 items-center">
                  {index > 0 && <div className={`mx-1.5 h-px w-5 ${step.status === "completed" ? "bg-emerald-600" : "bg-slate-700"}`} />}
                  <div title={step.error || step.output} className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] ${step.status === "completed" ? "border-emerald-900 bg-emerald-950/30 text-emerald-300" : step.status === "failed" || step.status === "rejected" ? "border-rose-900 bg-rose-950/30 text-rose-300" : step.status === "running" ? "border-cyan-900 bg-cyan-950/30 text-cyan-300" : "border-slate-700 text-slate-500"}`}>
                    {step.status === "completed" ? <CheckCircle2 size={11} /> : <Circle size={11} />} {step.name}
                  </div>
                </div>)}
                {!["completed", "failed", "cancelled"].includes(run.status) && <button aria-label={`Cancel ${workflow?.name ?? "workflow"}`} disabled={busy} onClick={() => void perform(() => bridge.cancelWorkflow(run.id))} className="ml-auto flex items-center gap-1 text-[10px] text-rose-300 hover:text-rose-200"><CircleStop size={13} /> Cancel</button>}
              </div>
              {run.error && <p className="mt-2 text-xs text-rose-300">{run.error}</p>}
            </article>;
          })}
        </div>
      </div>

      {runTarget && <div className="fixed inset-0 z-40 grid place-items-center bg-slate-950/80 p-5">
        <form onSubmit={(event) => void submitRun(event)} className="w-full max-w-xl space-y-4 rounded-2xl border border-slate-700 bg-slate-900 p-6">
          <div className="flex items-start justify-between"><div><h2 className="text-lg font-bold">Run {runTarget.name}</h2><p className="mt-1 text-xs text-slate-500">{modeLabels[runTarget.approvalMode].description}</p></div><button type="button" aria-label="Close" onClick={() => setRunTarget(null)}><X /></button></div>
          <label className="block text-sm">Goal<textarea required name="goal" rows={4} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Add a concise welcome section and preserve the existing style." /></label>
          <label className="block text-sm">Workspace-relative target file<input required name="targetFile" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="scratch/automation-demo.md" /></label>
          <button disabled={busy} className="w-full rounded-lg bg-violet-400 py-2 font-bold text-slate-950 disabled:opacity-50">{busy ? "Starting…" : "Start workflow"}</button>
        </form>
      </div>}

      {showAgentForm && <div className="fixed inset-0 z-40 grid place-items-center bg-slate-950/80 p-5">
        <form onSubmit={(event) => void submitAgent(event)} className="w-full max-w-lg space-y-4 rounded-2xl border border-slate-700 bg-slate-900 p-6">
          <div className="flex justify-between"><h2 className="text-lg font-bold">Create specialized agent</h2><button type="button" aria-label="Close" onClick={() => setShowAgentForm(false)}><X /></button></div>
          <label className="block text-sm">Name<input required name="name" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Security reviewer" /></label>
          <label className="block text-sm">Role<input required name="role" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Reviews changes for security risks" /></label>
          <label className="block text-sm">Instructions<textarea required name="instructions" rows={3} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Identify unsafe paths, leaked secrets, and risky dependencies." /></label>
          <label className="block text-sm">Provider<select name="provider" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2"><option value="ollama">Ollama (local)</option><option value="openai">OpenAI</option></select></label>
          <fieldset><legend className="text-sm">Capabilities</legend><div className="mt-2 flex gap-4 text-xs text-slate-300">{(["analysis", "file_edit", "validate"] as AgentCapability[]).map((item) => <label key={item} className="flex items-center gap-1.5"><input type="checkbox" name="capabilities" value={item} defaultChecked={item === "analysis"} /> {item.replace("_", " ")}</label>)}</div></fieldset>
          <button disabled={busy} className="w-full rounded-lg bg-cyan-400 py-2 font-bold text-slate-950 disabled:opacity-50">Create agent</button>
        </form>
      </div>}

      {showWorkflowForm && <div className="fixed inset-0 z-40 grid place-items-center overflow-auto bg-slate-950/80 p-5">
        <form onSubmit={(event) => void submitWorkflow(event)} className="w-full max-w-lg space-y-4 rounded-2xl border border-slate-700 bg-slate-900 p-6">
          <div className="flex justify-between"><h2 className="text-lg font-bold">Create workflow</h2><button type="button" aria-label="Close" onClick={() => setShowWorkflowForm(false)}><X /></button></div>
          <label className="block text-sm">Name<input required name="name" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Documentation delivery" /></label>
          <label className="block text-sm">Description<input required name="description" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2" placeholder="Plan, implement, validate, and review documentation." /></label>
          <label className="block text-sm">Automation policy<select name="approvalMode" defaultValue="approve_plan" className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2">{Object.entries(modeLabels).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}</select></label>
          {(["planner", "builder", "reviewer"] as const).map((role) => <label key={role} className="block text-sm capitalize">{role}<select required name={role} defaultValue={bridge.agents.find((agent) => agent.id === role)?.id ?? bridge.agents[0]?.id} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2">{bridge.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name} — {agent.role}</option>)}</select></label>)}
          <button disabled={busy || bridge.agents.length === 0} className="w-full rounded-lg bg-violet-400 py-2 font-bold text-slate-950 disabled:opacity-50">Create workflow</button>
        </form>
      </div>}

      {bridge.planApproval && <PlanApprovalModal request={bridge.planApproval} busy={busy} onDecision={(decision) => perform(() => bridge.decidePlanApproval(bridge.planApproval!.requestId, decision))} />}
      {bridge.approval && <ApprovalModal request={bridge.approval} busy={busy} onDecision={decideFile} />}
    </section>
  );
}
