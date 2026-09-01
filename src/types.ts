export type TaskStatus = "backlog" | "active" | "needs_approval" | "completed" | "failed";

export interface AgentTask {
  id: string;
  title: string;
  prompt: string;
  status: TaskStatus;
  filePath?: string;
  provider?: "openai" | "ollama";
  error?: string;
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
