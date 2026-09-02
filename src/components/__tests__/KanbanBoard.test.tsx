import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AgentBridge } from "../../hooks/useAgentBridge";
import type { AgentTask } from "../../types";
import { KanbanBoard } from "../KanbanBoard";

const openExternal = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock("@tauri-apps/plugin-shell", () => ({ open: openExternal }));

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
    runTask: vi.fn(), completeTask: vi.fn().mockResolvedValue([]), controlSession: vi.fn(), decideApproval: vi.fn(), decidePlanApproval: vi.fn(),
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
    expect(screen.getByRole("dialog", { name: /execution history/i })).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByText("12 tests passed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /close execution history/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("navigates every execution trace attached to a task", async () => {
    const user = userEvent.setup();
    render(<KanbanBoard bridge={bridge([task({
      worklogs: [
        {
          id: "log-1", taskId: "task-1", message: "Compiled", kind: "command", createdAt: "first",
          traceId: "trace-1", trace: { id: "trace-1", taskId: "task-1", commandExecuted: "npm run build", stdout: "build passed", stderr: "", exitCode: 0, truncated: false },
        },
        {
          id: "log-2", taskId: "task-1", message: "Tested", kind: "command", createdAt: "second",
          traceId: "trace-2", trace: { id: "trace-2", taskId: "task-1", commandExecuted: "npm test", stdout: "tests passed", stderr: "", exitCode: 0, truncated: false },
        },
      ],
    })])} />);

    await user.click(screen.getByRole("button", { name: /view 2 execution traces/i }));
    expect(screen.getByText("npm test")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /previous execution trace/i }));
    expect(screen.getByText("npm run build")).toBeInTheDocument();
    expect(screen.getByText("Compiled")).toBeInTheDocument();
  });

  it("sends cooperative pause and stop controls only for capable sessions", async () => {
    const user = userEvent.setup();
    const testBridge = bridge([task({})]);
    render(<KanbanBoard bridge={testBridge} />);

    await user.click(screen.getByRole("button", { name: /pause session-a/i }));
    expect(testBridge.controlSession).toHaveBeenCalledWith("session-a", "pause");
    await user.click(screen.getByRole("button", { name: /stop session-a/i }));
    expect(screen.getByRole("dialog", { name: /stop this agent session/i })).toBeInTheDocument();
    expect(testBridge.controlSession).not.toHaveBeenCalledWith("session-a", "kill");
    await user.click(screen.getByRole("button", { name: /^stop agent$/i }));
    expect(testBridge.controlSession).toHaveBeenCalledWith("session-a", "kill");
  });

  it("keeps task-level controls available outside session swimlanes", async () => {
    const user = userEvent.setup();
    const testBridge = bridge([task({})]);
    render(<KanbanBoard bridge={testBridge} />);

    await user.selectOptions(screen.getByRole("combobox", { name: /group task swimlanes/i }), "none");
    await user.click(screen.getByRole("button", { name: /pause agent for governed task/i }));
    expect(testBridge.controlSession).toHaveBeenCalledWith("session-a", "pause");
  });

  it("confirms and completes an imported issue through the governed outbound path", async () => {
    const user = userEvent.setup();
    const imported = task({
      title: "Taskloom two-way sync test", status: "backlog", source: "provider",
      filePath: null, agentId: null, sessionId: null,
      links: [{
        id: "issue-1", kind: "issue", provider: "github",
        label: "PlainJane20/taskloom#1", url: "https://github.com/PlainJane20/taskloom/issues/1",
        createdAt: "now",
      }],
    });
    const testBridge = bridge([imported]);
    testBridge.completeTask = vi.fn().mockResolvedValue([{
      id: "sync-1", connectionId: "github-main", direction: "outbound",
      action: "close_issue", status: "completed", message: "Closed PlainJane20/taskloom#1",
      taskId: imported.id, externalId: "1", attemptCount: 1, createdAt: "now",
    }]);
    render(<KanbanBoard bridge={testBridge} />);

    await user.click(screen.getByRole("button", { name: /mark taskloom two-way sync test complete/i }));
    const dialog = screen.getByRole("dialog", { name: /complete linked task/i });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("PlainJane20/taskloom#1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^mark complete$/i }));

    expect(testBridge.completeTask).toHaveBeenCalledWith(imported.id);
    expect(await screen.findByText("Closed PlainJane20/taskloom#1")).toBeInTheDocument();
  });

  it("opens imported issue links in the system browser when running in Tauri", async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: {}, configurable: true });
    render(<KanbanBoard bridge={bridge([task({
      title: "Imported issue", status: "backlog", source: "provider", filePath: null,
      agentId: null, sessionId: null,
      links: [{
        id: "issue-4", kind: "issue", provider: "github", label: "PlainJane20/taskloom#2",
        url: "https://github.com/PlainJane20/taskloom/issues/2", createdAt: "now",
      }],
    })])} />);

    await user.click(screen.getByRole("link", { name: /PlainJane20\/taskloom#2/i }));

    expect(openExternal).toHaveBeenCalledWith("https://github.com/PlainJane20/taskloom/issues/2");
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  });

  it("keeps provider conflicts visible after a task is completed", async () => {
    const user = userEvent.setup();
    const imported = task({
      title: "Conflicted issue", status: "backlog", source: "provider", filePath: null,
      agentId: null, sessionId: null,
      links: [{
        id: "issue-2", kind: "issue", provider: "github", label: "acme/app#2",
        url: "https://github.com/acme/app/issues/2", createdAt: "now",
      }],
    });
    const testBridge = bridge([imported]);
    testBridge.completeTask = vi.fn().mockResolvedValue([{
      id: "sync-2", connectionId: "github-main", direction: "outbound",
      action: "close_issue", status: "conflict", message: "acme/app#2 changed on GitHub",
      taskId: imported.id, externalId: "2", attemptCount: 1, createdAt: "now",
    }]);
    render(<KanbanBoard bridge={testBridge} />);

    await user.click(screen.getByRole("button", { name: /mark conflicted issue complete/i }));
    await user.click(screen.getByRole("button", { name: /^mark complete$/i }));

    expect(await screen.findByText(
      "Task completed, but provider sync needs attention: acme/app#2 changed on GitHub",
    )).toBeInTheDocument();
  });

  it("cancels imported issue completion without calling the bridge", async () => {
    const user = userEvent.setup();
    const imported = task({
      title: "Imported issue", status: "backlog", source: "provider", filePath: null,
      agentId: null, sessionId: null,
      links: [{
        id: "issue-3", kind: "issue", provider: "github", label: "acme/app#3",
        url: "https://github.com/acme/app/issues/3", createdAt: "now",
      }],
    });
    const testBridge = bridge([imported]);
    render(<KanbanBoard bridge={testBridge} />);

    await user.click(screen.getByRole("button", { name: /mark imported issue complete/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByRole("dialog", { name: /complete linked task/i })).not.toBeInTheDocument();
    expect(testBridge.completeTask).not.toHaveBeenCalled();
  });
});
