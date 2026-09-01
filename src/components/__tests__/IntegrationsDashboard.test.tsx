import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AgentBridge } from "../../hooks/useAgentBridge";
import { IntegrationsDashboard } from "../IntegrationsDashboard";

function makeBridge(overrides: Partial<AgentBridge> = {}): AgentBridge {
  const connection = {
    id: "github-main", provider: "github", repository: "acme/app",
    syncDirection: "bidirectional", autoClose: true, enabled: true,
    status: "connected", createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
  } as const;
  return {
    status: "connected", tasks: [], sessions: [], approval: null, planApproval: null,
    agents: [], workflows: [], workflowRuns: [], triggers: [], fileTriggers: [],
    providerConnections: [], syncEvents: [], externalIssueLinks: [], error: null,
    createProviderConnection: vi.fn().mockResolvedValue(connection),
    testProviderConnection: vi.fn().mockResolvedValue(connection),
    syncProviderInbound: vi.fn().mockResolvedValue({ imported: 2, updated: 1, unchanged: 3 }),
    syncTaskOutbound: vi.fn().mockResolvedValue([]),
    ...overrides,
  } as unknown as AgentBridge;
}

describe("IntegrationsDashboard", () => {
  it("connects and verifies a GitHub repository", async () => {
    const user = userEvent.setup();
    const bridge = makeBridge();
    render(<IntegrationsDashboard bridge={bridge} />);

    await user.type(screen.getByLabelText(/repository/i), "acme/app");
    await user.click(screen.getByRole("button", { name: /^connect$/i }));

    expect(bridge.createProviderConnection).toHaveBeenCalledWith({
      provider: "github", repository: "acme/app",
      syncDirection: "bidirectional", autoClose: true,
    });
    expect(bridge.testProviderConnection).toHaveBeenCalledWith("github-main");
    expect(await screen.findByText(/connected acme\/app/i)).toBeInTheDocument();
  });

  it("imports issues and reports the idempotent reconciliation summary", async () => {
    const user = userEvent.setup();
    const base = makeBridge();
    const bridge = makeBridge({
      providerConnections: [{
        id: "github-main", provider: "github", repository: "acme/app",
        syncDirection: "bidirectional", autoClose: true, enabled: true,
        status: "connected", createdAt: "2026-09-01T00:00:00Z",
        updatedAt: "2026-09-01T00:00:00Z",
      }],
      syncProviderInbound: base.syncProviderInbound,
    });
    render(<IntegrationsDashboard bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: /import issues/i }));

    expect(bridge.syncProviderInbound).toHaveBeenCalledWith("github-main");
    expect(await screen.findByText(/imported 2, updated 1, unchanged 3/i)).toBeInTheDocument();
  });

  it("requires confirmation before overriding a remote conflict", async () => {
    const user = userEvent.setup();
    const bridge = makeBridge({
      syncEvents: [{
        id: "event-1", connectionId: "github-main", direction: "outbound",
        action: "close_issue", status: "conflict", message: "Issue changed on GitHub",
        taskId: "task-1", externalId: "9001", attemptCount: 1,
        createdAt: "2026-09-01T00:00:00Z",
      }],
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<IntegrationsDashboard bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: /close anyway/i }));

    expect(window.confirm).toHaveBeenCalled();
    expect(bridge.syncTaskOutbound).toHaveBeenCalledWith("task-1", true);
  });
});
