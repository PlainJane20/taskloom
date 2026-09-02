import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { AgentBridge } from "../../hooks/useAgentBridge";
import type { FileSnapshot, SnapshotPreview } from "../../types";
import { SnapshotsDashboard } from "../SnapshotsDashboard";

const snapshot: FileSnapshot = {
  snapshotId: "snapshot-1",
  filePath: "src/example.ts",
  existed: true,
  createdAt: new Date().toISOString(),
  taskId: "task-1",
  agentId: "builder",
  reason: "pre_write",
};

const preview: SnapshotPreview = {
  ...snapshot,
  snapshotContent: "export const value = 1;\n",
  snapshotTruncated: false,
  snapshotSha256: "old-hash",
  currentContent: "export const value = 2;\n",
  currentExists: true,
  currentTruncated: false,
  currentSha256: "current-hash",
};

function makeBridge(overrides: Partial<AgentBridge> = {}): AgentBridge {
  return {
    snapshots: [snapshot], snapshotRestoreEvents: [],
    previewSnapshot: vi.fn().mockResolvedValue(preview),
    restoreSnapshot: vi.fn().mockResolvedValue({
      id: "restore-1", snapshotId: "snapshot-1", filePath: "src/example.ts",
      status: "completed", createdAt: new Date().toISOString(),
    }),
    refreshSnapshots: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as AgentBridge;
}

describe("SnapshotsDashboard", () => {
  it("filters snapshots and opens a side-by-side preview", async () => {
    const user = userEvent.setup();
    const bridge = makeBridge();
    render(<SnapshotsDashboard bridge={bridge} />);

    expect(screen.getByText("src/example.ts")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText(/search files/i), "missing");
    expect(screen.getByText(/no snapshots match/i)).toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText(/search files/i));
    await user.click(screen.getByRole("button", { name: /compare/i }));

    expect(bridge.previewSnapshot).toHaveBeenCalledWith("snapshot-1");
    expect(await screen.findByRole("dialog")).toHaveTextContent("export const value = 1;");
    expect(screen.getByRole("dialog")).toHaveTextContent("export const value = 2;");
  });

  it("requires confirmation and restores against the previewed current hash", async () => {
    const user = userEvent.setup();
    const bridge = makeBridge();
    render(<SnapshotsDashboard bridge={bridge} />);

    await user.click(screen.getByRole("button", { name: /compare/i }));
    await user.click(await screen.findByRole("button", { name: /restore this version/i }));
    expect(bridge.restoreSnapshot).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /^confirm restore$/i }));

    expect(bridge.restoreSnapshot).toHaveBeenCalledWith("snapshot-1", "current-hash");
    expect(bridge.refreshSnapshots).toHaveBeenCalledOnce();
    expect(await screen.findByRole("status")).toHaveTextContent(/new safety snapshot/i);
  });
});
