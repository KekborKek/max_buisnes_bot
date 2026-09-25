// Экран 17. Форма своей задачи (docs/screens/17-task-form.md): четыре поля (D9),
// дата — нативный input type="date" (D8). Событие task_created пишет бэкенд в POST /api/tasks.
import {
  Button,
  CellHeader,
  CellInput,
  CellList,
  CellSimple,
  Counter,
  Input,
  Radio,
  Typography,
} from "@maxhub/max-ui";
import { useState } from "react";

import { addDays } from "../calendar";
import { errorKind, type ErrorKind } from "../data/http";
import type { DataSource } from "../data/source";
import { useNavigation } from "../router";
import { ErrorBanner } from "../shell/ErrorBanner";
import { useToast } from "../shell/Toast";
import { useConfirmPress } from "../shell/useConfirmPress";
import { itemKey, useCalendarStore } from "../store";
import { texts } from "../texts";
import {
  REMIND_HOURS,
  REMIND_OFFSETS,
  type RemindOffset,
  type TaskDraft,
  type TaskInput,
} from "../types";

export const TITLE_MAX = 60;
/** Счётчик символов появляется, когда название длиннее 50. */
export const TITLE_COUNTER_FROM = 50;

const OFFSET_LABEL: Record<RemindOffset, string> = {
  0: texts.form.remind0,
  1: texts.form.remind1,
  3: texts.form.remind3,
  7: texts.form.remind7,
};

interface Values {
  title: string;
  date: string;
  offset: RemindOffset;
  hour: number;
}

export interface FieldErrors {
  title: string | null;
  date: string | null;
}

/** Валидация из спеки: название обязательно, дата — не раньше сегодняшней. */
export function validate(values: Pick<Values, "title" | "date">, today: string): FieldErrors {
  return {
    title: values.title.trim() === "" ? texts.form.nameRequired : null,
    date:
      values.date === ""
        ? texts.form.dateRequired
        : values.date < today
          ? texts.form.datePast
          : null,
  };
}

interface Props {
  source: DataSource;
  /** «Сегодня» в поясе пользователя. */
  today: string;
  /** Черновик из бота (`start_param=task_draft` → `/api/me`). */
  draft: TaskDraft | null;
  /** Правка задачи: её карточка уже загружена экраном 16. */
  taskId?: number;
  /** Дата по умолчанию для новой задачи — выбранный день экрана 15 (route.date, #93). */
  initialDate?: string;
  /**
   * Открыта кнопкой «+ Задача» на экране 15: сохранение новой задачи возвращает туда же,
   * а не на список 14, с выбранным днём — датой созданной задачи (#93, «Решение человека»).
   */
  onCreatedFromMonth?: (dueDate: string) => void;
}

export function TaskFormScreen({
  source,
  today,
  draft,
  taskId,
  initialDate,
  onCreatedFromMonth,
}: Props) {
  const nav = useNavigation();
  const toast = useToast();
  const { cards, upsert } = useCalendarStore();
  const editing = taskId !== undefined ? cards[itemKey("task", taskId)] : undefined;

  const [initial] = useState<Values>(() => {
    if (editing?.type === "task") {
      return {
        title: editing.title,
        date: editing.due_date,
        offset: editing.remind_offset_days,
        hour: editing.remind_hour,
      };
    }
    return {
      title: (draft?.title ?? "").slice(0, TITLE_MAX),
      date: draft?.due_date ?? initialDate ?? addDays(today, 1),
      offset: 1,
      hour: 10,
    };
  });
  const [values, setValues] = useState<Values>(initial);
  const [titleTouched, setTitleTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ErrorKind | null>(null);
  const confirmCancel = useConfirmPress();

  // Правка задачи, которой нет в кэше (не должно случаться: «Перенести» есть только в карточке).
  if (taskId !== undefined && editing?.type !== "task") {
    return (
      <div className="screen">
        <section className="empty">
          <Typography.Headline>{texts.card.notFound}</Typography.Headline>
          <Button size="large" stretched onClick={nav.home}>
            {texts.card.backToList}
          </Button>
        </section>
      </div>
    );
  }

  const errors = validate(values, today);
  const canSave = !errors.title && !errors.date && !saving;
  const dirty =
    values.title !== initial.title ||
    values.date !== initial.date ||
    values.offset !== initial.offset ||
    values.hour !== initial.hour;
  const set = (patch: Partial<Values>) => setValues((v) => ({ ...v, ...patch }));

  const save = () => {
    if (!canSave) return;
    const input: TaskInput = {
      title: values.title.trim(),
      due_date: values.date,
      remind_offset_days: values.offset,
      remind_hour: values.hour,
    };
    setSaving(true);
    setError(null);
    const request =
      taskId !== undefined ? source.updateTask(taskId, input) : source.createTask(input);
    request.then(
      (card) => {
        upsert(card);
        if (taskId !== undefined) {
          nav.back();
          toast(texts.form.updated);
        } else {
          if (onCreatedFromMonth) {
            onCreatedFromMonth(card.due_date);
          } else {
            nav.home();
          }
          toast(texts.form.created);
        }
      },
      (e: unknown) => {
        // Форма не закрывается, введённое остаётся на месте.
        const kind = errorKind(e);
        setSaving(false);
        setError(kind);
        void source.track("error", { where: "task_form", kind }).catch(() => undefined);
      },
    );
  };

  const cancel = () => {
    if (!dirty || confirmCancel.armed) {
      confirmCancel.reset();
      nav.back();
      return;
    }
    confirmCancel.arm();
  };

  const titleError = titleTouched ? errors.title : null;
  const left = TITLE_MAX - values.title.length;

  return (
    <div className="screen">
      {error && <ErrorBanner kind={error} onRetry={save} retrying={saving} />}
      <Typography.Headline className="form-title">
        {taskId !== undefined ? texts.form.titleEdit : texts.form.titleNew}
      </Typography.Headline>

      <CellList
        mode="island"
        header={
          <CellHeader
            after={values.title.length > TITLE_COUNTER_FROM && <Counter value={left} rounded />}
          >
            {texts.form.name}
          </CellHeader>
        }
      >
        <CellInput
          aria-label={texts.form.name}
          aria-invalid={titleError ? true : undefined}
          aria-describedby={titleError ? "task-title-error" : undefined}
          placeholder={texts.form.namePlaceholder}
          maxLength={TITLE_MAX}
          value={values.title}
          disabled={saving}
          onChange={(e) => {
            set({ title: e.target.value.slice(0, TITLE_MAX) });
            setTitleTouched(true);
          }}
          onBlur={() => setTitleTouched(true)}
        />
      </CellList>
      {titleError && (
        <Typography.Label id="task-title-error" className="field-error">
          {titleError}
        </Typography.Label>
      )}

      <CellList mode="island" header={<CellHeader>{texts.form.date}</CellHeader>}>
        <div className="form-field">
          <Input
            type="date"
            aria-label={texts.form.date}
            aria-invalid={errors.date ? true : undefined}
            aria-describedby={errors.date ? "task-date-error" : undefined}
            min={today}
            value={values.date}
            disabled={saving}
            onChange={(e) => set({ date: e.target.value })}
          />
        </div>
      </CellList>
      {errors.date && (
        <Typography.Label id="task-date-error" className="field-error">
          {errors.date}
        </Typography.Label>
      )}

      <CellList
        mode="island"
        role="radiogroup"
        aria-label={texts.form.remind}
        header={<CellHeader>{texts.form.remind}</CellHeader>}
      >
        {REMIND_OFFSETS.map((offset) => (
          <CellSimple
            key={offset}
            as="label"
            before={
              <Radio
                name="remind-offset"
                checked={values.offset === offset}
                disabled={saving}
                onChange={() => set({ offset })}
              />
            }
            title={OFFSET_LABEL[offset]}
          />
        ))}
      </CellList>

      <CellList
        mode="island"
        role="radiogroup"
        aria-label={texts.form.time}
        header={<CellHeader>{texts.form.time}</CellHeader>}
      >
        {REMIND_HOURS.map((hour) => (
          <CellSimple
            key={hour}
            as="label"
            before={
              <Radio
                name="remind-hour"
                checked={values.hour === hour}
                disabled={saving}
                onChange={() => set({ hour })}
              />
            }
            title={texts.form.hour(hour)}
          />
        ))}
      </CellList>

      <div className="card-actions">
        <Button size="large" stretched loading={saving} disabled={!canSave} onClick={save}>
          {texts.form.save}
        </Button>
        <Button size="large" stretched variant="secondary" disabled={saving} onClick={cancel}>
          {confirmCancel.armed ? texts.form.discardConfirm : texts.form.cancel}
        </Button>
      </div>
    </div>
  );
}
