// «Поделиться сроком» (SHARE): экран приглашения — загрузка, недействительная ссылка, ошибка,
// приглашение; «Добавить» и «Не нужно».
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { ApiError } from "../data/http";
import { ToastProvider } from "../shell/Toast";
import { fakeSource, makeTaskCard } from "../test/fakeSource";
import { texts } from "../texts";
import type { ShareInvite } from "../types";
import { ShareScreen } from "./ShareScreen";

const TODAY = "2026-09-23";
const CODE = "abcdEFGH1234";
const INVITE: ShareInvite = {
  code: CODE,
  item_type: "obligation",
  title: "Авансовый платёж",
  due_date: "2026-10-28",
};

function renderShare(code = CODE) {
  const fake = fakeSource();
  const onFinish = vi.fn();
  render(
    <ToastProvider>
      <ShareScreen source={fake.source} code={code} today={TODAY} onFinish={onFinish} />
    </ToastProvider>,
  );
  return { ...fake, onFinish };
}

async function ready(invite: ShareInvite = INVITE) {
  const ctx = renderShare();
  await act(async () => ctx.last("getShare").resolve(invite));
  return ctx;
}

test("загрузка: спиннер, запрос приглашения по коду", () => {
  const { source } = renderShare();
  expect(document.querySelector("[aria-busy='true']")).toBeInTheDocument();
  expect(source.getShare).toHaveBeenCalledWith(CODE);
});

test("приглашение: вопрос, название, дата и две кнопки; без имени отправителя", async () => {
  await ready();
  expect(screen.getByText(texts.share.title)).toBeInTheDocument();
  expect(screen.getByText("Авансовый платёж")).toBeInTheDocument();
  expect(screen.getByText("28 октября, среда")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: texts.share.add })).toBeEnabled();
  expect(screen.getByRole("button", { name: texts.share.decline })).toBeEnabled();
  expect(screen.queryByText(texts.share.past)).not.toBeInTheDocument();
});

test("дата в прошлом (D32): подсказка, что напоминаний не будет", async () => {
  await ready({ ...INVITE, due_date: "2026-09-15" });
  expect(screen.getByText(texts.share.past)).toBeInTheDocument();
});

test("ссылка недействительна (404) — текст и выход к календарю", async () => {
  const { last, onFinish } = renderShare();
  await act(async () => last("getShare").reject(new ApiError("client", 404)));
  expect(screen.getByText(texts.share.invalidTitle)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: texts.share.toCalendar }));
  expect(onFinish).toHaveBeenCalledWith(null);
});

test("пустой код — сразу «недействительна», без запроса", () => {
  const { source } = renderShare("");
  expect(screen.getByText(texts.share.invalidTitle)).toBeInTheDocument();
  expect(source.getShare).not.toHaveBeenCalled();
});

test("ошибка сети — плашка с «Повторить», повтор загружает приглашение", async () => {
  const { last, source } = renderShare();
  await act(async () => last("getShare").reject(new ApiError("network")));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.share.loadError);
  expect(source.track).toHaveBeenCalledWith("error", { where: "share", kind: "network" });
  expect(screen.getByRole("button", { name: texts.share.toCalendar })).toBeInTheDocument();

  await userEvent.click(within(screen.getByRole("alert")).getByRole("button"));
  expect(source.getShare).toHaveBeenCalledTimes(2);
  await act(async () => last("getShare").resolve(INVITE));
  expect(screen.getByText("Авансовый платёж")).toBeInTheDocument();
});

test("«Добавить»: загрузка на кнопке, тост и задача наружу", async () => {
  const { last, source, onFinish } = await ready();
  await userEvent.click(screen.getByRole("button", { name: texts.share.add }));
  expect(source.acceptShare).toHaveBeenCalledWith(CODE);
  expect(screen.getByRole("button", { name: texts.share.decline })).toBeDisabled();

  const task = makeTaskCard({ id: 42, title: "Авансовый платёж" });
  await act(async () => last("acceptShare").resolve({ created: true, task }));
  expect(onFinish).toHaveBeenCalledWith(task);
  expect(screen.getByText(texts.share.added)).toBeInTheDocument();
});

test("ошибка «Добавить» — плашка, приглашение на месте, «Повторить» добавляет", async () => {
  const { last, source, onFinish } = await ready();
  await userEvent.click(screen.getByRole("button", { name: texts.share.add }));
  await act(async () => last("acceptShare").reject(new ApiError("server", 503)));
  expect(screen.getByRole("alert")).toHaveTextContent(texts.share.acceptError);
  expect(screen.getByText("Авансовый платёж")).toBeInTheDocument();
  expect(onFinish).not.toHaveBeenCalled();

  await userEvent.click(within(screen.getByRole("alert")).getByRole("button"));
  expect(source.acceptShare).toHaveBeenCalledTimes(2);
  await act(async () => last("acceptShare").resolve({ created: false, task: makeTaskCard() }));
  expect(onFinish).toHaveBeenCalled();
});

test("«Не нужно» — share_declined и выход без задачи", async () => {
  const { source, onFinish } = await ready();
  await userEvent.click(screen.getByRole("button", { name: texts.share.decline }));
  expect(source.track).toHaveBeenCalledWith("share_declined", { item_type: "obligation" });
  expect(source.acceptShare).not.toHaveBeenCalled();
  expect(onFinish).toHaveBeenCalledWith(null);
});
