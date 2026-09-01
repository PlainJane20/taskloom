import { useCallback, useEffect, useRef, useState } from "react";
import { documentDir, join, resourceDir } from "@tauri-apps/api/path";
import { Command, type Child } from "@tauri-apps/plugin-shell";
import type {
  AgentProfile, AgentTask, ApprovalRequest, BridgeRequest, BridgeResponse,
  PlanApprovalRequest, Workflow, WorkflowRun,
} from "../types";

export type BridgeStatus = "connecting" | "connected" | "error" | "stopped";

export interface AgentBridge {
  status: BridgeStatus;
  tasks: AgentTask[];
  approval: ApprovalRequest | null;
  planApproval: PlanApprovalRequest | null;
  agents: AgentProfile[];
  workflows: Workflow[];
  workflowRuns: WorkflowRun[];
  error: string | null;
  send: (message: BridgeRequest) => Promise<BridgeResponse>;
  createTask: (input: Pick<AgentTask, "title" | "prompt" | "filePath" | "provider">) => Promise<AgentTask>;
  runTask: (taskId: string) => Promise<void>;
  decideApproval: (requestId: string, decision: "approve" | "reject") => Promise<void>;
  decidePlanApproval: (requestId: string, decision: "approve" | "reject") => Promise<void>;
  createAgent: (input: Omit<AgentProfile, "id">) => Promise<AgentProfile>;
  createWorkflow: (input: Omit<Workflow, "id" | "enabled">) => Promise<Workflow>;
  runWorkflow: (workflowId: string, goal: string, targetFile: string) => Promise<void>;
  cancelWorkflow: (workflowRunId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

type PendingRequest = {
  resolve: (value: BridgeResponse) => void;
  reject: (reason: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
};

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function useAgentBridge(): AgentBridge {
  const [status, setStatus] = useState<BridgeStatus>("connecting");
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [planApproval, setPlanApproval] = useState<PlanApprovalRequest | null>(null);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowRuns, setWorkflowRuns] = useState<WorkflowRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const childRef = useRef<Child | null>(null);
  const pendingRef = useRef(new Map<string, PendingRequest>());

  const upsertTask = useCallback((task: AgentTask) => {
    setTasks((current) => {
      const exists = current.some((item) => item.id === task.id);
      return exists ? current.map((item) => item.id === task.id ? task : item) : [...current, task];
    });
  }, []);

  const receive = useCallback((raw: string) => {
    let message: BridgeResponse;
    try {
      message = JSON.parse(raw) as BridgeResponse;
    } catch {
      setError("The engine returned an invalid JSON message.");
      return;
    }

    if (message.type === "approval_required" && message.payload) {
      setApproval(message.payload as unknown as ApprovalRequest);
    }
    if (message.type === "plan_approval_required" && message.payload) {
      setPlanApproval(message.payload as unknown as PlanApprovalRequest);
    }
    if ((message.type === "task_list" || message.type === "state_snapshot") && message.payload) {
      const restoredTasks = message.payload.tasks;
      const restoredApprovals = message.payload.approvals;
      const restoredPlanApprovals = message.payload.planApprovals;
      if (Array.isArray(restoredTasks)) setTasks(restoredTasks as AgentTask[]);
      if (Array.isArray(restoredApprovals)) {
        setApproval((restoredApprovals[0] as ApprovalRequest | undefined) ?? null);
      }
      if (Array.isArray(restoredPlanApprovals)) {
        setPlanApproval((restoredPlanApprovals[0] as PlanApprovalRequest | undefined) ?? null);
      }
      if (Array.isArray(message.payload.agents)) setAgents(message.payload.agents as unknown as AgentProfile[]);
      if (Array.isArray(message.payload.workflows)) setWorkflows(message.payload.workflows as unknown as Workflow[]);
      if (Array.isArray(message.payload.workflowRuns)) setWorkflowRuns(message.payload.workflowRuns as unknown as WorkflowRun[]);
    }
    const task = message.payload?.task as AgentTask | undefined;
    if (task) upsertTask(task);

    if (message.id) {
      const pending = pendingRef.current.get(message.id);
      if (pending) {
        clearTimeout(pending.timeout);
        pendingRef.current.delete(message.id);
        if (message.ok === false) pending.reject(new Error(message.error?.message ?? "Engine request failed"));
        else pending.resolve(message);
      }
    }
  }, [upsertTask]);

  useEffect(() => {
    let disposed = false;
    let child: Child | null = null;

    async function start() {
      if (!isTauriRuntime()) {
        setStatus("error");
        setError("The local engine is available in the Tauri desktop app. Run `npm run tauri dev`.");
        return;
      }
      try {
        // Development edits the checked-out repository. Production resolves the
        // bundled engine and confines file operations to a visible user folder.
        const isDevelopment = import.meta.env.DEV;
        const enginePath = isDevelopment
          ? "engine/main.py"
          : await join(await resourceDir(), "engine", "main.py");
        const workspacePath = isDevelopment
          ? "."
          : await join(await documentDir(), "TaskloomWorkspace");
        const command = Command.create(
          "python3",
          ["-u", enginePath, "--workspace", workspacePath],
          isDevelopment ? { cwd: ".." } : undefined,
        );
        command.stdout.on("data", receive);
        command.stderr.on("data", (line) => console.warn(`[engine] ${line}`));
        command.on("error", (message) => {
          setStatus("error");
          setError(`Engine process error: ${message}`);
        });
        command.on("close", ({ code, signal }) => {
          if (!disposed) {
            setStatus("stopped");
            setError(`Taskloom engine stopped unexpectedly (exit code ${code ?? "unknown"}${signal ? `, signal ${signal}` : ""}).`);
          }
        });
        child = await command.spawn();
        if (disposed) {
          await child.kill();
          return;
        }
        childRef.current = child;
        setStatus("connected");
        setError(null);
        await child.write(`${JSON.stringify({ id: "bootstrap-state", type: "list_state", payload: {} })}\n`);
      } catch (cause) {
        setStatus("error");
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    }

    void start();
    return () => {
      disposed = true;
      childRef.current = null;
      if (child) void child.kill();
      for (const pending of pendingRef.current.values()) {
        clearTimeout(pending.timeout);
        pending.reject(new Error("Engine stopped"));
      }
      pendingRef.current.clear();
    };
  }, [receive]);

  const send = useCallback(async (request: BridgeRequest): Promise<BridgeResponse> => {
    const child = childRef.current;
    if (!child) throw new Error("The Taskloom engine is not connected");
    const id = request.id ?? crypto.randomUUID();
    const message = { ...request, id };
    const response = new Promise<BridgeResponse>((resolve, reject) => {
      const timeout = setTimeout(() => {
        pendingRef.current.delete(id);
        reject(new Error(`Engine request '${request.type}' timed out`));
      }, 125_000);
      pendingRef.current.set(id, { resolve, reject, timeout });
    });
    try {
      await child.write(`${JSON.stringify(message)}\n`);
    } catch (cause) {
      const pending = pendingRef.current.get(id);
      if (pending) clearTimeout(pending.timeout);
      pendingRef.current.delete(id);
      throw cause;
    }
    return response;
  }, []);

  const createTask = useCallback(async (input: Pick<AgentTask, "title" | "prompt" | "filePath" | "provider">) => {
    const response = await send({ type: "create_task", payload: input });
    return response.payload?.task as unknown as AgentTask;
  }, [send]);

  const runTask = useCallback(async (taskId: string) => {
    // The engine persists `active` before awaiting the model, but the JSON-lines
    // response arrives only after generation. Reflect that authoritative state
    // immediately so a slow local model never makes the board look frozen.
    setTasks((current) => current.map((task) => (
      task.id === taskId ? { ...task, status: "active", error: undefined } : task
    )));
    try {
      await send({ type: "run_task", payload: { taskId } });
    } catch (cause) {
      // Reload the persisted failed/backlog state before surfacing the error.
      try {
        await send({ type: "list_tasks", payload: {} });
      } catch {
        // Preserve the original provider error if reconciliation also fails.
      }
      throw cause;
    }
  }, [send]);

  const decideApproval = useCallback(async (requestId: string, decision: "approve" | "reject") => {
    await send({ type: "approval_decision", payload: { requestId, decision } });
    setApproval(null);
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const refresh = useCallback(async () => {
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const decidePlanApproval = useCallback(async (requestId: string, decision: "approve" | "reject") => {
    await send({ type: "plan_approval_decision", payload: { requestId, decision } });
    setPlanApproval(null);
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const createAgent = useCallback(async (input: Omit<AgentProfile, "id">) => {
    const response = await send({ type: "create_agent", payload: input });
    await send({ type: "list_state", payload: {} });
    return response.payload?.agent as unknown as AgentProfile;
  }, [send]);

  const createWorkflow = useCallback(async (input: Omit<Workflow, "id" | "enabled">) => {
    const response = await send({ type: "create_workflow", payload: input });
    await send({ type: "list_state", payload: {} });
    return response.payload?.workflow as unknown as Workflow;
  }, [send]);

  const runWorkflow = useCallback(async (workflowId: string, goal: string, targetFile: string) => {
    await send({ type: "run_workflow", payload: { workflowId, goal, targetFile } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const cancelWorkflow = useCallback(async (workflowRunId: string) => {
    await send({ type: "cancel_workflow", payload: { workflowRunId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  return {
    status, tasks, approval, planApproval, agents, workflows, workflowRuns, error, send,
    createTask, runTask, decideApproval, decidePlanApproval, createAgent, createWorkflow,
    runWorkflow, cancelWorkflow, refresh,
  };
}
