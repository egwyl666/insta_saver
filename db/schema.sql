-- dl_bot schema
-- Применяется идемпотентно: можно гонять повторно, ничего не сломается.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------
-- Пользователи бота (whitelist)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    tg_id        INTEGER PRIMARY KEY,
    username     TEXT,
    role         TEXT    NOT NULL DEFAULT 'user'   CHECK (role IN ('admin', 'user')),
    status       TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'banned')),
    max_pending  INTEGER,                          -- NULL = берём из settings
    added_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    added_by     INTEGER,
    note         TEXT
);

-- ---------------------------------------------------------------
-- Пул инстаграм-аккаунтов (левые акки, добавляются через админку)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT    NOT NULL UNIQUE,       -- как мы его называем, не обязательно ник
    platform        TEXT    NOT NULL DEFAULT 'instagram'
                            CHECK (platform IN ('instagram', 'twitter')),
    cookies_path    TEXT    NOT NULL,              -- зашифрованный файл в data/cookies/
    status          TEXT    NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'cooldown', 'expired', 'blocked', 'disabled')),
    last_used_at    TEXT,
    last_checked_at TEXT,
    last_ok_at      TEXT,                          -- когда сессия последний раз точно была жива
    tasks_total     INTEGER NOT NULL DEFAULT 0,
    cooldown_until  TEXT,
    added_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_accounts_pick
    ON accounts (platform, status, last_used_at);

-- ---------------------------------------------------------------
-- Задачи на скачивание
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id         INTEGER NOT NULL REFERENCES users (tg_id),
    chat_id       INTEGER NOT NULL,
    message_id    INTEGER,                         -- сообщение "взял в работу", чтобы его потом править
    url           TEXT    NOT NULL,
    url_hash      TEXT    NOT NULL,                -- sha256 от НОРМАЛИЗОВАННОГО url
    source        TEXT    NOT NULL CHECK (source IN ('instagram', 'twitter')),
    media_kind    TEXT             CHECK (media_kind IN ('post', 'reel', 'story', 'highlight', 'unknown')),
    target_user   TEXT,                            -- чей профиль, если смогли распарсить
    status        TEXT    NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'running', 'done', 'failed',
                                            'pending_access', 'expired', 'cancelled')),
    account_id    INTEGER REFERENCES accounts (id),-- каким аккаунтом работали / подписывались
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_check_at TEXT,                            -- для pending_access
    expires_at    TEXT,                            -- TTL ожидания
    error_code    TEXT,
    error_detail  TEXT,
    files_sent    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at    TEXT,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks (tg_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status      ON tasks (status, next_check_at);
CREATE INDEX IF NOT EXISTS idx_tasks_created     ON tasks (created_at);

-- ---------------------------------------------------------------
-- Кэш отданных файлов: второй раз тот же рилс отдаём мгновенно
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS media_cache (
    url_hash   TEXT    NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,         -- порядок в карусели / медиагруппе
    file_id    TEXT    NOT NULL,                   -- telegram file_id
    file_type  TEXT    NOT NULL CHECK (file_type IN ('photo', 'video', 'animation', 'document')),
    size       INTEGER,
    source     TEXT,
    hits       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (url_hash, position)
);

-- ---------------------------------------------------------------
-- Доступ к приватным профилям: свойство ПРОФИЛЯ, а не ссылки
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS private_targets (
    username        TEXT    NOT NULL,
    account_id      INTEGER NOT NULL REFERENCES accounts (id),
    access_state    TEXT    NOT NULL DEFAULT 'none'
                            CHECK (access_state IN ('none', 'requested', 'granted', 'rejected')),
    last_checked_at TEXT,
    next_check_at   TEXT,
    checks          INTEGER NOT NULL DEFAULT 0,
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (username, account_id)
);

CREATE INDEX IF NOT EXISTS idx_private_due
    ON private_targets (access_state, next_check_at);

-- ---------------------------------------------------------------
-- Настройки (правятся из админки, без рестарта)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('pending_ttl_days',      '7'),
    ('max_pending_per_user',  '5'),
    ('max_file_mb',           '49'),
    ('check_interval_1_min',  '60'),    -- первые сутки: раз в час
    ('check_interval_2_min',  '180'),   -- вторые сутки: раз в 3 часа
    ('check_interval_3_min',  '360'),   -- дальше: раз в 6 часов
    ('account_cooldown_min',  '5'),
    ('account_tasks_burst',   '10'),    -- столько задач подряд, потом cooldown
    ('account_rate_cooldown_min', '30'), -- отдых аккаунта после rate limit
    ('cache_enabled',         '1'),
    ('stories_for_private',   '0'),     -- сторис приватных в очередь не ставим: протухнут
    ('tmp_keep_minutes',      '30');

-- ---------------------------------------------------------------
-- Лог событий
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id      INTEGER,
    task_id    INTEGER,
    account_id INTEGER,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_logs_created ON logs (created_at);
CREATE INDEX IF NOT EXISTS idx_logs_event   ON logs (event, created_at);
