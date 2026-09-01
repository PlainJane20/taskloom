import { render, screen } from "@testing-library/react";
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
    id: "delivery", name: "Safe delivery", description: "Plan and implement", approvalMode: "approve_plan", enabled: true,
    steps: [
      { id: "plan", name: "Plan", agentId: "planner", kind: "analysis", instruction: "Plan", dependsOn: [] },
      { id: "build", name: "Build", agentId: "builder", kind: "file_edit", instruction: "Build", dependsOn: ["plan"] },
    ],
  }],
  workflowRuns: [],
  error: null,
  send: vi.fn(), createTask: vi.fn(), runTask: vi.fn(), decideApproval: vi.fn(),
  decidePlanApproval: vi.fn(), createAgent: vi.fn(), createWorkflow: vi.fn(),
  runWorkflow: vi.fn(), cancelWorkflow: vi.fn(), refresh: vi.fn(),
} as unknown as AgentBridge;

describe("AutomationDashboard", () => {
  it("shows the agent team and opens a workflow run form", async () => {
    render(<AutomationDashboard bridge={bridge} />);

    expect(screen.getAllByText("Planner").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Builder").length).toBeGreaterThan(0);
    expect(screen.getByText("Safe delivery")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(screen.getByRole("heading", { name: /run safe delivery/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/goal/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/target file/i)).toBeInTheDocument();
  });
});
