import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/app.css";

createRoot(document.getElementById("root")!).render(
  // VITE_DEV_USER 只在本地 demo 用（服务端还要开 VP_DEV_AUTH）
  <StrictMode><App devUser={import.meta.env.VITE_DEV_USER} /></StrictMode>,
);
