// Типизированный доступ к MAX Bridge (window.WebApp).
// Методы — только из docs/max-api-notes.md, не выдумывать по аналогии с Telegram.

export interface MaxWebApp {
  initData: string;
  initDataUnsafe: {
    user?: { user_id?: number; id?: number; first_name?: string };
    start_param?: unknown;
  };
  platform?: string;
  version?: string;
  requestContact?: () => Promise<unknown>;
  openCodeReader?: () => Promise<unknown>;
  openLink?: (url: string) => void;
  openMaxLink?: (url: string) => void;
  BackButton?: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
  HapticFeedback?: unknown;
}

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

/** Мы внутри MAX, если Bridge загрузился и передал initData. */
export function getWebApp(): MaxWebApp | null {
  const wa = window.WebApp;
  return wa && wa.initData ? wa : null;
}

/** initData для заголовка X-Max-Init-Data. Вне MAX (локальная разработка) — "dev". */
export function getInitData(): string {
  return getWebApp()?.initData ?? "dev";
}
