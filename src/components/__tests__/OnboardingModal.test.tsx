import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_SETTINGS } from "../../settings";
import type { HealthReport } from "../../types";
import { OnboardingModal } from "../OnboardingModal";

const health: HealthReport = {
  checkedAt: "2026-09-02T18:00:00Z", ready: true, workspace: "/tmp/taskloom",
  checks: [{ id: "workspace", label: "Workspace", status: "ready", detail: "Ready.", required: true }],
};

describe("OnboardingModal", () => {
  it("explains the safety model before showing setup readiness", async () => {
    const user = userEvent.setup();
    render(<OnboardingModal settings={DEFAULT_SETTINGS} health={health} onComplete={vi.fn()} onCustomize={vi.fn()} />);

    expect(screen.getByRole("img", { name: /taskloom logo/i })).toBeInTheDocument();
    expect(screen.getByText(/local workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/human approval/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /check my setup/i }));

    expect(screen.getByText(/your local environment/i)).toBeInTheDocument();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("offers recommended and customized completion paths", async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();
    const onCustomize = vi.fn();
    render(<OnboardingModal settings={DEFAULT_SETTINGS} health={health} onComplete={onComplete} onCustomize={onCustomize} />);
    await user.click(screen.getByRole("button", { name: /check my setup/i }));
    await user.click(screen.getByRole("button", { name: /customize settings/i }));
    expect(onCustomize).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: /use recommended setup/i }));
    expect(onComplete).toHaveBeenCalledOnce();
  });
});
