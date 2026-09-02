import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_SETTINGS } from "../../settings";
import type { HealthReport } from "../../types";
import { SettingsDashboard } from "../SettingsDashboard";

const health: HealthReport = {
  checkedAt: "2026-09-02T18:00:00Z",
  ready: true,
  workspace: "/Users/example/TaskloomWorkspace",
  checks: [
    { id: "workspace", label: "Workspace", status: "ready", detail: "Readable and writable.", required: true },
    { id: "github", label: "GitHub CLI", status: "warning", detail: "Optional.", required: false },
  ],
};

describe("SettingsDashboard", () => {
  it("saves a trimmed local configuration and completes onboarding", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<SettingsDashboard settings={DEFAULT_SETTINGS} health={null} bridgeStatus="connected" onSave={onSave} onCheck={vi.fn()} />);

    await user.type(screen.getByLabelText(/workspace folder/i), "  /tmp/taskloom-workspace  ");
    await user.selectOptions(screen.getByLabelText(/default ai provider/i), "openai");
    await user.click(screen.getByRole("button", { name: /save and reconnect/i }));

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      workspacePath: "/tmp/taskloom-workspace",
      defaultProvider: "openai",
      onboardingComplete: true,
    }));
    expect(screen.getByRole("status")).toHaveTextContent(/settings saved/i);
  });

  it("runs and renders local environment health checks", async () => {
    const user = userEvent.setup();
    const onCheck = vi.fn().mockResolvedValue(health);
    const { rerender } = render(<SettingsDashboard settings={DEFAULT_SETTINGS} health={null} bridgeStatus="connected" onSave={vi.fn()} onCheck={onCheck} />);

    await user.click(screen.getByRole("button", { name: /run health checks/i }));
    expect(onCheck).toHaveBeenCalledOnce();

    rerender(<SettingsDashboard settings={DEFAULT_SETTINGS} health={health} bridgeStatus="connected" onSave={vi.fn()} onCheck={onCheck} />);
    expect(screen.getByText("READY")).toBeInTheDocument();
    expect(screen.getByText("/Users/example/TaskloomWorkspace")).toBeInTheDocument();
    expect(screen.getByText("GitHub CLI")).toBeInTheDocument();
  });
});
