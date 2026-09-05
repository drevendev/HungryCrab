import { describe, expect, it, vi } from "vitest";
import { createStore } from "./store";

describe("createStore", () => {
  it("notifies subscribers once per change", () => {
    const store = createStore({ count: 0 });
    const listener = vi.fn();
    store.subscribe(listener);
    store.set({ count: 1 });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("stops notifying after dispose", () => {
    const store = createStore({ count: 0 });
    const listener = vi.fn();
    store.subscribe(listener);
    store.dispose();
    store.set({ count: 2 });
    expect(listener).not.toHaveBeenCalled();
  });
});
