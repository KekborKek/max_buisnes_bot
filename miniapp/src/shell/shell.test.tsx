// Общие элементы оболочки: тост (D7) и ErrorBoundary.
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { texts } from "../texts";
import { ErrorBoundary } from "./ErrorBoundary";
import { TOAST_MS, ToastProvider, useToast } from "./Toast";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function ToastButtons() {
  const show = useToast();
  return (
    <>
      <button onClick={() => show("Первое")}>first</button>
      <button onClick={() => show("Второе")}>second</button>
    </>
  );
}

test("тост один на приложение и исчезает через 2 с", () => {
  vi.useFakeTimers();
  render(
    <ToastProvider>
      <ToastButtons />
    </ToastProvider>,
  );
  act(() => screen.getByText("first").click());
  expect(screen.getByText("Первое")).toBeInTheDocument();

  act(() => vi.advanceTimersByTime(TOAST_MS - 500));
  act(() => screen.getByText("second").click());
  expect(screen.queryByText("Первое")).not.toBeInTheDocument();
  expect(screen.getByText("Второе")).toBeInTheDocument();

  act(() => vi.advanceTimersByTime(TOAST_MS - 1));
  expect(screen.getByText("Второе")).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByText("Второе")).not.toBeInTheDocument();
});

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error("boom");
  return <span>цел</span>;
}

function Harness() {
  const [explode, setExplode] = useState(true);
  return (
    <>
      <button onClick={() => setExplode(false)}>fix</button>
      <ErrorBoundary onError={onError}>
        <Bomb explode={explode} />
      </ErrorBoundary>
    </>
  );
}
const onError = vi.fn();

test("ErrorBoundary: вместо белого экрана — плашка с «Повторить»", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  render(<Harness />);
  expect(screen.getByRole("alert")).toHaveTextContent(texts.common.error);
  expect(onError).toHaveBeenCalled();

  await userEvent.click(screen.getByText("fix"));
  await userEvent.click(screen.getByRole("button", { name: texts.common.retry }));
  expect(screen.getByText("цел")).toBeInTheDocument();
});
