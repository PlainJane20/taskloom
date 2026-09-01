import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApprovalModal } from "../ApprovalModal";

const request = {
  taskId: "task-7",
  requestId: "approval-42",
  filePath: "src/example.ts",
  before: "const answer = 41;\n",
  after: "const answer = 42;\n",
  summary: "Correct the answer",
};

describe("ApprovalModal", () => {
  it("shows the before and after previews", () => {
    render(<ApprovalModal request={request} onDecision={vi.fn()} />);
    expect(screen.getByText("const answer = 41;")).toBeInTheDocument();
    expect(screen.getByText("const answer = 42;")).toBeInTheDocument();
    expect(screen.getByText("src/example.ts")).toBeInTheDocument();
  });

  it("emits the approve IPC payload", async () => {
    const onDecision = vi.fn();
    render(<ApprovalModal request={request} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(onDecision).toHaveBeenCalledWith({
      type: "approval_decision",
      payload: { requestId: "approval-42", decision: "approve" },
    });
  });

  it("emits the reject IPC payload", async () => {
    const onDecision = vi.fn();
    render(<ApprovalModal request={request} onDecision={onDecision} />);
    await userEvent.click(screen.getByRole("button", { name: /reject/i }));
    expect(onDecision).toHaveBeenCalledWith({
      type: "approval_decision",
      payload: { requestId: "approval-42", decision: "reject" },
    });
  });
});
