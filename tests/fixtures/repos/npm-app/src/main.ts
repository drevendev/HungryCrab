import { renderApp } from "./app";
import { createStore } from "./lib/store";

const store = createStore({ pools: [] as string[] });
const root = document.querySelector<HTMLDivElement>("#app");

if (root) {
  renderApp(root, store);
}
