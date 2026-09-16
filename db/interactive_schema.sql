CREATE TABLE IF NOT EXISTS book_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER,
    chapter_number INTEGER,
    title TEXT,
    pages_range TEXT,
    raw_ocr_text TEXT,
    status TEXT DEFAULT 'pending',
    FOREIGN KEY(book_id) REFERENCES books(id)
);

CREATE TABLE IF NOT EXISTS interactive_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER,
    task_type TEXT, 
    difficulty_level INTEGER DEFAULT 3, 
    content_json TEXT, 
    is_public BOOLEAN DEFAULT 0,
    created_by INTEGER, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chapter_id) REFERENCES book_chapters(id),
    FOREIGN KEY(created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_task_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    task_id INTEGER,
    score INTEGER, 
    attempts INTEGER DEFAULT 1,
    last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(task_id) REFERENCES interactive_tasks(id)
);

CREATE TABLE IF NOT EXISTS audio_podcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER,
    title TEXT,
    audio_url TEXT,
    duration INTEGER,
    FOREIGN KEY(chapter_id) REFERENCES book_chapters(id)
);
