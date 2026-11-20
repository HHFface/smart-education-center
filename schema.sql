-- SMART EDUCATION CENTER: ma'lumotlar bazasi sxemasi (SQLite).
-- Hamma vaqt UTC da "YYYY-MM-DD HH:MM:SS" ko'rinishida saqlanadi.
-- Fayl takroran bajarilishi xavfsiz (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash    TEXT NOT NULL,
    role             TEXT NOT NULL CHECK (role IN ('admin','teacher','student','parent')),
    first_name       TEXT NOT NULL DEFAULT '',
    last_name        TEXT NOT NULL DEFAULT '',
    phone            TEXT,
    email            TEXT,
    birth_date       TEXT,
    avatar           TEXT,
    lang             TEXT NOT NULL DEFAULT 'uz',
    theme            TEXT NOT NULL DEFAULT 'system',
    is_active        INTEGER NOT NULL DEFAULT 1,
    email_verified   INTEGER NOT NULL DEFAULT 0,
    phone_verified   INTEGER NOT NULL DEFAULT 0,
    telegram_chat_id TEXT,
    link_code        TEXT,
    link_code_expires TEXT,
    reminder_minutes TEXT NOT NULL DEFAULT '1440,60,10',
    last_seen        TEXT,
    failed_logins    INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    created_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users(email) WHERE email IS NOT NULL AND email <> '';
CREATE INDEX IF NOT EXISTS ix_users_role ON users(role);
CREATE INDEX IF NOT EXISTS ix_users_chat ON users(telegram_chat_id);

CREATE TABLE IF NOT EXISTS user_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT,
    ip         TEXT,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);

-- Tezlik cheklovi: login, parolni tiklash, ro'yxatdan o'tish urinishlari (IP / identifikator bo'yicha)
CREATE TABLE IF NOT EXISTS rate_hits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket     TEXT NOT NULL,
    key        TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rate_hits ON rate_hits(bucket, key, created_at);

CREATE TABLE IF NOT EXISTS tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose    TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    role       TEXT NOT NULL CHECK (role IN ('teacher','student','parent','admin')),
    student_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    group_id   INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    expires_at TEXT NOT NULL,
    used_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    entity     TEXT,
    entity_id  INTEGER,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_log(created_at);

-- ------------------------------------------------------------ fanlar, kurslar, guruhlar
CREATE TABLE IF NOT EXISTS subjects (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS courses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    name       TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'A1',
    price      INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id),
    name       TEXT NOT NULL,
    teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    room       TEXT,
    is_active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS group_students (
    group_id   INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at  TEXT NOT NULL,
    PRIMARY KEY (group_id, student_id)
);

CREATE TABLE IF NOT EXISTS parent_students (
    parent_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, student_id)
);

CREATE TABLE IF NOT EXISTS student_levels (
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    level      TEXT NOT NULL DEFAULT 'A1',
    PRIMARY KEY (student_id, subject_id)
);

CREATE TABLE IF NOT EXISTS lessons (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id  INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    ends_at   TEXT NOT NULL,
    room      TEXT,
    topic     TEXT,
    status    TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_lessons_start ON lessons(starts_at);
CREATE INDEX IF NOT EXISTS ix_lessons_group ON lessons(group_id);

-- ------------------------------------------------------------ davomat
CREATE TABLE IF NOT EXISTS attendance (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id  INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN ('present','late','excused','absent')),
    marked_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE (lesson_id, student_id)
);

CREATE TABLE IF NOT EXISTS absence_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id  INTEGER NOT NULL REFERENCES subjects(id),
    lesson_date TEXT NOT NULL,
    reason      TEXT NOT NULL,
    comment     TEXT,
    return_date TEXT,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected','info_needed')),
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    review_note TEXT,
    created_at  TEXT NOT NULL,
    file_name   TEXT
);

-- ------------------------------------------------------------ uy vazifalari
CREATE TABLE IF NOT EXISTS homework (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    due_at      TEXT NOT NULL,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    homework_id  INTEGER NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
    student_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL DEFAULT 'text',
    text         TEXT,
    file_name    TEXT,
    is_late      INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','checked')),
    score        INTEGER,
    feedback     TEXT,
    submitted_at TEXT NOT NULL,
    checked_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    checked_at   TEXT,
    UNIQUE (homework_id, student_id)
);

CREATE TABLE IF NOT EXISTS homework_misses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    homework_id INTEGER NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('warning','penalty','excused')),
    coins       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE (homework_id, student_id)
);

-- ------------------------------------------------------------ Smart Points
CREATE TABLE IF NOT EXISTS student_stats (
    student_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    xp         INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
    coins      INTEGER NOT NULL DEFAULT 0 CHECK (coins >= 0)
);

CREATE TABLE IF NOT EXISTS points_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('xp','coin')),
    amount           INTEGER NOT NULL,
    requested_amount INTEGER NOT NULL,
    reason_code      TEXT NOT NULL,
    reason           TEXT,
    ref_type         TEXT,
    ref_id           INTEGER,
    created_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL,
    revoked          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_points_student ON points_log(student_id, created_at);
CREATE INDEX IF NOT EXISTS ix_points_ref ON points_log(ref_type, ref_id);

CREATE TABLE IF NOT EXISTS disputes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ref_type    TEXT NOT NULL,
    ref_id      INTEGER NOT NULL,
    message     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','rejected')),
    resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

-- ------------------------------------------------------------ savollar va Battle
CREATE TABLE IF NOT EXISTS challenges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    group_id   INTEGER REFERENCES groups(id) ON DELETE SET NULL,
    title      TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'A1',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER NOT NULL REFERENCES subjects(id),
    level         TEXT NOT NULL DEFAULT 'A1',
    qtype         TEXT NOT NULL CHECK (qtype IN ('vocabulary','grammar','translation','listening','reading')),
    text          TEXT NOT NULL,
    passage       TEXT,
    audio_url     TEXT,
    options       TEXT NOT NULL,
    correct_index INTEGER NOT NULL,
    explanation   TEXT,
    challenge_id  INTEGER REFERENCES challenges(id) ON DELETE CASCADE,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_active     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_questions_pick ON questions(subject_id, level, qtype, is_active);

CREATE TABLE IF NOT EXISTS battle_ratings (
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL,
    games      INTEGER NOT NULL DEFAULT 0,
    wins       INTEGER NOT NULL DEFAULT 0,
    losses     INTEGER NOT NULL DEFAULT 0,
    draws      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (student_id, subject_id)
);

CREATE TABLE IF NOT EXISTS battles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id      INTEGER NOT NULL REFERENCES subjects(id),
    kind            TEXT NOT NULL,
    level           TEXT NOT NULL DEFAULT 'A1',
    challenger_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    opponent_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_id    INTEGER REFERENCES challenges(id) ON DELETE SET NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','counter','scheduled','ready','active','finished',
                                      'declined','expired','cancelled','no_show','void')),
    quick           INTEGER NOT NULL DEFAULT 0,
    scheduled_at    TEXT,
    expires_at      TEXT,
    lobby_deadline  TEXT,
    proposed_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    question_ids    TEXT,
    started_at      TEXT,
    finished_at     TEXT,
    winner_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    no_show_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    suspicious      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_battles_status ON battles(status);

CREATE TABLE IF NOT EXISTS battle_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id   INTEGER NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    proposed_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    starts_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS battle_players (
    battle_id     INTEGER NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ready         INTEGER NOT NULL DEFAULT 0,
    rating_before INTEGER,
    rating_after  INTEGER,
    correct       INTEGER NOT NULL DEFAULT 0,
    wrong         INTEGER NOT NULL DEFAULT 0,
    total_time_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (battle_id, user_id)
);

CREATE TABLE IF NOT EXISTS battle_answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id   INTEGER NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    q_index     INTEGER NOT NULL,
    choice      INTEGER,
    is_correct  INTEGER NOT NULL DEFAULT 0,
    time_ms     INTEGER NOT NULL DEFAULT 0,
    answered_at TEXT NOT NULL,
    UNIQUE (battle_id, user_id, q_index)
);

-- Mashq rejimi: rasmiy reytingga ta'sir qilmaydi, natijalari alohida saqlanadi
CREATE TABLE IF NOT EXISTS practice_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id   INTEGER NOT NULL REFERENCES subjects(id),
    kind         TEXT NOT NULL,
    level        TEXT NOT NULL,
    question_ids TEXT NOT NULL,
    answers      TEXT NOT NULL DEFAULT '{}',
    correct      INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done')),
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_practice_user ON practice_sessions(user_id, id);

CREATE TABLE IF NOT EXISTS battle_reminders_sent (
    battle_id INTEGER NOT NULL REFERENCES battles(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    minutes   INTEGER NOT NULL,
    PRIMARY KEY (battle_id, user_id, minutes)
);

-- ------------------------------------------------------------ Smart Shop
CREATE TABLE IF NOT EXISTS shop_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    price_coins INTEGER NOT NULL CHECK (price_coins >= 0),
    stock       INTEGER,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS shop_orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL REFERENCES shop_items(id),
    price      INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','fulfilled','cancelled')),
    created_at TEXT NOT NULL,
    handled_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- ------------------------------------------------------------ o'quv natijalari
CREATE TABLE IF NOT EXISTS exam_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id  INTEGER NOT NULL REFERENCES subjects(id),
    exam_type   TEXT NOT NULL,
    title       TEXT,
    score       REAL NOT NULL,
    max_score   REAL NOT NULL DEFAULT 100,
    official    INTEGER NOT NULL DEFAULT 0,
    taken_on    TEXT NOT NULL,
    comment     TEXT,
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS skill_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    skill      TEXT NOT NULL CHECK (skill IN ('grammar','vocabulary','listening','reading','speaking','writing')),
    period     TEXT NOT NULL,
    score      INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    comment    TEXT,
    UNIQUE (student_id, subject_id, skill, period)
);

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id  INTEGER REFERENCES subjects(id),
    title       TEXT NOT NULL,
    target_date TEXT,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','done')),
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS goal_tasks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    title   TEXT NOT NULL,
    due_on  TEXT,
    done    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vocab_words (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER NOT NULL REFERENCES subjects(id),
    level         TEXT NOT NULL DEFAULT 'A1',
    word          TEXT NOT NULL,
    translation   TEXT NOT NULL,
    pronunciation TEXT,
    example       TEXT,
    audio_url     TEXT
);

CREATE TABLE IF NOT EXISTS vocab_progress (
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id     INTEGER NOT NULL REFERENCES vocab_words(id) ON DELETE CASCADE,
    box         INTEGER NOT NULL DEFAULT 1,
    reps        INTEGER NOT NULL DEFAULT 0,
    next_review TEXT NOT NULL,
    PRIMARY KEY (student_id, word_id)
);

CREATE TABLE IF NOT EXISTS club_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK (kind IN ('speaking','movie')),
    subject_id  INTEGER REFERENCES subjects(id),
    title       TEXT NOT NULL,
    starts_at   TEXT NOT NULL,
    description TEXT,
    questions   TEXT,
    new_words   TEXT,
    reward_xp   INTEGER NOT NULL DEFAULT 10,
    reward_coin INTEGER NOT NULL DEFAULT 5,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS club_attendees (
    event_id   INTEGER NOT NULL REFERENCES club_events(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    attended   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (event_id, student_id)
);

CREATE TABLE IF NOT EXISTS certificates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    number     TEXT NOT NULL UNIQUE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES subjects(id),
    course     TEXT NOT NULL,
    level      TEXT,
    issued_at  TEXT NOT NULL,
    issued_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT,
    revoked_reason TEXT
);

-- ------------------------------------------------------------ moliya
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id   INTEGER REFERENCES courses(id) ON DELETE SET NULL,
    amount      INTEGER NOT NULL CHECK (amount >= 0),
    discount    INTEGER NOT NULL DEFAULT 0,
    due_date    TEXT NOT NULL,
    paid_at     TEXT,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paid','cancelled')),
    method      TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    note        TEXT,
    reminded    INTEGER NOT NULL DEFAULT 0,
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS payment_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id  INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    amount      INTEGER NOT NULL CHECK (amount > 0),
    method      TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    created_at  TEXT NOT NULL,
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    voided      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_txn_payment ON payment_transactions(payment_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_external ON payment_transactions(method, external_id) WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS expenses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    category   TEXT,
    amount     INTEGER NOT NULL CHECK (amount >= 0),
    spent_on   TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- ------------------------------------------------------------ bildirishnomalar, xabarlar
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    payload    TEXT,
    link       TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, is_read, created_at);

CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    in_app   INTEGER NOT NULL DEFAULT 1,
    telegram INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, kind)
);

CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel    TEXT NOT NULL CHECK (channel IN ('telegram','email')),
    target     TEXT NOT NULL,
    subject    TEXT,
    body       TEXT NOT NULL,
    markup     TEXT,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON outbox(status);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_pair ON messages(from_id, to_id);

CREATE TABLE IF NOT EXISTS ai_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
