import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AgentBridge } from "../../hooks/useAgentBridge";
import type { AgentTask } from "../../types";
import { KanbanBoard } from "../KanbanBoard";

function task(overrides: Partial<AgentTask>): AgentTask {
  return {
    id: "task-1", title: "Governed task", prompt: "Perform safe work", status: "active",
    filePath: "src/auth/token.ts", provider: "ollama", source: "mcp",
    governanceState: "accepted", confidenceScore: 0.92, agentId: "agent-a",
    sessionId: "session-a", branchName: "feature/auth", progressCurrent: 1,
    progressTotal: 2, version: 1, links: [], worklogs: [], ...overrides,
  };
}

function bridge(tasks: AgentTask[]): AgentBridge {
  return {
    status: "connected", tasks,
    sessions: [
      { id: "session-a", agentId: "agent-a", status: "active", branchName: "feature/auth", controlCapabilities: ["pause", "resume", "kill"], startedAt: "now", lastHeartbeatAt: "now" },
      { id: "session-b", agentId: "agent-b", status: "waiting_for_human", branchName: "feature/auth", controlCapabilities: [], startedAt: "now", lastHeartbeatAt: "now" },
    ],
    approval: null, planApproval: null, agents: [], workflows: [], workflowRuns: [],
    triggers: [], fileTriggers: [], error: null, send: vi.fn(), createTask: vi.fn(),
    runTask: vi.fn(), controlSession: vi.fn(), decideApproval: vi.fn(), decidePlanApproval: vi.fn(),
    createAgent: vi.fn(), createWorkflow: vi.fn(), updateWorkflow: vi.fn(),
    duplicateWorkflow: vi.fn(), setWorkflowEnabled: vi.fn(), archiveWorkflow: vi.fn(),
    runWorkflow: vi.fn(), retryWorkflow: vi.fn(), cancelWorkflow: vi.fn(),
    createTrigger: vi.fn(), setTriggerEnabled: vi.fn(), runTriggerNow: vi.fn(),
    deleteTrigger: vi.fn(), createFileTrigger: vi.fn(), setFileTriggerEnabled: vi.fn(),
    deleteFileTrigger: vi.fn(), refresh: vi.fn(),
  } as unknown as AgentBridge;
}

describe("KanbanBoard governance view", () => {
  it("routes low-confidence work to Drafts and shows session status", () => {
    render(<KanbanBoard bridge={bridge([task({
      status: "draft", governanceState: "pending_review", confidenceScore: 0.3,
    })])} />);

    expect(screen.getByText("Drafts / Pending Review")).toBeInTheDocument();
    expect(screen.getByText("30% confidence")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("warns both swimlanes when distinct agents touch the same directory", () => {
    render(<KanbanBoard bridge={bridge([
      task({ id: "a" }),
      task({ id: "b", agentId: "agent-b", sessionId: "session-b", filePath: "src/auth/user.ts" }),
    ])} />);

    expect(screen.getAllByText(/multiple agents are touching the same directory/i)).toHaveLength(2);
    expect(screen.getAllByText("collision")).toHaveLength(2);
    expect(screen.getByText("waiting for human")).toBeInTheDocument();
  });

  it("opens the exact redacted execution trace from a task card", async () => {
    const user = userEvent.setup();
    render(<KanbanBoard bridge={bridge([task({
      worklogs: [{
        id: "log", taskId: "task-1", message: "Tests", kind: "command", createdAt: "now",
        traceId: "trace", trace: {
          id: "trace", taskId: "task-1", worklogId: "log", commandExecuted: "npm test",
          stdout: "12 tests passed", stderr: "", exitCode: 0, truncated: false,
        },
      }],
    })])} />);

    await user.click(screen.getByRole("button", { name: /view execution trace/i }));
    expect(screen.getByRole("dialog", { name: /execution trace/i })).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByText("12 tests passed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /close execution trace/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends cooperative pause and stop controls only for capable sessions", async () => {
    const user = userEvent.setup();
    const testBridge = bridge([task({})]);
    render(<KanbanBoard bridge={testBridge} />);

    await user.click(screen.getByRole("button", { name: /pause session-a/i }));
    expect(testBridge.controlSession).toHaveBeenCalledWith("session-a", "pause");
    await user.click(screen.getByRole("button", { name: /stop session-a/i }));
    expect(testBridge.controlSession).toHaveBeenCalledWith("session-a", "kill");
  });
});
