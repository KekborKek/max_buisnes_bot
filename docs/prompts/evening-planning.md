Прочитай AGENTS.md, docs/STATUS.md, docs/decisions.md, открытые issues (gh issue list --state open) и git log --since=yesterday.
1) Обнови docs/STATUS.md: что работает, что сломано, решения дня.
2) Предложи задачи на завтра: не больше 6, из разных дорожек, без пересечения по файлам.
Для каждой — черновик по шаблону .github/ISSUE_TEMPLATE/agent-task.md. Сначала покажи список.
3) После моего "ок" создай их: gh issue create --title ... --label lane:<дорожка>,agent-ok --body-file ...
