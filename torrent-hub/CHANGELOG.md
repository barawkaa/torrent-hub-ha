## 3.4.0 — резервные поисковые каталоги (TorAPI / Torlock / JacRed.su)

- TorAPI: документированный объединённый поиск RuTracker, Kinozal, RuTor, NoNameClub без входа на каждом сайте; размер, сиды, метаданные; при отправке magnet запрашивается по ID конкретной раздачи.
- Torlock: готовый профиль стандартного Torznab API без ключа.
- JacRed.su: резервный профиль JacRed для самостоятельной проверки.
- Новые источники добавляются при обновлении существующих настроек ровно один раз и остаются выключенными до индивидуальной проверки; старые маршруты/логины/прокси не изменяются.
- Авторизованные .torrent с RuTracker и Kinozal через TorAPI не обещаем: поддерживаются magnet / infohash при доступном API.
- Раздельная диагностика источников уже предусмотрена в Torrent Hub.
- BitRu и Torrent.by пока не добавлялись без подтверждённого поддерживаемого API и проверенной схемы поисковых результатов.

## 3.3.0 — восстановлена выдача результатов поиска

- Удалён неработающий обработчик удалённого элемента Prowlarr, который прерывал загрузку страницы.
- `smartEnabled` инициализируется до установки обработчиков поиска; результаты появляются по мере поступления от трекеров.
- Для работающих агрегаторов отображается длительность ожидания; ошибки отдельных источников не блокируют выдачу.
- Добавлен интеграционный тест открытия страницы, настоящей отправки запроса и отображения результатов.
- Остальные функции, данные и настройки сохранены.

## 3.2.0 — result filtering, search source resilience

- Smart search default size is 7–150 GiB; only untouched 5–10 GiB saved defaults are migrated.
- After search, faceted filtering by observed title, year, quality, translation, dubbing studio, video source, codec/HDR, content type, country, file size and seeds.
- Live tracker/JacRed metadata survives normalization and duplicate merging.
- The single checkbox disables or enables all aggregators, including old 'main' entries; removed provider selection from the advanced search UI.
- Knaben v2 GET-search API support and migration of the default built-in endpoint.
- Better timeout/HTTP/connection diagnostics for public aggregator APIs, including jac.black. Access depends on each site's real API and network behavior.
- Removed obsolete Prowlarr cleanup UI without deleting old files or stored user data.
- Android 9 compatibility: main page script avoids syntax introduced in Chrome 80+; Android WebView downloads torrent files over a direct HTTP endpoint.
- The previous Home Assistant search engines and locally configured servers remain intact.

# История изменений

## 3.1.0 — авторизация RuTracker и Kinozal

- Логин и пароль прямо в карточках RuTracker и Kinozal, кнопка «Подключить аккаунт».
- Автоматическая отправка формы входа сайта с проверкой авторизации; сессии используются для поиска и .torrent.
- Повторная авторизация после истечения сессии; капча/Cloudflare выводятся как отдельная ошибка.
- Пароли не передаются в JSON-ответах настроек, хранятся только в закрытом файле HA OS.
- Cookie больше не нужно копировать из браузера. CSS-адаптеры скрыты в дополнительных настройках.


## 3.0.1 — локальный доступ без пароля

- Веб-интерфейс на порту 8766 открывается сразу, без логина, пароля и сессии.
- Удалены вкладка «Доступ», форма входа и обработка учётных данных Torrent Hub.
- Сохранены ограничения межсайтовых запросов из браузера.
- Home Assistant Ingress, прямой поиск, умный подбор, прокси, TorrServer и PWA не менялись.
- **Внимание:** не направляйте незащищённый порт 8766 из интернета на Home Assistant.


## 3.0.0 — мобильный интерфейс и защищённый отдельный доступ

- Обычный и расширенный поиск сохранены; добавлена отдельная вкладка «Умный поиск» с регулируемыми ползунками, сохраняемыми предпочтениями и рекомендациями с альтернативами.
- Кнопка «Подобрать лучший вариант» использует результаты обычного поиска.
- Приведён к единому виду мобильный интерфейс; выделены цветом размер, сиды, дата.
- Диагностика сгруппирована по подсистемам, проверка каждого трекера показывает доступность и результаты.
- Можно добавлять простые HTML-источники через настраиваемые CSS-селекторы.
- Добавлены отключенные по умолчанию адаптеры RuTracker и Kinozal с авторизованными Cookie; реальная работа с учётной записью пока не подтверждена.
- Отдельный локальный порт 8766 с собственной авторизацией; HTTPS-публикация через Keenetic и установка мобильной PWA через браузер.
- Прежний Home Assistant Ingress, TorrServer, агрегаторы, прокси и RAM-кеш сохранены.
- Сборка ARM64 проходит до публикации образа в GHCR.

## 2.0.0 — прямой поиск без базы

- Исключены Prowlarr и его .NET-контейнер.
- Реально протестированы прямые адаптеры RuTor, MegaPeer и NNM-Club.
- Постоянная база поиска отсутствует; доступен ограниченный RAM-кеш и его автоочистка.
- Сохранены агрегаторы, magnet, TorrServer, загрузка .torrent и прокси.
