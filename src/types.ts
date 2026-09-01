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
