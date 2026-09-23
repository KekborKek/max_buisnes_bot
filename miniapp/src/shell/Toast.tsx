// Тост (D7): блок внизу экрана на Typography, исчезает через 2 с. Один на всё приложение.
import { Typography } from "@maxhub/max-ui";
import { createContext, type ReactNode, useCallback, useContext, useRef, useState } from "react";

export const TOAST_MS = 2000;

type ShowToast = (text: string) => void;

const ToastContext = createContext<ShowToast>(() => undefined);

export function useToast(): ShowToast {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [text, setText] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const show = useCallback<ShowToast>((next) => {
    clearTimeout(timer.current);
    setText(next);
    timer.current = setTimeout(() => setText(null), TOAST_MS);
  }, []);

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toast-slot" role="status" aria-live="polite">
        {text && (
          <div className="toast">
            <Typography.Body>{text}</Typography.Body>
          </div>
        )}
      </div>
    </ToastContext.Provider>
  );
}
