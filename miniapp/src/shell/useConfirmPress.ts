// Подтверждение повторным нажатием (D8): первое нажатие меняет текст кнопки на 3 с,
// второе в этот срок — выполняет действие. Модалки в max-ui нет.
import { useCallback, useEffect, useRef, useState } from "react";

export const CONFIRM_MS = 3000;

export function useConfirmPress(ms = CONFIRM_MS) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);

  const arm = useCallback(() => {
    clearTimeout(timer.current);
    setArmed(true);
    timer.current = setTimeout(() => setArmed(false), ms);
  }, [ms]);

  const reset = useCallback(() => {
    clearTimeout(timer.current);
    setArmed(false);
  }, []);

  return { armed, arm, reset };
}
