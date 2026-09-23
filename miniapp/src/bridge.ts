// Типизированный доступ к MAX Bridge (window.WebApp).
// Методы — только из docs/max-api-notes.md, не выдумывать по аналогии с Telegram.

export interface MaxBackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

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
  BackButton?: MaxBackButton;
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

/**
 * start_param запуска: в MAX — `WebApp.initDataUnsafe.start_param`;
 * вне MAX — query-параметр `?start_param=`, чтобы отлаживать диплинки на localhost:5173.
 */
export function getStartParam(): string | null {
  const wa = getWebApp();
  const raw = wa
    ? wa.initDataUnsafe?.start_param
    : new URLSearchParams(window.location.search).get("start_param");
  return typeof raw === "string" && raw ? raw : null;
}

/** Кнопка «Назад» MAX; вне MAX её нет — оболочка показывает замену. */
export function getBackButton(): MaxBackButton | null {
  return getWebApp()?.BackButton ?? null;
}

/** Ссылка на диалог с ботом (`https://max.ru/<botName>`). Пусто — кнопка «Открыть чат» скрыта. */
export function getBotUrl(): string {
  const url: unknown = import.meta.env.VITE_BOT_URL;
  return typeof url === "string" ? url.trim() : "";
}

/**
 * Открыть диалог с ботом. В MAX — `openMaxLink(url)` [сверить: поведение на живом клиенте];
 * вне MAX или без метода — обычная ссылка.
 */
export function openBotChat(url: string): void {
  const wa = getWebApp();
  if (wa?.openMaxLink) {
    wa.openMaxLink(url);
    return;
  }
  window.open(url, "_blank", "noopener");
}
