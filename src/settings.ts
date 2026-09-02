import type { AppSettings } from "./types";

export const SETTINGS_STORAGE_KEY = "taskloom.settings.v1";

export const DEFAULT_SETTINGS: AppSettings = {
  workspacePath: "",
  defaultProvider: "ollama",
  ollamaUrl: "http://127.0.0.1:11434/api/generate",
  ollamaModel: "llama3.2",
  openaiModel: "gpt-4o-mini",
  onboardingComplete: false,
};

export function loadSettings(storage: Pick<Storage, "getItem"> = localStorage): AppSettings {
  try {
    const saved = storage.getItem(SETTINGS_STORAGE_KEY);
    if (!saved) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(saved) as Partial<AppSettings>;
    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      defaultProvider: parsed.defaultProvider === "openai" ? "openai" : "ollama",
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(
  settings: AppSettings,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  storage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
}
