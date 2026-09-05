import { format } from "date-fns";
import type { Store } from "./lib/store";

export interface AppState {
  pools: string[];
}

export function renderApp(root: HTMLElement, store: Store<AppState>): void {
  const render = () => {
    const { pools } = store.get();
    root.innerHTML = `
      <h1>Crab Cove</h1>
      <p>${format(new Date(), "yyyy-MM-dd")}</p>
      <ul>${pools.map((pool) => `<li>${pool}</li>`).join("")}</ul>
      <button id="add">Add pool</button>
    `;
    root.querySelector("#add")?.addEventListener("click", () => {
      store.set({ pools: [...pools, `Pool ${pools.length + 1}`] });
    });
  };
  store.subscribe(render);
  render();
}
