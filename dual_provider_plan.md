# План: Два провайдера под управлением Hermes (v2, с улучшениями)

## Правило жизненного цикла системы (invariant)

1. **GUI — единственный entry point** для пользователя. Двойной клик по `Launch GUI.bat`.
2. **Hermes запускается только после** того, как прокси подняты и config.yaml исправлен.
3. **Если Hermes уже был запущен** — GUI принудительно останавливает его перед продолжением.
4. **Все изменения config.yaml — с автобэкапом** и возможностью восстановления.
5. **Пользователь вмешивается только** при ошибках авторизации или восстановления конфига.
6. **Закрытие GUI = полная остановка** всех процессов (proxy, auth-Chrome, watchdog, Hermes).
7. **Никаких фоновых остатков** после закрытия GUI.

---

## Улучшения, внесённые в план (по результатам review)

### Безопасность
- **B1. Gated start Hermes**: Hermes не стартует, если `best-effort=false` и хотя бы один провайдер DOWN. Предотвращает ситуацию “пользователь думает, что работает через Qwen+DeepSeek, а Hermes упал”.
- **B2. File lock при merge config.yaml**: предотвращает гонку, если пользователь параллельно правит config вручную.
- **B3. Checksum config.yaml**: после merge записываем `sha256` в `state.json`. При следующем старте GUI проверяем, не изменялся ли файл под ногами.
- **B4. Recursive kill по PID tree**: при закрытии GUI или отмене `re_auth` — убиваем все дочерние процессы (CTRL_BREAK_EVENT + taskkill /T /F).

### Надёжность
- **S2. re_auth с cleanup**: при таймауте/отмене — убиваем auth-Chrome, не оставляем процессы висеть.
- **S6. Token validation в health**: проверяем размер `token` в auth-файлах и флаг `invalid`. Если токен битый — возвращаем `healthy=False, detail="token_invalid"`.
- **S7. Persistent логирование**: append-only лог `ProviderManager.log` с ротацией 50KB.

### UX
- **U1. Toggle «Hermes + оба провайдера»**: одна кнопка вместо разрозненных старт/стоп.
- **U2. Progress during re_auth**: GUI показывает таймер и статус, кнопка «Отмена».
- **U4. Автовосстановление Hermes**: если PID существует, но health DOWN — предлагаем перезапуск.

---

## Фазы реализации

### Phase 0: Подготовка (стандартизация)

**Существует и требует минимальных правок:**
- `hermes-dev/ProviderManager/provider_manager.py` — уже правлен (qwen `re_auth_cmd`, generic `re_auth()`)
- `hermes-dev/ProviderManager/gui.py` — уже правлен (кнопка Перелогин для qwen)

**Создаём:**
- `hermes-dev/ProviderManager/hermes_provider_config.py` — безопасный merge config.yaml с бэкапом, file lock, checksum
- `hermes-dev/ProviderManager/launch_gui.py` — entry point для двойного клика: orchestrator полного цикла
- `hermes-dev/ProviderManager/provider_tool.py` — Hermes tool (start/stop/health/re_auth)
- `hermes-dev/ProviderManager/watchdog.py` — внешний watchdog

### Phase 1: Разблокировка P0 (базовая работоспособность)

**1.1 Auth-скрипты: убрать терминальный Enter**
- `FreeQwenApi/scripts/qwen_chrome_auth.cjs` — удалить `ask()`, запустить polling сразу
- `FreeDeepseekAPI/scripts/deepseek_chrome_auth.js` — удалить `ask()`, запустить polling сразу
- Таймаут: 5 минут
- Cleanup: `if (!reuseChrome) kill Chrome process` в конце (успех или таймаут)
- На Windows: `taskkill /F /PID <pid>` или через `spawn('taskkill', ...)`

**1.2 re_auth() блокирующий + recursive kill**
- `provider_manager.py` — `subprocess.Popen` → `subprocess.run` с `timeout=360` (6 мин)
- При отмене/таймауте: `CTRL_BREAK_EVENT` на process group + `taskkill /T /F /PID`
- GUI polling статуса: обновление каждую секунду

**1.3 Provider entries в config.yaml**
- `hermes_provider_config.py`:
  - Находит активный `config.yaml` (`.hermes/config.yaml` + профили)
  - Автобэкап (`.hermes/backups/config.yaml.TIMESTAMP.bak`, last 5)
  - Merge только нужных ключей (`custom:freeqwen`, `custom:freedeep`), идемпотентно
  - File lock во время merge (на Windows через `portalocker` или `msvcrmode`)
  - Валидация YAML; при ошибке — восстановление из бэкапа
  - Запись `sha256` последнего удачного merge в `state.json`
- Формат entries (без `tools:`!):

```yaml
custom:freeqwen:
  base_url: http://127.0.0.1:3264/api/v2
  api_key: dummy
  models:
    qwen3.7-max: {}
    qwen3-coder-plus: {}

custom:freedeep:
  base_url: http://127.0.0.1:9655/v1
  api_key: dummy
  models:
    deepseek-chat: {}
```

**1.4 Закрытие GUI — полная остановка**
- `launch_gui.py` (или в `gui.py` при закрытии):
  - `ProviderController.stop()` оба провайдера
  - Рекурсивный kill auth-Chrome (9334/9335) + proxy процессы
  - Если Hermes был запущен этим GUI — stop
  - Таймаут на kill: 3 сек на операцию

### Phase 2: Чистота ресурсов (P1)

**2.1 stop() дополнен убийством auth-Chrome**
- `provider_manager.py`, метод `stop()`:
  - Существующий: taskkill proxy-PID + netstat fallback + stop_cmd
  - Добавить: fallback kill by `--remote-debugging-port=9334/9335`
  - Или: найти процессы с `--user-data-dir=...profile-<name>` и убить

**2.2 Логирование**
- `ProviderManager.log` (append-only)
- Ротация: 50KB, хранить 3 файла
- Записывать: start/stop/re_auth/health check/ошибки

**2.3 Token validation в is_healthy()**
- Для qwen: читать `session/tokens.json`, проверять `token.length > 10` и `!invalid`
- Для deepseek: читать `deepseek-auth.json`, проверять `token.length > 10`
- Если токен битый — `return False, "token_invalid"`

### Phase 3: Автономность (P2)

**3.1 Watchdog**
- `watchdog.py` — запускается как detached process из GUI
- Каждые 10 минут: health check оба провайдера
- При DOWN:
  1. Попытка `start()`
  2. Если не поднялся — проверяет auth-файл
  3. Если auth-файл есть — запускает `re_auth()`
  4. Логирует все действия
- При 401/403 в health body — сразу `re_auth()`

**3.2 Gated start Hermes**
- Hermes стартует только если:
  - `best-effort=true` (разрешено запускаться с частичной доступностью)
  - ИЛИ оба провайдера healthy
  - ИЛИ хотя бы один healthy и пользователь подтвердил “запустить с одним”
- В `launch_gui.py` проверка перед стартом Hermes

### Phase 4: UX и параллельность (P3)

**4.1 Toggle «Hermes + оба провайдера»**
- Одна кнопка ON/OFF
- GUI сама решает: start proxies → start Hermes / stop Hermes → stop proxies

**4.2 Progress during re_auth**
- Кнопка «Перелогин» → меняется на «Отмена авторизации...» + таймер
- `log_var` показывает: “Авторизация Qwen... 12 сек”
- При отмене: recursive kill + возврат кнопки

**4.3 «Перелогин обоих»**
- Параллельный запуск двух `re_auth` в потоках
- Отдельный прогресс для каждого

**4.4 Stream enforcement для Qwen**
- В `qwen_light_proxy.py` (или отдельный middleware):
  - При POST `/v1/chat/completions` принудительно `"stream": true`
  - Это убирает зависимость от клиента (Hermes/delegate_task)

---

## Таймауты и поллинг (технические параметры)

### Жёсткие таймауты

| Операция | Таймаут | Действие при превышении |
|----------|---------|------------------------|
| Чтение/merge/запись config.yaml | **5 сек** | Ошибка, восстановление из бэкапа, запрос пользователю |
| `ProviderController.start()` health | **15 сек** | Возврат `started_but_not_healthy` |
| `re_auth()` (полный цикл) | **6 мин** | Kill chrome + дети, возврат `failed:timeout` |
| Kill процесса (stop) | **3 сек** | Логирование ошибки, продолжение shutdown |
| HTTP health check | **3 сек** | Считать DOWN, следующий тик watchdog |

### Поллинг

| Операция | Интервал | Таймаут/критерий |
|----------|----------|------------------|
| Hermes startup | 1 сек | PID найден + health UP / 30 сек |
| re_auth progress | 1 сек | Exit-код / 6 мин |
| Watchdog health | 10 мин | Бесконечно (пока GUI жив) |
| GUI refresh status | 2 сек | GUI запущен |

---

## Структура файлов (что существует, что создаём/изменяем)

**Существует и правки:**
- `FreeQwenApi/scripts/qwen_chrome_auth.cjs` — убрать `ask()`, добавить cleanup
- `FreeDeepseekAPI/scripts/deepseek_chrome_auth.js` — убрать `ask()`, добавить cleanup
- `hermes-dev/ProviderManager/provider_manager.py` — ре-auth блокирующий, recursive kill, token validation, логирование
- `hermes-dev/ProviderManager/gui.py` — toggle, progress auth, integration с `hermes_provider_config.py`, кнопка «оба»

**Создаём:**
- `hermes-dev/ProviderManager/hermes_provider_config.py` — merge config.yaml с бэкапом, lock, checksum
- `hermes-dev/ProviderManager/launch_gui.py` — orchestrator lifecycle
- `hermes-dev/ProviderManager/provider_tool.py` — Hermes tool
- `hermes-dev/ProviderManager/watchdog.py` — внешний watchdog

**Изменяем:**
- `hermes-dev/restore.ps1` — добавить вызов merge providers + restore config
- `hermes-dev/ProviderManager/ProviderManager.log` — новый, ротация

---

## Следующие шаги (кодовая задача)

При утверждении начинаю с **Phase 1 (P0)**:

1. `hermes_provider_config.py` — модуль merge с бэкапом, file lock, checksum
2. `qwen_chrome_auth.cjs` — убрать `ask()`, добавить polling + cleanup
3. `deepseek_chrome_auth.js` — убрать `ask()`, добавить polling + cleanup
4. `provider_manager.py` — re_auth блокирующий, recursive kill, token validation, logging
5. `gui.py` — toggle, progress auth, кнопка «оба», вызов `hermes_provider_config.py`
6. `launch_gui.py` — orchestrator: stop Hermes → merge config → start proxies → start Hermes → watchdog
