import "@maxhub/max-ui/dist/styles.css";

import { MaxUI } from "@maxhub/max-ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MaxUI>
      <App />
    </MaxUI>
  </StrictMode>,
);
