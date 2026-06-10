import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BuSelector } from "@/components/BuSelector";

// In-memory backing store for the mocked BU module so the component's
// useSyncExternalStore subscription behaves like the real one.
const state = {
  enabled: true,
  options: ["1", "42", "100"],
  selected: "" as string,
  listeners: new Set<() => void>(),
};

vi.mock("@/lib/bu", () => ({
  isPocBuOverrideEnabled: () => state.enabled,
  getPocBuOptions: () => state.options,
  getSelectedBu: () => (state.selected === "" ? null : state.selected),
  getServerSelectedBu: () => null,
  setSelectedBu: (value: string | null) => {
    state.selected = value ?? "";
    for (const listener of state.listeners) listener();
  },
  subscribeSelectedBu: (onChange: () => void) => {
    state.listeners.add(onChange);
    return () => state.listeners.delete(onChange);
  },
}));

beforeEach(() => {
  state.enabled = true;
  state.options = ["1", "42", "100"];
  state.selected = "";
  state.listeners.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("BuSelector", () => {
  it("renders nothing when POC mode is off", () => {
    state.enabled = false;
    const { container } = render(<BuSelector />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the dropdown with the default option and configured BUs", () => {
    render(<BuSelector />);
    const select = screen.getByLabelText(/bu override/i);
    expect(select).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /default \(from token\)/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "BU 1" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "BU 42" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "BU 100" })).toBeInTheDocument();
  });

  it("reflects the persisted selection as the current value", () => {
    state.selected = "42";
    render(<BuSelector />);
    expect(screen.getByLabelText(/bu override/i)).toHaveValue("42");
  });

  it("persists a selection when the operator picks a BU", async () => {
    const user = userEvent.setup();
    render(<BuSelector />);
    await user.selectOptions(screen.getByLabelText(/bu override/i), "42");
    expect(state.selected).toBe("42");
    expect(screen.getByLabelText(/bu override/i)).toHaveValue("42");
  });

  it("clears the selection when the default option is chosen", async () => {
    const user = userEvent.setup();
    state.selected = "42";
    render(<BuSelector />);
    await user.selectOptions(screen.getByLabelText(/bu override/i), "");
    expect(state.selected).toBe("");
    expect(screen.getByLabelText(/bu override/i)).toHaveValue("");
  });
});
