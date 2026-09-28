# dl_bot — бот для скачивания из Instagram и Twitter/X

Whitelist, пул левых инста-аккаунтов, кэш file_id, админка на 8082.

Готово: **этап 1** (база и слой хранения) и **этап 2** (ядро бота).
Впереди: очередь ожидания подписки (этап 3) и админка (этап 4).

## Что уже умеет

- Ловит ссылки инсты и твиттера в любом сообщении, в том числе несколько сразу
- Понимает зеркала (vxtwitter, ddinstagram и прочие) и чистит мусорные параметры
- Инста: посты, рилсы, сторис, хайлайты — через gallery-dl, запасной вариант yt-dlp
- Твиттер: видео через yt-dlp, картинки через gallery-dl, без логина
- Карусели уходят альбомом, разбивка по 10 файлов (лимит телеги)
- Кэш: повтор той же ссылки отдаётся мгновенно по `file_id`, без скачивания
- Пул аккаунтов: нагрузка размазывается, cooldown, протухший акк сам выбывает и задача уходит на следующий
- Алерты владельцу в личку, с троттлингом
- `/queue`, `/cancel`, админские `/allow`, `/ban`, `/stats`

## Установка

```bash
cd /home/pios/dl_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env && chmod 600 .env
python -m db.init_db --key            # ключ -> COOKIES_KEY в .env
# в .env также вписать BOT_TOKEN и ADMIN_TG_ID
python -m db.init_db --admin <твой_tg_id> --username simon
```

Запуск вручную: `python -m bot.main`

Деплой на Pi: `bash deploy/install.sh` — подставит REPLACE_USER/REPLACE_HOME,
поставит юниты, включит еженедельное обновление yt-dlp и gallery-dl.

## Cookies

Пока админки нет, аккаунт добавляется руками:

```python
python - <<'PY'
from accounts import crypto
from db import storage
rel = crypto.store("acc_one", open("cookies.txt","rb").read())
print(storage.add_account("acc_one", rel))
PY
```

Экспортировать `cookies.txt` (формат Netscape) расширением из браузера,
залогиненного **левым** аккаунтом. Не основным.

## Проверка

```bash
python smoke_test.py        # 45 проверок: база, ссылки, шифрование
python smoke_test_bot.py    # 36 проверок: ядро бота на фейковом телеграме
```

Оба гоняются на временной БД и без сети, боевую не трогают.

## Файлы

| Файл | За что отвечает |
|---|---|
| `db/schema.sql`, `db/storage.py` | схема и весь доступ к БД |
| `common/urls.py` | нормализация ссылок, зеркала, тип медиа |
| `accounts/crypto.py` | шифрование cookies |
| `downloaders/base.py` | запуск утилит, классификация ошибок, сбор файлов |
| `downloaders/instagram.py`, `twitter.py` | стратегии скачивания |
| `bot/queue.py` | воркеры, полный цикл задачи, ретраи |
| `bot/sender.py` | отправка, медиагруппы, кэш file_id |
| `bot/handlers.py` | команды и приём ссылок |
| `bot/texts.py` | все тексты в одном месте |
| `bot/alerts.py` | алерты владельцу |
| `deploy/` | systemd юниты + install.sh |
