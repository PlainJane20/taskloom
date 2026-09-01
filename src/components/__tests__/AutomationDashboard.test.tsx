import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AgentBridge } from "../../hooks/useAgentBridge";
import { AutomationDashboard } from "../AutomationDashboard";

const bridge = {
  status: "connected",
  tasks: [],
  approval: null,
  planApproval: null,
  agents: [
    { id: "planner", name: "Planner", role: "Plans work", instructions: "Plan", provider: "ollama", capabilities: ["analysis"] },
    { id: "builder", name: "Builder", role: "Builds files", instructions: "Build", provider: "ollama", capabilities: ["file_edit"] },
  ],
  workflows: [{
    id: "delivery", name: "Safe delivery", description: "Plan and implement", approvalMode: "approve_plan", enabled: true, archived: false,
    steps: [
      { id: "plan", name: "Plan", agentId: "planner", kind: "analysis", instruction: "Plan", dependsOn: [], command: [], timeoutSeconds: 120 },
      { id: "build", name: "Build", agentId: "builder", kind: "file_edit", instruction: "Build", dependsOn: ["plan"], command: [], timeoutSeconds: 120 },
      { id: "validate", name: "Validate", agentId: "builder", kind: "validate", instruction: "Validate", dependsOn: ["build"], command: [], timeoutSeconds: 120 },
    ],
  }],
  workflowRuns: [],
  triggers: [],
  fileTriggers: [],
  error: null,
  send: vi.fn(), createTask: vi.fn(), runTask: vi.fn(), decideApproval: vi.fn(),
  decidePlanApproval: vi.fn(), createAgent: vi.fn(), createWorkflow: vi.fn(),
  updateWorkflow: vi.fn(), duplicateWorkflow: vi.fn(), setWorkflowEnabled: vi.fn(),
  archiveWorkflow: vi.fn(), runWorkflow: vi.fn(), retryWorkflow: vi.fn(),
  cancelWorkflow: vi.fn(), createTrigger: vi.fn(), setTriggerEnabled: vi.fn(),
  runTriggerNow: vi.fn(), deleteTrigger: vi.fn(), refresh: vi.fn(),
  createFileTrigger: vi.fn(), setFileTriggerEnabled: vi.fn(), deleteFileTrigger: vi.fn(),
} as unknown as AgentBridge;

describe("AutomationDashboard", () => {
  it("shows the agent team and opens a workflow run form", async () => {
    render(<AutomationDashboard bridge={bridge} />);

    expect(screen.getAllByText("Planner").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Builder").length).toBeGreaterThan(0);
    expect(screen.getByText("Safe delivery")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));
    expect(screen.getByRole("heading", { name: /run safe delivery/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/goal/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/target file/i)).toBeInTheDocument();
  });

  it("creates a durable workflow schedule", async () => {
    const user = userEvent.setup();
    render(<AutomationDashboard bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: /schedule/i }));
    expect(screen.getByRole("heading", { name: /schedule safe delivery/i })).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^goal/i), "Refresh the project report");
    await user.type(screen.getByLabelText(/target file/i), "scratch/report.md");
    await user.click(screen.getByRole("button", { name: /create schedule/i }));

    expect(bridge.createTrigger).toHaveBeenCalledWith(expect.objectContaining({
      workflowId: "delivery", intervalMinutes: 60, goal: "Refresh the project report",
      targetFile: "scratch/report.md", enabled: true,
    }));
  });

  it("creates a guarded filesystem watch", async () => {
    const user = userEvent.setup();
    render(<AutomationDashboard bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: /watch files/i }));
    expect(screen.getByRole("heading", { name: /watch files for safe delivery/i })).toBeInTheDocument();
    await user.type(screen.getByLabelText(/file or folder/i), "inbox");
    fireEvent.change(screen.getByLabelText(/^goal/i), {
      target: { value: "Review changed file: {file}" },
    });
    await user.click(screen.getByRole("button", { name: /create file watch/i }));

    expect(bridge.createFileTrigger).toHaveBeenCalledWith({
      workflowId: "delivery", name: "Safe delivery file watch", watchPath: "inbox",
      pattern: "**/*", cooldownSeconds: 30, goal: "Review changed file: {file}",
      enabled: true,
    });
  });

  it("shows and pauses a persistent file watch", async () => {
    const user = userEvent.setup();
    const bridgeWithWatch = {
      ...bridge,
      fileTriggers: [{
        id: "watch-1", workflowId: "delivery", name: "Inbox watch", watchPath: "inbox",
        pattern: "**/*.md", cooldownSeconds: 30, goal: "Review {file}", enabled: true,
        trackedFiles: 4,
      }],
    } as unknown as AgentBridge;
    render(<AutomationDashboard bridge={bridgeWithWatch} />);

    expect(screen.getByText("Inbox watch")).toBeInTheDocument();
    expect(screen.getByText(/tracking 4 files/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /pause watch/i }));

    expect(bridge.setFileTriggerEnabled).toHaveBeenCalledWith("watch-1", false);
  });

  it("configures a validation command without shell parsing", async () => {
    const user = userEvent.setup();
    render(<AutomationDashboard bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: /edit/i }));
    const command = screen.getByLabelText(/validation command for validate/i);
    const timeout = screen.getByLabelText(/timeout for validate/i);
    await user.type(command, "npm\ntest\n--\n--runInBand");
    await user.clear(timeout);
    await user.type(timeout, "240");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    expect(bridge.updateWorkflow).toHaveBeenCalledWith(expect.objectContaining({
      steps: expect.arrayContaining([
        expect.objectContaining({
          id: "validate", command: ["npm", "test", "--", "--runInBand"],
          timeoutSeconds: 240,
        }),
      ]),
    }));
  });

  it("shows captured validation output in durable run history", () => {
    const bridgeWithRun = {
      ...bridge,
      workflowRuns: [{
        id: "run-1", workflowId: "delivery", goal: "Validate release",
        targetFile: "README.md", status: "completed", planApproved: true,
        steps: [{
          id: "run-1:validate", workflowRunId: "run-1", stepId: "validate",
          agentId: "builder", name: "Validate", kind: "validate", status: "completed",
          output: "7 tests passed\n",
        }],
        events: [],
      }],
    } as unknown as AgentBridge;

    render(<AutomationDashboard bridge={bridgeWithRun} />);

    expect(screen.getByText(/validation output · validate/i)).toBeInTheDocument();
    expect(screen.getByText(/7 tests passed/i)).toBeInTheDocument();
  });
});
