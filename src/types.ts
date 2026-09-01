export type TaskStatus =
  | "draft"
  | "backlog"
  | "active"
  | "blocked"
  | "needs_approval"
  | "completed"
  | "failed"
  | "cancelled";
export type GovernanceState = "accepted" | "pending_review" | "rejected";
export type AgentSessionStatus = "active" | "waiting_for_human" | "error_stuck" | "idle" | "completed";

export interface TaskLink {
  id: string;
  kind: "commit" | "pull_request" | "issue" | string;
  provider?: string | null;
  label?: string | null;
  url?: string | null;
  gitSha?: string | null;
  createdAt: string;
}

export interface ExecutionTrace {
  id: string;
  taskId: string;
  worklogId?: string | null;
  commandExecuted?: string | null;
  stdout: string;
  stderr: string;
  exitCode?: number | null;
  truncated: boolean;
  startedAt?: string | null;
  completedAt?: string | null;
  contentSha256?: string | null;
}

export interface TaskWorklog {
  id: string;
  taskId: string;
  message: string;
  kind: string;
  agentId?: string | null;
  sessionId?: string | null;
  progressCurrent?: number | null;
  progressTotal?: number | null;
  traceId?: string | null;
  createdAt: string;
  trace?: ExecutionTrace | null;
}

export interface AgentSession {
  id: string;
  agentId: string;
  status: AgentSessionStatus;
  branchName?: string | null;
  controlCapabilities: Array<"pause" | "resume" | "kill" | string>;
  startedAt: string;
  lastHeartbeatAt: string;
  completedAt?: string | null;
  error?: string | null;
}

export interface AgentTask {
  id: string;
  title: string;
  prompt: string;
  status: TaskStatus;
  filePath?: string | null;
  provider?: "openai" | "ollama";
  error?: string | null;
  source: "manual" | "workflow" | "mcp" | "provider" | string;
  governanceState: GovernanceState;
  confidenceScore?: number | null;
  agentId?: string | null;
  sessionId?: string | null;
  branchName?: string | null;
  parentTaskId?: string | null;
  clusterKey?: string | null;
  progressCurrent: number;
  progressTotal: number;
  version: number;
  createdAt?: string | null;
  updatedAt?: string | null;
  links: TaskLink[];
  worklogs: TaskWorklog[];
}

export interface ApprovalRequest {
  taskId: string;
  requestId: string;
  filePath: string;
  before: string;
  after: string;
  summary: string;
  workflowRunId?: string;
  stepRunId?: string;
}

export type AgentCapability = "analysis" | "file_edit" | "validate";
export type ApprovalMode = "observe" | "approve_changes" | "approve_plan" | "trusted";
export type WorkflowStatus = "queued" | "running" | "needs_approval" | "completed" | "failed" | "cancelled";
export type StepStatus = WorkflowStatus | "rejected";

export interface AgentProfile {
  id: string;
  name: string;
  role: string;
  instructions: string;
  provider: "openai" | "ollama";
  model?: string;
  capabilities: AgentCapability[];
}

export interface WorkflowStep {
  id: string;
  name: string;
  agentId: string;
  kind: AgentCapability;
  instruction: string;
  dependsOn: string[];
  command: string[];
  timeoutSeconds: number;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  approvalMode: ApprovalMode;
  enabled: boolean;
  archived: boolean;
  steps: WorkflowStep[];
}

export interface ExecutionEvent {
  id: string;
  workflowRunId: string;
  stepRunId?: string;
  type: string;
  message: string;
  createdAt: string;
}

export interface AutomationTrigger {
  id: string;
  workflowId: string;
  name: string;
  intervalMinutes: number;
  goal: string;
  targetFile: string;
  enabled: boolean;
  nextRunAt?: string;
  lastRunAt?: string;
  lastRunId?: string;
  error?: string;
}

export interface FileTrigger {
  id: string;
  workflowId: string;
  name: string;
  watchPath: string;
  pattern: string;
  cooldownSeconds: number;
  goal: string;
  enabled: boolean;
  lastRunAt?: string;
  lastRunId?: string;
  error?: string;
  trackedFiles: number;
}

export type SyncDirection = "inbound" | "outbound" | "bidirectional";
export type ProviderConnectionStatus = "not_tested" | "testing" | "connected" | "error";

export interface ProviderConnection {
  id: string;
  provider: "github" | string;
  repository: string;
  syncDirection: SyncDirection;
  autoClose: boolean;
  enabled: boolean;
  status: ProviderConnectionStatus;
  lastSyncAt?: string | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface SyncEvent {
  id: string;
  connectionId: string;
  direction: "inbound" | "outbound" | "system";
  action: string;
  status: "queued" | "running" | "completed" | "failed";
  message: string;
  taskId?: string | null;
  externalId?: string | null;
  attemptCount: number;
  nextRetryAt?: string | null;
  createdAt: string;
  completedAt?: string | null;
}

export interface StepRun {
  id: string;
  workflowRunId: string;
  stepId: string;
  agentId: string;
  name: string;
  kind: AgentCapability;
  status: StepStatus;
  output: string;
  error?: string;
  startedAt?: string;
  completedAt?: string;
}

export interface WorkflowRun {
  id: string;
  workflowId: string;
  goal: string;
  targetFile: string;
  status: WorkflowStatus;
  currentStep?: string;
  error?: string;
  planApproved: boolean;
  startedAt?: string;
  completedAt?: string;
  steps: StepRun[];
  events: ExecutionEvent[];
}

export interface PlanApprovalRequest {
  requestId: string;
  workflowRunId: string;
  workflowName: string;
  goal: string;
  targetFile: string;
  summary: string;
  steps: Array<{ name: string; agentName: string; kind: AgentCapability }>;
}

export interface BridgeRequest {
  id?: string;
  type: string;
  payload?: Record<string, unknown>;
}

export interface BridgeResponse {
  id?: string;
  type: string;
  ok?: boolean;
  payload?: Record<string, unknown>;
  error?: { code: string; message: string };
}
