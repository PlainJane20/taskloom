import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, SETTINGS_STORAGE_KEY, loadSettings, saveSettings } from "./settings";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

describe("local settings", () => {
  it("falls back safely when stored data is malformed", () => {
    const storage = memoryStorage();
    storage.setItem(SETTINGS_STORAGE_KEY, "not-json");
    expect(loadSettings(storage)).toEqual(DEFAULT_SETTINGS);
  });

  it("round-trips supported preferences while preserving defaults", () => {
    const storage = memoryStorage();
    saveSettings({ ...DEFAULT_SETTINGS, workspacePath: "/tmp/taskloom", defaultProvider: "openai", onboardingComplete: true }, storage);
    expect(loadSettings(storage)).toEqual(expect.objectContaining({
      workspacePath: "/tmp/taskloom", defaultProvider: "openai", onboardingComplete: true,
      ollamaModel: "llama3.2",
    }));
  });
});
