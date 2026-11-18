import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { BackendGate } from "./components/BackendGate";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BackendGate>{(onLoaded) => <App onLoaded={onLoaded} />}</BackendGate>
  </React.StrictMode>,
);
