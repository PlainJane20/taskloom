import { useCallback, useEffect, useRef, useState } from "react";
import { documentDir, join, resourceDir } from "@tauri-apps/api/path";
import { Command, type Child } from "@tauri-apps/plugin-shell";
import type {
  AgentProfile, AgentSession, AgentTask, ApprovalRequest, AutomationTrigger, BridgeRequest, BridgeResponse,
  AppSettings, ExternalIssueLink, FileSnapshot, FileTrigger, HealthReport, PlanApprovalRequest, ProviderConnection,
  SnapshotPreview, SnapshotRestoreEvent, SyncDirection, SyncEvent,
  Workflow, WorkflowRun,
} from "../types";
import { DEFAULT_SETTINGS } from "../settings";

export type BridgeStatus = "connecting" | "connected" | "error" | "stopped";

export interface AgentBridge {
  status: BridgeStatus;
  tasks: AgentTask[];
  sessions: AgentSession[];
  approval: ApprovalRequest | null;
  planApproval: PlanApprovalRequest | null;
  agents: AgentProfile[];
  workflows: Workflow[];
  workflowRuns: WorkflowRun[];
  triggers: AutomationTrigger[];
  fileTriggers: FileTrigger[];
  providerConnections: ProviderConnection[];
  syncEvents: SyncEvent[];
  externalIssueLinks: ExternalIssueLink[];
  snapshots: FileSnapshot[];
  snapshotRestoreEvents: SnapshotRestoreEvent[];
  health: HealthReport | null;
  error: string | null;
  send: (message: BridgeRequest) => Promise<BridgeResponse>;
  createTask: (input: Pick<AgentTask, "title" | "prompt" | "filePath" | "provider">) => Promise<AgentTask>;
  editTask: (input: Pick<AgentTask, "id" | "title" | "prompt" | "filePath" | "provider" | "version">) => Promise<AgentTask>;
  archiveTasks: (taskIds: string[]) => Promise<string[]>;
  runTask: (taskId: string) => Promise<void>;
  completeTask: (taskId: string) => Promise<SyncEvent[]>;
  controlSession: (sessionId: string, action: "pause" | "resume" | "kill") => Promise<void>;
  decideApproval: (requestId: string, decision: "approve" | "reject") => Promise<void>;
  decidePlanApproval: (requestId: string, decision: "approve" | "reject") => Promise<void>;
  createAgent: (input: Omit<AgentProfile, "id">) => Promise<AgentProfile>;
  createWorkflow: (input: Omit<Workflow, "id" | "enabled" | "archived">) => Promise<Workflow>;
  updateWorkflow: (workflow: Workflow) => Promise<Workflow>;
  duplicateWorkflow: (workflowId: string) => Promise<Workflow>;
  setWorkflowEnabled: (workflowId: string, enabled: boolean) => Promise<void>;
  archiveWorkflow: (workflowId: string) => Promise<void>;
  runWorkflow: (workflowId: string, goal: string, targetFile: string) => Promise<void>;
  retryWorkflow: (workflowRunId: string) => Promise<void>;
  cancelWorkflow: (workflowRunId: string) => Promise<void>;
  createTrigger: (input: Omit<AutomationTrigger, "id" | "lastRunAt" | "lastRunId" | "error" | "nextRunAt">) => Promise<AutomationTrigger>;
  setTriggerEnabled: (triggerId: string, enabled: boolean) => Promise<void>;
  runTriggerNow: (triggerId: string) => Promise<void>;
  deleteTrigger: (triggerId: string) => Promise<void>;
  createFileTrigger: (input: Omit<FileTrigger, "id" | "lastRunAt" | "lastRunId" | "error" | "trackedFiles">) => Promise<FileTrigger>;
  setFileTriggerEnabled: (triggerId: string, enabled: boolean) => Promise<void>;
  deleteFileTrigger: (triggerId: string) => Promise<void>;
  createProviderConnection: (input: {
    provider: "github"; repository: string; syncDirection: SyncDirection; autoClose: boolean;
    backgroundSyncEnabled: boolean; syncIntervalMinutes: number;
  }) => Promise<ProviderConnection>;
  testProviderConnection: (connectionId: string) => Promise<ProviderConnection>;
  updateProviderConnectionSync: (connectionId: string, backgroundSyncEnabled: boolean, syncIntervalMinutes: number) => Promise<ProviderConnection>;
  syncProviderInbound: (connectionId: string) => Promise<{ imported: number; updated: number; unchanged: number; completed: number; reopened: number }>;
  syncTaskOutbound: (taskId: string, force?: boolean) => Promise<SyncEvent[]>;
  previewSnapshot: (snapshotId: string) => Promise<SnapshotPreview>;
  restoreSnapshot: (snapshotId: string, expectedCurrentSha256: string) => Promise<SnapshotRestoreEvent>;
  refreshSnapshots: () => Promise<void>;
  runHealthCheck: () => Promise<HealthReport>;
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

export function useAgentBridge(settings: AppSettings = DEFAULT_SETTINGS): AgentBridge {
  const [status, setStatus] = useState<BridgeStatus>("connecting");
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [planApproval, setPlanApproval] = useState<PlanApprovalRequest | null>(null);
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [workflowRuns, setWorkflowRuns] = useState<WorkflowRun[]>([]);
  const [triggers, setTriggers] = useState<AutomationTrigger[]>([]);
  const [fileTriggers, setFileTriggers] = useState<FileTrigger[]>([]);
  const [providerConnections, setProviderConnections] = useState<ProviderConnection[]>([]);
  const [syncEvents, setSyncEvents] = useState<SyncEvent[]>([]);
  const [externalIssueLinks, setExternalIssueLinks] = useState<ExternalIssueLink[]>([]);
  const [snapshots, setSnapshots] = useState<FileSnapshot[]>([]);
  const [snapshotRestoreEvents, setSnapshotRestoreEvents] = useState<SnapshotRestoreEvent[]>([]);
  const [health, setHealth] = useState<HealthReport | null>(null);
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
    if (message.type === "health_report" && message.payload?.health) {
      setHealth(message.payload.health as unknown as HealthReport);
    }
    if ((message.type === "task_list" || message.type === "state_snapshot") && message.payload) {
      const restoredTasks = message.payload.tasks;
      const restoredApprovals = message.payload.approvals;
      const restoredPlanApprovals = message.payload.planApprovals;
      if (Array.isArray(restoredTasks)) setTasks(restoredTasks as AgentTask[]);
      if (Array.isArray(message.payload.sessions)) {
        setSessions(message.payload.sessions as unknown as AgentSession[]);
      }
      if (Array.isArray(restoredApprovals)) {
        setApproval((restoredApprovals[0] as ApprovalRequest | undefined) ?? null);
      }
      if (Array.isArray(restoredPlanApprovals)) {
        setPlanApproval((restoredPlanApprovals[0] as PlanApprovalRequest | undefined) ?? null);
      }
      if (Array.isArray(message.payload.agents)) setAgents(message.payload.agents as unknown as AgentProfile[]);
      if (Array.isArray(message.payload.workflows)) setWorkflows(message.payload.workflows as unknown as Workflow[]);
      if (Array.isArray(message.payload.workflowRuns)) setWorkflowRuns(message.payload.workflowRuns as unknown as WorkflowRun[]);
      if (Array.isArray(message.payload.triggers)) setTriggers(message.payload.triggers as unknown as AutomationTrigger[]);
      if (Array.isArray(message.payload.fileTriggers)) setFileTriggers(message.payload.fileTriggers as unknown as FileTrigger[]);
      if (Array.isArray(message.payload.providerConnections)) {
        setProviderConnections(message.payload.providerConnections as unknown as ProviderConnection[]);
      }
      if (Array.isArray(message.payload.syncEvents)) {
        setSyncEvents(message.payload.syncEvents as unknown as SyncEvent[]);
      }
      if (Array.isArray(message.payload.externalIssueLinks)) {
        setExternalIssueLinks(message.payload.externalIssueLinks as unknown as ExternalIssueLink[]);
      }
      if (Array.isArray(message.payload.snapshots)) {
        setSnapshots(message.payload.snapshots as unknown as FileSnapshot[]);
      }
      if (Array.isArray(message.payload.snapshotRestoreEvents)) {
        setSnapshotRestoreEvents(message.payload.snapshotRestoreEvents as unknown as SnapshotRestoreEvent[]);
      }
    }
    if (message.type === "snapshots_listed" && message.payload) {
      if (Array.isArray(message.payload.snapshots)) {
        setSnapshots(message.payload.snapshots as unknown as FileSnapshot[]);
      }
      if (Array.isArray(message.payload.restoreEvents)) {
        setSnapshotRestoreEvents(message.payload.restoreEvents as unknown as SnapshotRestoreEvent[]);
      }
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
        const workspacePath = settings.workspacePath.trim() || (isDevelopment
          ? "."
          : await join(await documentDir(), "TaskloomWorkspace"));
        const command = Command.create(
          "python3",
          ["-u", enginePath, "--workspace", workspacePath],
          {
            ...(isDevelopment ? { cwd: ".." } : {}),
            env: {
              TASKLOOM_DEFAULT_PROVIDER: settings.defaultProvider,
              TASKLOOM_OLLAMA_URL: settings.ollamaUrl,
              TASKLOOM_OLLAMA_MODEL: settings.ollamaModel,
              TASKLOOM_OPENAI_MODEL: settings.openaiModel,
            },
          },
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
        await child.write(`${JSON.stringify({ id: "bootstrap-health", type: "health_check", payload: {} })}\n`);
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
  }, [receive, settings.defaultProvider, settings.ollamaModel, settings.ollamaUrl, settings.openaiModel, settings.workspacePath]);

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

  const editTask = useCallback(async (
    input: Pick<AgentTask, "id" | "title" | "prompt" | "filePath" | "provider" | "version">,
  ) => {
    const response = await send({
      type: "edit_task",
      payload: {
        taskId: input.id,
        title: input.title,
        prompt: input.prompt,
        filePath: input.filePath,
        provider: input.provider,
        expectedVersion: input.version,
      },
    });
    return response.payload?.task as unknown as AgentTask;
  }, [send]);

  const archiveTasks = useCallback(async (taskIds: string[]) => {
    const response = await send({ type: "archive_tasks", payload: { taskIds } });
    return (response.payload?.taskIds as unknown as string[] | undefined) ?? [];
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

  const completeTask = useCallback(async (taskId: string) => {
    const response = await send({ type: "update_task", payload: { taskId, status: "completed" } });
    await send({ type: "list_state", payload: {} });
    return (response.payload?.events as unknown as SyncEvent[] | undefined) ?? [];
  }, [send]);

  const controlSession = useCallback(async (
    sessionId: string, action: "pause" | "resume" | "kill",
  ) => {
    await send({ type: "control_agent_session", payload: { sessionId, action } });
    await send({ type: "list_state", payload: {} });
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

  const createWorkflow = useCallback(async (input: Omit<Workflow, "id" | "enabled" | "archived">) => {
    const response = await send({ type: "create_workflow", payload: input });
    await send({ type: "list_state", payload: {} });
    return response.payload?.workflow as unknown as Workflow;
  }, [send]);

  const updateWorkflow = useCallback(async (workflow: Workflow) => {
    const response = await send({
      type: "update_workflow",
      payload: {
        workflowId: workflow.id, name: workflow.name, description: workflow.description,
        approvalMode: workflow.approvalMode, enabled: workflow.enabled, steps: workflow.steps,
      },
    });
    await send({ type: "list_state", payload: {} });
    return response.payload?.workflow as unknown as Workflow;
  }, [send]);

  const duplicateWorkflow = useCallback(async (workflowId: string) => {
    const response = await send({ type: "duplicate_workflow", payload: { workflowId } });
    await send({ type: "list_state", payload: {} });
    return response.payload?.workflow as unknown as Workflow;
  }, [send]);

  const setWorkflowEnabled = useCallback(async (workflowId: string, enabled: boolean) => {
    await send({ type: "set_workflow_enabled", payload: { workflowId, enabled } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const archiveWorkflow = useCallback(async (workflowId: string) => {
    await send({ type: "archive_workflow", payload: { workflowId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const runWorkflow = useCallback(async (workflowId: string, goal: string, targetFile: string) => {
    await send({ type: "run_workflow", payload: { workflowId, goal, targetFile } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const cancelWorkflow = useCallback(async (workflowRunId: string) => {
    await send({ type: "cancel_workflow", payload: { workflowRunId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const retryWorkflow = useCallback(async (workflowRunId: string) => {
    await send({ type: "retry_workflow", payload: { workflowRunId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const createTrigger = useCallback(async (
    input: Omit<AutomationTrigger, "id" | "lastRunAt" | "lastRunId" | "error" | "nextRunAt">,
  ) => {
    const response = await send({ type: "create_trigger", payload: input });
    await send({ type: "list_state", payload: {} });
    return response.payload?.trigger as unknown as AutomationTrigger;
  }, [send]);

  const setTriggerEnabled = useCallback(async (triggerId: string, enabled: boolean) => {
    await send({ type: "set_trigger_enabled", payload: { triggerId, enabled } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const runTriggerNow = useCallback(async (triggerId: string) => {
    await send({ type: "run_trigger_now", payload: { triggerId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const deleteTrigger = useCallback(async (triggerId: string) => {
    await send({ type: "delete_trigger", payload: { triggerId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const createFileTrigger = useCallback(async (
    input: Omit<FileTrigger, "id" | "lastRunAt" | "lastRunId" | "error" | "trackedFiles">,
  ) => {
    const response = await send({ type: "create_file_trigger", payload: input });
    await send({ type: "list_state", payload: {} });
    return response.payload?.fileTrigger as unknown as FileTrigger;
  }, [send]);

  const setFileTriggerEnabled = useCallback(async (triggerId: string, enabled: boolean) => {
    await send({ type: "set_file_trigger_enabled", payload: { triggerId, enabled } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const deleteFileTrigger = useCallback(async (triggerId: string) => {
    await send({ type: "delete_file_trigger", payload: { triggerId } });
    await send({ type: "list_state", payload: {} });
  }, [send]);

  const createProviderConnection = useCallback(async (input: {
    provider: "github"; repository: string; syncDirection: SyncDirection; autoClose: boolean;
    backgroundSyncEnabled: boolean; syncIntervalMinutes: number;
  }) => {
    const response = await send({ type: "create_provider_connection", payload: input });
    await send({ type: "list_state", payload: {} });
    return response.payload?.connection as unknown as ProviderConnection;
  }, [send]);

  const testProviderConnection = useCallback(async (connectionId: string) => {
    const response = await send({ type: "test_provider_connection", payload: { connectionId } });
    await send({ type: "list_state", payload: {} });
    return response.payload?.connection as unknown as ProviderConnection;
  }, [send]);

  const updateProviderConnectionSync = useCallback(async (
    connectionId: string, backgroundSyncEnabled: boolean, syncIntervalMinutes: number,
  ) => {
    const response = await send({
      type: "update_provider_connection_sync",
      payload: { connectionId, backgroundSyncEnabled, syncIntervalMinutes },
    });
    await send({ type: "list_state", payload: {} });
    return response.payload?.connection as unknown as ProviderConnection;
  }, [send]);

  const syncProviderInbound = useCallback(async (connectionId: string) => {
    const response = await send({ type: "sync_provider_inbound", payload: { connectionId } });
    await send({ type: "list_state", payload: {} });
    return response.payload?.summary as unknown as {
      imported: number; updated: number; unchanged: number; completed: number; reopened: number;
    };
  }, [send]);

  const syncTaskOutbound = useCallback(async (taskId: string, force = false) => {
    const response = await send({ type: "sync_task_outbound", payload: { taskId, force } });
    await send({ type: "list_state", payload: {} });
    return response.payload?.events as unknown as SyncEvent[];
  }, [send]);

  const runHealthCheck = useCallback(async () => {
    const response = await send({ type: "health_check", payload: {} });
    const report = response.payload?.health as unknown as HealthReport;
    setHealth(report);
    return report;
  }, [send]);

  const previewSnapshot = useCallback(async (snapshotId: string) => {
    const response = await send({ type: "preview_snapshot", payload: { snapshotId } });
    return response.payload as unknown as SnapshotPreview;
  }, [send]);

  const restoreSnapshot = useCallback(async (snapshotId: string, expectedCurrentSha256: string) => {
    const response = await send({
      type: "restore_snapshot",
      payload: { snapshotId, expectedCurrentSha256, confirmed: true },
    });
    return response.payload?.restoreEvent as unknown as SnapshotRestoreEvent;
  }, [send]);

  const refreshSnapshots = useCallback(async () => {
    await send({ type: "list_snapshots", payload: {} });
  }, [send]);

  return {
    status, tasks, sessions, approval, planApproval, agents, workflows, workflowRuns, triggers, fileTriggers,
    providerConnections, syncEvents, externalIssueLinks, snapshots, snapshotRestoreEvents, health,
    error, send,
    createTask, editTask, archiveTasks, runTask, completeTask, controlSession, decideApproval, decidePlanApproval, createAgent, createWorkflow,
    updateWorkflow, duplicateWorkflow, setWorkflowEnabled, archiveWorkflow,
    runWorkflow, retryWorkflow, cancelWorkflow, createTrigger, setTriggerEnabled,
    runTriggerNow, deleteTrigger, createFileTrigger, setFileTriggerEnabled, deleteFileTrigger,
    createProviderConnection, testProviderConnection, updateProviderConnectionSync,
    syncProviderInbound,
    syncTaskOutbound,
    previewSnapshot, restoreSnapshot, refreshSnapshots,
    runHealthCheck,
    refresh,
  };
}
