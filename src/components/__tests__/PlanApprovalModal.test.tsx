import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PlanApprovalModal } from "../PlanApprovalModal";

const request = {
  requestId: "plan-42",
  workflowRunId: "run-7",
  workflowName: "Safe delivery pipeline",
  goal: "Create a welcome note",
  targetFile: "scratch/welcome.md",
  summary: "Approve 4 steps before Taskloom begins",
  steps: [
    { name: "Plan", agentName: "Planner", kind: "analysis" as const },
    { name: "Implement", agentName: "Builder", kind: "file_edit" as const },
    { name: "Validate", agentName: "Reviewer", kind: "validate" as const },
  ],
};

describe("PlanApprovalModal", () => {
  it("shows the goal, target, and assigned agent plan", () => {
    render(<PlanApprovalModal request={request} onDecision={vi.fn()} />);

    expect(screen.getByText("Create a welcome note")).toBeInTheDocument();
    expect(screen.getByText("scratch/welcome.md")).toBeInTheDocument();
    expect(screen.getByText(/Builder · file edit/)).toBeInTheDocument();
  });

  it("emits explicit plan approval and rejection decisions", async () => {
    const onDecision = vi.fn();
    const { rerender } = render(<PlanApprovalModal request={request} onDecision={onDecision} />);

    await userEvent.click(screen.getByRole("button", { name: /approve & run/i }));
    expect(onDecision).toHaveBeenLastCalledWith("approve");

    rerender(<PlanApprovalModal request={request} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /reject plan/i }));
    expect(onDecision).toHaveBeenLastCalledWith("reject");
  });
});
