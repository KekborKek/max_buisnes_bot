// Дымовой тест: проверяет, что связка vitest + jsdom + @testing-library работает.
// Настоящие тесты поведения мини-аппа — задача #18.
import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import App from "./App";
import { texts } from "./texts";

test("App показывает состояние загрузки до ответа API", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  render(<App />);
  expect(screen.getByText(texts.loading)).toBeInTheDocument();
});
