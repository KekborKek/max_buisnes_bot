# Git и GitHub с нуля — как устроен наш репозиторий и как с ним работать

Этот файл для всей команды. Если ты никогда не работал с Git — читай по порядку.

---

## 1. Пять понятий, без которых никуда

| Понятие | Что это простыми словами | Аналогия |
|---|---|---|
| **Репозиторий (repo)** | Папка проекта + скрытая папка `.git`, где хранится вся история изменений | Google Doc с историей версий, только для целой папки |
| **Коммит (commit)** | Сохранённый «снимок» всех файлов с подписью: кто, когда и зачем | Точка сохранения в игре |
| **Ветка (branch)** | Параллельная линия коммитов. `main` — главная, рабочая версия | Черновик, который потом вливают в чистовик |
| **Remote / GitHub** | Копия репозитория на сервере GitHub. `push` — отправить туда свои коммиты, `pull` — забрать чужие | Облако, куда синхронизируется папка |
| **Pull Request (PR)** | Заявка «влейте мою ветку в main»: там видно изменения, их комментируют, запускаются проверки | Режим предложения правок в документе |

Ещё два слова:
- **CI (GitHub Actions)** — робот, который на каждый PR сам запускает тесты, линтер и сборку Docker. Красный крестик = что-то сломано, зелёная галочка = можно вливать.
- **Issue** — задача или баг на GitHub. У нас одна задача = один Issue = один PR.

Git работает **локально** (на твоём компьютере), GitHub — **сайт**, где лежит общая копия. Можно коммитить без интернета, а потом отправить всё разом.

---

## 2. Что уже сделано (как Claude собрал шаблон)

1. **Собрал проект в облачной среде Claude** и проверил его:
   - бэкенд: 7 автотестов прошли (`pytest`), линтер без замечаний (`ruff`);
   - мини-приложение: собирается (`npm run build`);
   - вручную запустил сервер: `/health` отвечает, API мини-аппа и вебхук работают;
   - `docker compose build` в облаке проверить нельзя (там закрыт доступ к Docker Hub) — это проверит CI на GitHub при первой отправке.
2. **Упаковал в архив и перенёс** в папку `max_buisnes_bot` на твоём Mac, распаковал.
3. **Создал репозиторий:** `git init -b main` — появилась скрытая папка `.git`, основная ветка называется `main`.
4. **Добавил файлы в индекс:** `git add -A` — «пометить все файлы для следующего снимка». Что НЕ попадает в Git, записано в `.gitignore` (секреты `.env`, `node_modules`, базы данных, кэши).
5. **Сделал первый коммит:** `git commit -m "Создать шаблон репозитория"`. Автор — `KekborKek <KekborKek@users.noreply.github.com>`: коммиты привяжутся к твоему профилю, а настоящая почта не попадёт в историю.
6. Для этого понадобилось **разрешение на удаление файлов** в папке: Git постоянно создаёт и удаляет служебные файлы (`index.lock`), без этого он ломается.

Посмотреть результат можно так (Терминал на Mac):
```bash
cd ~/Claude/Projects/max_buisnes_bot
git log --oneline      # список коммитов
git status             # что изменено с последнего коммита (сейчас — ничего)
```

---

## 3. Что сделать тебе (один раз, ~20 минут)

### 3.1 Установить инструменты
1. **Git.** Открой Терминал (Cmd+Пробел → «Терминал») и введи `git --version`. Если Mac предложит установить «Command Line Developer Tools» — соглашайся, это и есть Git.
2. **VS Code:** code.visualstudio.com → Download for Mac → перетащи в «Программы».
3. **Docker Desktop:** docker.com/products/docker-desktop → версия для Apple Silicon (M1/M2/M3) или Intel — смотри  → «Об этом Mac».
4. **GitHub CLI (`gh`)** — по желанию, но очень удобно (им пользуются и агенты). Проще всего через Homebrew:
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # если brew ещё нет
   brew install gh
   gh auth login        # GitHub.com → HTTPS → Login with a web browser
   ```

### 3.2 Опубликовать репозиторий на GitHub через VS Code
1. VS Code → File → Open Folder → `Claude/Projects/max_buisnes_bot`.
2. Слева значок **Source Control** (ветка с кружочками, Ctrl+Shift+G).
3. Кнопка **Publish Branch** → «Sign in with GitHub» → откроется браузер → разреши доступ.
4. Выбери **Publish to GitHub private repository** и имя `max-business-bot`.
5. Готово: внизу появится «Open on GitHub». На сайте ты увидишь все файлы и README.

> Если захочешь без VS Code: создай пустой приватный репозиторий на github.com (кнопка New, без README), затем
> ```bash
> git remote add origin https://github.com/<логин>/max-business-bot.git
> git push -u origin main
> ```

### 3.3 Автор коммитов
В этом репозитории уже настроено: `KekborKek <KekborKek@users.noreply.github.com>`. Чтобы так было во всех проектах на твоём Mac:
```bash
git config --global user.name "KekborKek"
git config --global user.email "KekborKek@users.noreply.github.com"
```
Каждый участник команды делает то же со своим логином (адрес `<логин>@users.noreply.github.com` или из GitHub → Settings → Emails).

### 3.4 Настроить GitHub (на сайте репозитория)
1. **Проверить CI:** вкладка **Actions** → должен идти/пройти запуск «CI». Если красный — открой, посмотри шаг с ошибкой, пришли её Claude.
2. **Защитить main:** Settings → Branches → Add branch ruleset (или Add rule) → ветка `main` →
   ✅ Require a pull request before merging, ✅ Require status checks to pass (выбери backend, miniapp, docker), ✅ Block force pushes.
   Для приватного репозитория на бесплатном аккаунте правила могут быть недоступны — тогда просто договоримся не пушить в main напрямую.
3. **Пригласить команду:** Settings → Collaborators → Add people → логины участников.
4. **Метки для задач** (если поставил `gh`):
   ```bash
   for l in lane:bot lane:api lane:front lane:data lane:docs agent-ok needs-human; do gh label create "$l" --force; done
   ```
5. **Доска задач:** вкладка Projects → New project → шаблон **Board** → колонки Backlog, Ready, In progress, Review, Done → в настройках проекта Workflows включи «Item closed → Done» и «Pull request merged → Done».
6. **Этапы (Milestones):** Issues → Milestones → New: «Каркас», «Основной сценарий», «Мини-апп», «Ошибки и бонус», «Сдача».

### 3.5 Запустить проект у себя
```bash
cd ~/Claude/Projects/max_buisnes_bot
cp .env.example .env         # файл с настройками, в Git не попадает
docker compose up --build    # первый раз несколько минут
```
Открой http://localhost:8000/docs (API) и http://localhost:8080 (мини-апп с тестовым пользователем). Остановить — Ctrl+C или `docker compose down`.

---

## 4. Каждый день: основной цикл

```bash
git switch main && git pull                 # 1. забрать свежую версию
git switch -c lane/bot-start                # 2. новая ветка под задачу (имя: lane/<дорожка>-<кратко>)
# ... работа (руками или агентом) ...
git status                                   # 3. что изменилось
git add -A                                   # 4. пометить изменения
git commit -m "Добавить обработку /start"    # 5. снимок с понятным сообщением
git push -u origin lane/bot-start            # 6. отправить ветку на GitHub
gh pr create --fill                          # 7. открыть PR (или кнопкой на сайте), в описании: Closes #12
# 8. дождаться зелёного CI → ревью → Squash and merge на сайте
git switch main && git pull                  # 9. обновить main у себя
git branch -d lane/bot-start                 # 10. удалить локальную ветку
```

В VS Code то же самое кнопками: Source Control → «+» у файлов (add) → сообщение → ✓ Commit → Sync/Publish.

### Правила
- **Не коммить в `main` напрямую** — только через PR.
- Маленькие коммиты с понятным сообщением: «Добавить…», «Исправить…», «Удалить…».
- **Никогда не коммить `.env` и токены.** Если случайно закоммитил токен — сразу скажи техлиду: токен надо перевыпустить, удаления из истории недостаточно.
- Перед началом дня — `git pull`, перед PR — `git pull origin main` в свою ветку.

---

## 5. Когда что-то пошло не так

| Ситуация | Что делать |
|---|---|
| `git push` отклонён: «rejected… fetch first» | Кто-то успел раньше. `git pull --rebase`, потом снова `git push` |
| Конфликт (`CONFLICT` при pull/merge) | VS Code подсветит файлы: выбери «Accept Current / Incoming / Both», сохрани, `git add`, `git rebase --continue` (или `git commit`). Не уверен — позови техлида |
| Хочу отменить изменения в файле, ещё не коммитил | `git restore путь/к/файлу` (необратимо!) |
| Закоммитил, но не отправил, хочу исправить сообщение | `git commit --amend` |
| Отправил плохой коммит в свою ветку | Сделай новый коммит с исправлением. `push --force` в общие ветки не использовать |
| Не понимаю, где я | `git status` и `git log --oneline --graph -10` |
| CI красный | Открой PR → Details у упавшей проверки → читай последний шаг с ошибкой |

---

## 6. Карта репозитория

```
max_buisnes_bot/
├─ AGENTS.md / CLAUDE.md    правила для ИИ-агентов (читать первым)
├─ README.md                описание для жюри: запуск, переменные, архитектура (✏️ — дописать)
├─ compose.yaml             запуск всех сервисов одной командой
├─ .env.example             пример настроек (копируется в .env)
├─ Makefile                 короткие команды: make setup / test / lint / dev-api
├─ openapi.yaml             контракт API между мини-аппом и бэкендом
├─ backend/                 Python-сервер
│  ├─ app/main.py           точка входа
│  ├─ app/webhook.py        приём сообщений от MAX
│  ├─ app/bot/              логика бота: router (кто что обрабатывает), handlers (сценарии), keyboards
│  ├─ app/api/              API для мини-приложения
│  ├─ app/core/             общее ядро: БД, модели, клиент MAX, аналитика, тексты, адаптеры данных
│  └─ tests/                автотесты и примеры апдейтов MAX
├─ miniapp/                 мини-приложение (React + MAX UI)
│  └─ src/texts.ts          тексты мини-аппа (правит UX/UI)
├─ content/texts.yaml       тексты бота (правит UX/UI)
├─ seed/                    тестовые данные
├─ deploy/Caddyfile         HTTPS на сервере
├─ docs/                    STATUS, решения, шпаргалка MAX API, тест-кейсы, спеки экранов, промпты
└─ .github/                 CI, шаблоны задач и PR
```

## 7. Словарь команд
| Команда | Что делает |
|---|---|
| `git status` | Что изменено и что готово к коммиту |
| `git add <файл>` / `git add -A` | Пометить изменения для коммита |
| `git commit -m "..."` | Сохранить снимок |
| `git log --oneline` | История коммитов |
| `git diff` | Показать изменения построчно |
| `git switch <ветка>` / `git switch -c <новая>` | Перейти на ветку / создать и перейти |
| `git pull` | Забрать изменения с GitHub |
| `git push` | Отправить свои коммиты на GitHub |
| `git worktree add ../копия -b lane/front` | Вторая рабочая копия для параллельного агента |
| `gh issue list` / `gh issue view 12` | Задачи |
| `gh pr create --fill` / `gh pr checks` | Открыть PR / статус проверок |
