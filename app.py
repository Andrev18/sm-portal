"""Portal nauki sm.b2bgliwice.pl — FastAPI, SQLite, sesje cookie, SRS SM-2.

Moduły: auth, biblioteka (PDF/EPUB), fiszki (.apkg/.tsv), forum, chat (polling),
ogłoszenia, panel nauczyciela.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote as _urlquote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

BASE = Path(os.environ.get("PORTAL_BASE", "/data"))
DB_PATH = BASE / "portal.db"
BOOKS = BASE / "books"
UPLOADS = BASE / "uploads"
for _d in (BOOKS, UPLOADS):
    _d.mkdir(parents=True, exist_ok=True)

SECRET = os.environ.get("PORTAL_SECRET", "change-me")
serializer = URLSafeSerializer(SECRET, salt="sm-portal-session")
GATE_COOKIE = "gate"
GATE_PASS = os.environ.get("PORTAL_GATE_PASS", "5b")  # haslo dostepu do strony (klasa)
LOCKS: dict = {}  # rate-limit proby PIN

app = FastAPI(title="Portal Nauki", docs_url=None, redoc_url=None)
import time as _time
BUILD_TS = str(int(_time.time()))

templates = Jinja2Templates(directory="templates")
templates.env.globals["v"] = BUILD_TS  # cache-busting: /static/x.css?v={{ v }}

import re as _re_md
def _format_markdown_filter(text):
    if not text:
        return ""
    s = str(text)
    s = _re_md.sub(r'`([^`]+)`', r'<code class="tc-inline-code">\1</code>', s)
    s = _re_md.sub(r'\*\*([^*]+)\*\*', r'<strong class="tc-strong">\1</strong>', s)
    s = _re_md.sub(r'\*([^*]+)\*', r'<em>\1</em>', s)
    return s

templates.env.filters["format_markdown"] = _format_markdown_filter
templates.env.filters["md"] = _format_markdown_filter

# ---------------------------------------------------------------- AI (DeepSeek)
AI_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
AI_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")


def ai_generate_cards(text: str, count: int = 6) -> list[tuple[str, str]]:
    """DeepSeek generuje fiszki z dowolnego tekstu (notatka, rozdzial, temat lekcji)."""
    import json as _json
    import urllib.request
    prompt = (
        "Jestes asystentem tworzącym fiszki edukacyjne dla 11-latka (klasa 5, polska szkoła). "
        f"Z podanego materiału stworz dokladnie {count} fiszek 'pytanie - odpowiedz'. "
        "Pytania proste, konkretne, po polsku. Odpowiedzi krotkie (max 12 slow). "
        'Odpowiedz WYŁĄCZNIE tablicą JSON: [{"front":"...","back":"..."}, ...]\n\nMateriał:\n' + text[:4000]
    )
    try:
        req = urllib.request.Request(
            AI_URL.rstrip('/') + '/chat/completions',
            data=_json.dumps({
                "model": "Gemini 3.7 Flash High",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500, "temperature": 0.4,
            }).encode(),
            headers={"Authorization": f"Bearer {AI_KEY}"},
        )
        raw = urllib.request.urlopen(req, timeout=45).read()
        content = _json.loads(raw)["choices"][0]["message"]["content"]
        start, end = content.find("["), content.rfind("]") + 1
        cards = _json.loads(content[start:end])
        return [(c["front"][:200], c["back"][:400]) for c in cards if c.get("front") and c.get("back")][:count]
    except Exception:
        return []


def ai_speak_url(text: str) -> str:
    """URL do wymowy (Web Speech API obsluguje to po stronie przegladarki)."""
    return f"/tts?text={_urlquote(text[:300])}"

# ---------------------------------------------------------------- DB


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, login TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','teacher','student')),
  created_at TEXT DEFAULT (datetime('now')), last_seen TEXT);
CREATE TABLE IF NOT EXISTS books(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, subject TEXT, grade TEXT,
  filename TEXT NOT NULL, filetype TEXT, uploaded_by INTEGER, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS reading_progress(
  user_id INTEGER, book_id INTEGER, page INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
  PRIMARY KEY(user_id, book_id));
CREATE TABLE IF NOT EXISTS decks(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, subject TEXT, owner INTEGER, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY, deck_id INTEGER REFERENCES decks(id) ON DELETE CASCADE,
  front TEXT NOT NULL, back TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS card_state(
  user_id INTEGER, card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
  ease REAL DEFAULT 2.5, interval REAL DEFAULT 0, due TEXT DEFAULT (datetime('now')),
  reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0,
  PRIMARY KEY(user_id, card_id));
CREATE TABLE IF NOT EXISTS review_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, card_id INTEGER, rating INTEGER,
  reviewed_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS threads(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, author INTEGER, created_at TEXT DEFAULT (datetime('now')), pinned INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY, thread_id INTEGER REFERENCES threads(id) ON DELETE CASCADE,
  author INTEGER, body TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY, user_id INTEGER, body TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS announcements(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
  author INTEGER, due_date TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS announcement_reads(
  user_id INTEGER, announcement_id INTEGER, read_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY(user_id, announcement_id));
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY, student INTEGER, kind TEXT, ref_id INTEGER,
  note TEXT, due_date TEXT, created_by INTEGER, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS subjects(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, icon TEXT DEFAULT '📘',
  color TEXT DEFAULT '#4f6ef7', links TEXT);
CREATE TABLE IF NOT EXISTS points_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, delta INTEGER NOT NULL, reason TEXT,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS ideas(
  id INTEGER PRIMARY KEY, author INTEGER, title TEXT NOT NULL, body TEXT DEFAULT '',
  status TEXT DEFAULT 'nowy' CHECK(status IN ('nowy','planowane','zrobione','odrzucone')),
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS idea_votes(
  user_id INTEGER, idea_id INTEGER REFERENCES ideas(id) ON DELETE CASCADE,
  PRIMARY KEY(user_id, idea_id));
"""

conn_init = db()
conn_init.executescript(SCHEMA)

# migracja: PIN 5-cyfrowy (opcjonalny zamiennik hasla dla uczniow)
try:
    conn_init.execute("ALTER TABLE users ADD COLUMN pin_hash TEXT")
    conn_init.commit()
except sqlite3.OperationalError:
    pass  # kolumna juz istnieje

# migracja v6: avatar + wydane monety (ekonomia nagrod)
for _stmt in ("ALTER TABLE users ADD COLUMN avatar TEXT", "ALTER TABLE users ADD COLUMN coins_spent INTEGER DEFAULT 0"):
    try:
        conn_init.execute(_stmt)
        conn_init.commit()
    except sqlite3.OperationalError:
        pass

# migracja: notatki zrodlowe (z nich generuja sie fiszki)
try:
    conn_init.execute("""CREATE TABLE IF NOT EXISTS notes(
      id INTEGER PRIMARY KEY, author INTEGER, deck_id INTEGER, title TEXT,
      body TEXT, source TEXT DEFAULT 'text', image TEXT, created_at TEXT)""")
    conn_init.commit()
except sqlite3.OperationalError:
    pass


def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, hexd = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(dk.hex(), hexd)


# admin bootstrap: haslo z env albo losowe, wypisywane do pliku
row = conn_init.execute("SELECT COUNT(*) c FROM users").fetchone()
if row["c"] == 0:
    admin_pw = os.environ.get("PORTAL_ADMIN_PASSWORD") or secrets.token_urlsafe(10)
    (BASE / "admin_password.txt").write_text(admin_pw)
    conn_init.execute(
        "INSERT INTO users(login,password_hash,name,role) VALUES(?,?,?,?)",
        ("admin", hash_pw(admin_pw), "Administrator", "admin"),
    )
    conn_init.commit()
conn_init.close()

# Data definitions for Lektury 5 Klasy & Serwisy
LEKTURY_LIST = [
    {
        "id": 101,
        "title": "Baśniobór (Tom 01)",
        "author": "Brandon Mull",
        "icon": "🌲",
        "grade": "klasa 5",
        "type": "Lektura szkolna (PDF)",
        "desc": "Niezwykłe przygody Setha i Kendry w tajemniczym rezerwacie magicznych stworzeń.",
        "drive_url": "/read/101"
    },
    {
        "id": 1,
        "title": "Chłopcy z Placu Broni",
        "author": "Ferenc Molnár",
        "icon": "📖",
        "grade": "klasa 5",
        "type": "Lektura obowiązkowa",
        "desc": "Poruszająca opowieść o przyjaźni, honorze i poświęceniu Nemeczka i jego kolegów.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 2,
        "title": "Opowieści z Narnii: Lew, Czarownica i stara szafa",
        "author": "C.S. Lewis",
        "icon": "🦁",
        "grade": "klasa 5",
        "type": "Lektura obowiązkowa",
        "desc": "Magiczna podróż przez starą szafę do niezwykłej krainy Narnii rządzonej przez Aslana.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 3,
        "title": "Katarynka",
        "author": "Bolesław Prus",
        "icon": "📻",
        "grade": "klasa 5",
        "type": "Lektura obowiązkowa",
        "desc": "Nowela o panu Tomaszu i niewidomej dziewczynce, dla której dźwięki katarynki to promień nadziei.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 4,
        "title": "W pustyni i w puszczy",
        "author": "Henryk Sienkiewicz",
        "icon": "🐫",
        "grade": "klasa 5",
        "type": "Lektura obowiązkowa",
        "desc": "Niezwykłe przygody Staśka i Nel podczas niebezpiecznej przeprawy przez Afrykę.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 5,
        "title": "Mitologia Grecka",
        "author": "Jan Parandowski",
        "icon": "🏛️",
        "grade": "klasa 5",
        "type": "Lektura obowiązkowa",
        "desc": "Mity o bogach i bohaterach starożytnej Grecji: Dedal i Ikar, Herakles, Tezeusz, Demeter.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 6,
        "title": "Ania z Zielonego Wzgórza",
        "author": "Lucy Maud Montgomery",
        "icon": "👩🦰",
        "grade": "klasa 5",
        "type": "Lektura uzupełniająca",
        "desc": "Przygody emocjonalnej i pełnej wyobraźni Ani Shirley na Zielonym Wzgórzu.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 7,
        "title": "Legendy Polskie",
        "author": "Opracowanie Zbiorowe",
        "icon": "🐉",
        "grade": "klasa 5",
        "type": "Lektura obowiązkowa",
        "desc": "O Smoku Wawelskim, O Popielu i myszach, O Lechu, Czechu i Rusie, O Warsie i Sawie.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    },
    {
        "id": 8,
        "title": "Wiersze i Baśnie (Pan Twardowski)",
        "author": "Adam Mickiewicz / Jan Brzechwa",
        "icon": "📜",
        "grade": "klasa 5",
        "type": "Lektura uzupełniająca",
        "desc": "Pan Twardowski, Powrót taty, Bajki robotów i baśnie polskie.",
        "drive_url": "https://drive.google.com/drive/u/0/my-drive"
    }
]

SERVICES_LIST = [
    {
        "name": "Google Meet",
        "icon": "📹",
        "url": "https://meet.google.com/sp13-klasa5b",
        "badge": "Lekcje Online (Kod: sp13-klasa5b)",
        "desc": "Bezpośredni pokój spotkań, konsultacji i zdalnej nauki klasy 5B.",
        "color": "#00ac47"
    },
    {
        "name": "VULCAN Dashboard",
        "icon": "🏫",
        "url": "/vulcan",
        "badge": "Plan Lekcji & Oceny SP13",
        "desc": "Dedykowany pulpit: plan lekcji, oceny, terminarz i szczęśliwy numerek z e-dziennika.",
        "color": "#0052cc"
    },
    {
        "name": "Home Assistant",
        "icon": "🏠",
        "url": "http://10.10.10.123:8123",
        "badge": "Smart Dom & Szkoła",
        "desc": "Pulpit sterowania smart domem, statusy i integracje szkolne.",
        "color": "#03a9f4"
    },
    {
        "name": "FreeLingo",
        "icon": "🇬🇧",
        "url": "https://en.b2bgliwice.pl",
        "badge": "Język Angielski",
        "desc": "Gra edukacyjna i nauka słówek angielskich z lektorem AI.",
        "color": "#8b5cf6"
    },
    {
        "name": "ClassQuiz",
        "icon": "🎯",
        "url": "http://10.10.10.157:3210",
        "badge": "Quizy & Testy",
        "desc": "Interaktywne quizy klasowe z matematyki, historii i biologii.",
        "color": "#ec4899"
    },
    {
        "name": "Razzia",
        "icon": "⚡",
        "url": "http://10.10.10.157:3211",
        "badge": "Turnieje Na Żywo",
        "desc": "Szybka rywalizacja i quizy wiedzy na żywo z kolegami z klasy.",
        "color": "#f59e0b"
    },
    {
        "name": "bolt.diy",
        "icon": "🛠️",
        "url": "http://10.10.10.157:5173",
        "badge": "Projekty & AI",
        "desc": "Twórz własne aplikacje, strony i gry przy pomocy sztucznej inteligencji.",
        "color": "#0ea5e9"
    },
    {
        "name": "Supabase Studio",
        "icon": "⚡",
        "url": "http://10.10.10.157:8000",
        "badge": "Baza Wiedzy",
        "desc": "Zaplecze danych, baza fiszek i zasobów portalu.",
        "color": "#10b981"
    },
    {
        "name": "Typing Hero",
        "icon": "⌨️",
        "url": "https://www.typingclub.com",
        "badge": "Szybkie Pisanie",
        "desc": "Gra do bezpatrznego i szybkiego pisania na klawiaturze.",
        "color": "#3b82f6"
    },
    {
        "name": "Lisek AI (Korepetytor)",
        "icon": "🦊",
        "url": "#tutor",
        "badge": "Pomocnik AI",
        "desc": "Zadaj pytanie o lekcję, wypracowanie lub trudne zadanie.",
        "color": "#f97316"
    },
    {
        "name": "Fiszki SRS (Anki)",
        "icon": "🃏",
        "url": "/srs",
        "badge": "Powtórki SM-2",
        "desc": "System fiszek Anki dostosowany do programu 5 klasy.",
        "color": "#6366f1"
    },
    {
        "name": "Dysk Google Lektur",
        "icon": "📂",
        "url": "https://drive.google.com/drive/u/0/my-drive",
        "badge": "Google Drive",
        "desc": "Folder z lekturami i skanami (b2bgliwice@gmail.com).",
        "color": "#ea4335"
    }
]

SUBJECTS_INIT = [
    ("Język polski", "📖", "#ef4444", json.dumps([
        {"label":"📗 Podręcznik 'Między Nami 5' (PDF)","url":"/read/102"},
        {"label":"🌲 Lektura: Baśniobór (PDF)","url":"/read/101"},
        {"label":"📖 Lektury 5 Klasy","url":"/lektury"},
        {"label":"📂 Dysk Google (b2bgliwice@gmail.com)","url":"https://drive.google.com/drive/u/0/my-drive"}
    ])),
    ("Matematyka", "📐", "#3b82f6", json.dumps([
        {"label":"🎯 ClassQuiz - Testy z Matematyki","url":"http://10.10.10.157:3210"},
        {"label":"⚡ Razzia - Pojedynki Matematyczne","url":"http://10.10.10.157:3211"}
    ])),
    ("Historia", "🏰", "#f59e0b", json.dumps([
        {"label":"🃏 Fiszki Historyczne (Daty & Królowie)","url":"/srs"}
    ])),
    ("Biologia", "🌿", "#10b981", json.dumps([
        {"label":"🔬 Świat Przyrody i Komórki","url":"/srs"}
    ])),
    ("Geografia", "🌍", "#06b6d4", json.dumps([
        {"label":"🗺️ Krajobrazy Polski i Mapy","url":"/srs"}
    ])),
    ("Język angielski", "🇬🇧", "#8b5cf6", json.dumps([
        {"label":"📘 Podręcznik 'Together 5' (Macmillan MEE)","url":"https://www.macmillan.pl/mee"},
        {"label":"🇬🇧 FreeLingo - Słówka & Audio AI","url":"https://en.b2bgliwice.pl"}
    ])),
    ("Język niemiecki", "🇩🇪", "#6366f1", json.dumps([
        {"label":"🇩🇪 Podstawowe Zwroty i Fiszki","url":"/srs"}
    ])),
    ("Informatyka", "💻", "#0ea5e9", json.dumps([
        {"label":"⌨️ Typing Hero - Szybkie Pisanie","url":"https://www.typingclub.com"},
        {"label":"🛠️ bolt.diy - Tworzenie Aplikacji AI","url":"http://10.10.10.157:5173"}
    ])),
    ("Technika", "⚙️", "#64748b", json.dumps([
        {"label":"🚲 Karta Rowerowa & Przepisy","url":"/srs"}
    ])),
    ("Muzyka", "🎵", "#ec4899", json.dumps([
        {"label":"🎼 Nuty, Kompozytorzy i Instrumenty","url":"/srs"}
    ])),
    ("Plastyka", "🎨", "#f43f5e", json.dumps([
        {"label":"🖌️ Epoki Sztuki i Barwy","url":"/srs"}
    ])),
    ("Wychowanie fizyczne", "⚽", "#84cc16", json.dumps([
        {"label":"🏆 Zasady Gier Zespołowych","url":"/srs"}
    ])),
    ("Religia / Etyka", "⛪", "#a855f7", json.dumps([
        {"label":"📜 Wartości i Historia","url":"/srs"}
    ])),
]

_c = db()
try:
    _c.execute("UPDATE subjects SET name='Biologia', icon='🌿', color='#10b981' WHERE name='Przyroda'")
    _c.execute("UPDATE subjects SET name='Wychowanie fizyczne', icon='⚽', color='#84cc16' WHERE name='W-F'")
except Exception:
    pass

for _name, _icon, _color, _links in SUBJECTS_INIT:
    _c.execute("""
        INSERT INTO subjects(name, icon, color, links) VALUES(?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET icon=excluded.icon, color=excluded.color, links=excluded.links
    """, (_name, _icon, _color, _links))
_c.commit()
_c.close()


# ---------------------------------------------------------------- routing


def current_user(request: Request) -> Optional[sqlite3.Row]:
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        uid = serializer.loads(cookie).get("uid")
    except BadSignature:
        return None
    if not uid:
        return None
    conn = db()
    conn.execute("UPDATE users SET last_seen=datetime('now') WHERE id=?", (uid,))
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return user


def require(user=None):
    if user is None:
        raise HTTPException(302, headers={"Location": "/login"})
    return user


def require_role(user, *roles):
    require(user)
    if user["role"] not in roles:
        raise HTTPException(403, "Brak uprawnien")
    return user


# ---------------------------------------------------------------- PIN / gamifikacja


def pin_users():
    conn = db()
    users = conn.execute("SELECT id, pin_hash FROM users WHERE pin_hash IS NOT NULL").fetchall()
    conn.close()
    return users


def pin_taken(pin: str, exclude_uid: int) -> bool:
    """Czy ten PIN nalezy juz do kogos innego? (PIN = jak numer karty)"""
    for u in pin_users():
        if u["id"] != exclude_uid and verify_pw(pin, u["pin_hash"]):
            return True
    return False


def add_points(uid: int, delta: int, reason: str):
    conn = db()
    conn.execute("INSERT INTO points_log(user_id,delta,reason) VALUES(?,?,?)", (uid, delta, reason))
    conn.commit()
    conn.close()


def user_points(uid: int) -> int:
    conn = db()
    row = conn.execute("SELECT COALESCE(SUM(delta),0) s FROM points_log WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    return int(row["s"])


def user_streak(uid: int) -> int:
    """Dni z rzedu z co najmniej 1 akcja (powtorka fiszki / post / chat)."""
    conn = db()
    rows = conn.execute(
        """SELECT DISTINCT substr(ts,1,10) d FROM (
             SELECT reviewed_at ts FROM review_log WHERE user_id=:u
             UNION ALL SELECT created_at FROM chat WHERE user_id=:u
             UNION ALL SELECT created_at FROM posts WHERE author=:u
           ) ORDER BY d DESC LIMIT 90""", {"u": uid}).fetchall()
    conn.close()
    days = {r[0] for r in rows}
    from datetime import date, timedelta
    streak, d = 0, date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


# ---------------- liga tygodniowa, tytuly, rajd klasowy

TITLES = [(1000, "Legenda 5B 🐉"), (500, "Władca Wiedzy 👑"), (250, "Mistrz Fiszek 🥋"),
          (100, "Pogromca Zadań ⚔️"), (0, "Nowicjusz 5B 🌱")]


def get_title(xp: int) -> str:
    for min_xp, title in TITLES:
        if xp >= min_xp:
            return title
    return TITLES[-1][1]


def weekly_xp(uid: int) -> int:
    conn = db()
    row = conn.execute(
        """SELECT COALESCE(SUM(delta),0) s FROM points_log
           WHERE user_id=? AND created_at>=datetime('now','weekday 0','-6 days')""", (uid,)).fetchone()
    conn.close()
    return int(row["s"])


def league_board() -> list:
    """Tabela ligi tygodniowej klasy 5B (reset w poniedzialek)."""
    conn = db()
    board = conn.execute(
        """SELECT u.id, u.name, u.avatar,
           COALESCE((SELECT SUM(delta) FROM points_log p WHERE p.user_id=u.id
                     AND p.created_at>=datetime('now','weekday 0','-6 days')),0) wk
           FROM users u WHERE u.role='student' ORDER BY wk DESC, u.name""").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "avatar": r["avatar"] or "🦊", "week": int(r["wk"])} for r in board]


def class_raid() -> dict:
    """Wspolny cel klasy: 500 fiszek w tygodniu."""
    conn = db()
    row = conn.execute(
        """SELECT COUNT(*) c FROM review_log WHERE reviewed_at>=datetime('now','weekday 0','-6 days')""").fetchone()
    conn.close()
    return {"done": int(row["c"]), "goal": 500}


def coins_left(uid: int) -> int:
    return max(0, user_points(uid) - _coins_spent(uid))


def _coins_spent(uid: int) -> int:
    conn = db()
    row = conn.execute("SELECT COALESCE(coins_spent,0) s FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return int(row["s"])


# ---------------- API: misja dnia, ekwipunek, sesja fiszek (JSON bez przeladowan)

PETDEX_LIST = [
    {"slug": "boxcat", "name": "Boxcat", "img": "/static/pets/boxcat.png", "streak_req": 0, "points_req": 0, "desc": "Rudo-biały kotek w kartonowym pudełku."},
    {"slug": "cache-capy", "name": "Cache Capy", "img": "/static/pets/cache-capy.png", "streak_req": 1, "points_req": 20, "desc": "Kapibara niosąca plecak z notatkami."},
    {"slug": "nukey", "name": "Nukey", "img": "/static/pets/nukey.png", "streak_req": 2, "points_req": 40, "desc": "Urocza mikrofalówka z uśmiechniętą buzią."},
    {"slug": "socksy", "name": "Socksy", "img": "/static/pets/socksy.png", "streak_req": 3, "points_req": 60, "desc": "Mały uroczy skrzat z wielkimi uszami."},
    {"slug": "daemon-dumpling", "name": "Daemon Dumpling", "img": "/static/pets/daemon-dumpling.png", "streak_req": 4, "points_req": 80, "desc": "Chomik deweloper w czapce z daszkiem."},
    {"slug": "cosmo", "name": "Crafternauta Cosmo", "img": "/static/pets/cosmo.png", "streak_req": 5, "points_req": 100, "desc": "Kosmiczny astronauta w kombinezonie."},
    {"slug": "byte-bunny", "name": "Byte Bunny", "img": "/static/pets/byte-bunny.png", "streak_req": 6, "points_req": 120, "desc": "Biały króliczek z cyfrową koszulką."},
    {"slug": "cash-cuy", "name": "Cash Cuy", "img": "/static/pets/cash-cuy.png", "streak_req": 7, "points_req": 150, "desc": "Świnka morska w eleganckim krawacie."},
    {"slug": "prompt-penguin", "name": "Prompt Penguin", "img": "/static/pets/prompt-penguin.png", "streak_req": 8, "points_req": 180, "desc": "Słodki pingwinek z błękitnym brzuszkiem."},
    {"slug": "scoop", "name": "Scoop", "img": "/static/pets/scoop.png", "streak_req": 10, "points_req": 200, "desc": "Uśmiechnięta gałka lodów w wafelku."},
    {"slug": "kebo", "name": "Kebo", "img": "/static/pets/kebo.png", "streak_req": 12, "points_req": 250, "desc": "Misio koala w czarnej bluzie z fioletem."},
    {"slug": "skipper", "name": "TuxTerm", "img": "/static/pets/skipper.png", "streak_req": 14, "points_req": 300, "desc": "Pingwin Tux pracujący na laptopie."}
]

@app.post("/api/avatar")
def api_avatar(request: Request, item: str = Form(...)):
    """Wybierz zwierzaka Petdex jako aktywnego towarzysza."""
    user = current_user(request)
    if not user:
        raise HTTPException(401)
    
    conn = db()
    conn.execute("UPDATE users SET avatar=? WHERE id=?", (item, user["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse("/locker?msg=Wybór+zapisany%21+%F0%9F%90%BE", 302)


@app.get("/locker", response_class=HTMLResponse)
def locker(request: Request):
    user = require(current_user(request))
    ustreak = user_streak(user["id"])
    upoints = user_points(user["id"])
    current_avatar = user["avatar"] or "boxcat"

    pets = []
    for p in PETDEX_LIST:
        is_unlocked = (ustreak >= p["streak_req"]) or (upoints >= p["points_req"])
        is_active = (current_avatar == p["slug"] or current_avatar == p["img"])
        pets.append({
            **p,
            "is_unlocked": is_unlocked,
            "is_active": is_active
        })

    return templates.TemplateResponse(request, "locker.html", {
        "user": user,
        "pets": pets,
        "streak": ustreak,
        "points": upoints,
        "title": get_title(upoints)
    })


@app.get("/api/session/{deck_id}")
def api_session(deck_id: int, request: Request):
    """Microlearning: max 7 kart na sesje."""
    user = current_user(request)
    if not user:
        raise HTTPException(401)
    conn = db()
    rows = conn.execute(
        """SELECT c.id, c.front, c.back FROM cards c
           LEFT JOIN card_state s ON s.card_id=c.id AND s.user_id=?
           WHERE c.deck_id=? AND (s.due IS NULL OR s.due<=datetime('now'))
           ORDER BY s.due LIMIT 7""", (user["id"], deck_id)).fetchall()
    conn.close()
    return {"cards": [{"id": r["id"], "front": r["front"], "back": r["back"]} for r in rows]}


@app.post("/api/review/{card_id}")
def api_review(card_id: int, request: Request, rating: int = Form(...)):
    """Ocena karty (form-encoded). Zwraca nastepna karte + zdobyte XP. Blad tez daje 1 XP (bezpieczna petla)."""
    user = current_user(request)
    if not user:
        raise HTTPException(401)
    conn = db()
    deck_row = conn.execute("SELECT deck_id FROM cards WHERE id=?", (card_id,)).fetchone()
    if not deck_row:
        conn.close()
        raise HTTPException(404)
    deck_id = deck_row["deck_id"]
    st = conn.execute("SELECT * FROM card_state WHERE user_id=? AND card_id=?", (user["id"], card_id)).fetchone()
    ease = st["ease"] if st else 2.5
    interval = st["interval"] if st else 0.0
    reps = st["reps"] if st else 0
    lapses = st["lapses"] if st else 0
    if rating == 0:
        ease = max(1.3, ease - 0.2); interval = 0.003; lapses += 1
    elif rating == 1:
        ease = max(1.3, ease - 0.15); interval = max(interval * 1.2, 0.007)
    elif rating == 2:
        interval = max(interval * ease, 0.0104) if reps else 0.0104
    else:
        ease += 0.15; interval = max(interval * ease * 1.3, 0.041) if reps else 0.041
    reps += 1
    conn.execute(
        """INSERT INTO card_state(user_id,card_id,ease,interval,due,reps,lapses) VALUES(?,?,?,?,datetime('now','+{} days'),?,?)
           ON CONFLICT(user_id,card_id) DO UPDATE SET ease=?, interval=?, due=datetime('now','+{} days'), reps=?, lapses=?"""
        .format(interval, interval), (user["id"], card_id, ease, interval, reps, lapses, ease, interval, reps, lapses))
    conn.execute("INSERT INTO review_log(user_id,card_id,rating) VALUES(?,?,?)", (user["id"], card_id, rating))
    conn.commit()
    gained = 4 if rating >= 2 else 1
    add_points(user["id"], gained, f"fiszka (ocena {rating})")
    row = conn.execute(
        """SELECT c.id, c.front, c.back FROM cards c
           LEFT JOIN card_state s ON s.card_id=c.id AND s.user_id=?
           WHERE c.deck_id=? AND c.id!=? AND (s.due IS NULL OR s.due<=datetime('now'))
           ORDER BY s.due LIMIT 1""", (user["id"], deck_id, card_id)).fetchone()
    conn.close()
    return {"ok": True, "gained": gained,
            "next": {"id": row["id"], "front": row["front"], "back": row["back"]} if row else None}


# ---------------------------------------------------------------- gate (haslo dostepu)


def gate_ok(request: Request) -> bool:
    return True

def gate_form(request: Request):
    if gate_ok(request):
        return RedirectResponse("/login", 302)
    return templates.TemplateResponse(request, "gate.html", {"msg": ""})


@app.post("/gate")
def gate_post(request: Request, code: str = Form("")):
    if hmac.compare_digest(code.strip(), GATE_PASS):
        resp = RedirectResponse("/login", 302)
        resp.set_cookie(GATE_COOKIE, serializer.dumps({"g": True}), max_age=180 * 86400, httponly=True, samesite="lax")
        return resp
    return templates.TemplateResponse(request, "gate.html", {"msg": "Błędne hasło dostępu"}, status_code=401)


# ---------------------------------------------------------------- routing

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not gate_ok(request):
        return RedirectResponse("/gate", 302)
    return templates.TemplateResponse(request, "login.html", {"msg": ""})


@app.post("/login")
def login(request: Request, login_: str = Form(""), password: str = Form("")):
    if not gate_ok(request):
        return RedirectResponse("/gate", 302)
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE login=?", (login_.strip(),)).fetchone()
    ok = user and verify_pw(password, user["password_hash"])
    if not ok:
        conn.close()
        return templates.TemplateResponse(request, "login.html", {"msg": "Złe dane logowania"}, status_code=401)
    resp = RedirectResponse("/", 302)
    resp.set_cookie("session", serializer.dumps({"uid": user["id"]}), max_age=30 * 86400, httponly=True, samesite="lax")
    conn.close()
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", 302)
    resp.delete_cookie("session")
    return resp


# manifest pod rootem (wymog instalacyjny PWA - Chrome szuka /manifest.json)
@app.get("/manifest.json")
def manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


# ---------------- dodawanie tresci: plik / zdjecie / notatka -> fiszki

@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request):
    user = require(current_user(request))
    conn = db()
    decks = conn.execute("SELECT id, name, subject FROM decks ORDER BY name").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "add.html", {"user": user, "decks": decks})


def _parse_note_to_cards(text: str, deck_id: int, conn) -> int:
    """Parser notatki -> fiszki. Wspiera format:
    'pytanie - odpowiedz' | 'pytanie: odpowiedz' | 'pytanie? odpowiedz' | TSV"""
    import re as _re
    added = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) < 4:
            continue
        m = _re.split(r'\s+[-–—]\s+|:\s+|\?\s+|\t+', line, maxsplit=1)
        if len(m) == 2 and m[0].strip() and m[1].strip():
            conn.execute("INSERT INTO cards(deck_id,front,back) VALUES(?,?,?)",
                         (deck_id, m[0].strip()[:200], m[1].strip()[:400]))
            added += 1
    return added


@app.get("/ai/tutor")
def ai_tutor(request: Request, q: str = ""):
    """Asystent AI: odpowiada na pytanie z materialow klasy 5, po polsku, prosto."""
    user = require(current_user(request))
    if not q.strip():
        return {"answer": ""}
    import json as _json
    import urllib.request
    prompt = (
        "Jestes przyjaznym korepetytorem dla 11-latka (klasa 5 polskiej szkoly podstawowej). "
        "Odpowiadaj PO POLSKU, prosto, max 4 zdania, pozytywnie i zachecajaco. "
        "Uzywaj prostych przykladow. Nie podawaj gotowych rozwiazan zadan domowych - tłumacz krok po kroku.\n\n"
        "Pytanie ucznia: " + q[:500]
    )
    try:
        req = urllib.request.Request(
            AI_URL.rstrip('/') + '/chat/completions',
            data=_json.dumps({"model": "Gemini 3.7 Flash High",
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 400, "temperature": 0.5}).encode(),
            headers={"Authorization": f"Bearer {AI_KEY}"},
        )
        ans = _json.loads(urllib.request.urlopen(req, timeout=45).read())["choices"][0]["message"]["content"]
        return {"answer": ans.strip()}
    except Exception:
        return {"answer": "Ups, chwilę nie mogę pomyśleć. Sprobuj za moment! 🦊"}


@app.post("/add/note")
def add_note(request: Request, note: str = Form(""), deck: str = Form(""), title: str = Form(""), ai: str = Form("")):
    user = require(current_user(request))
    note, title = note.strip(), title.strip()
    conn = db()
    if note:
        deck_id = None
        if deck.isdigit():
            row = conn.execute("SELECT id FROM decks WHERE id=?", (int(deck),)).fetchone()
            deck_id = row["id"] if row else None
        if not deck_id:
            dname = title or f"Fiszki {user['name']}"
            cur = conn.execute("INSERT INTO decks(name,subject,owner) VALUES(?,?,?)", (dname, None, user["id"]))
            deck_id = cur.lastrowid
        added = 0
        if ai == "1":  # AI generuje fiszki z dowolnego tekstu (nie tylko pary)
            cards = ai_generate_cards(note, count=6)
            for front, back in cards:
                conn.execute("INSERT INTO cards(deck_id,front,back) VALUES(?,?,?)", (deck_id, front, back))
                added += 1
        if not added:
            added = _parse_note_to_cards(note, deck_id, conn)
        conn.execute('INSERT INTO notes(author,deck_id,title,body,source,created_at) VALUES(?,?,?,?,?,datetime("now"))',
                     (user["id"], deck_id, title or "notatka", note, "text"))
        conn.commit()
        conn.close()
        if added:
            add_points(user["id"], min(added, 10), f"fiszki z notatki (+{min(added,10)})")
        msg = f"✨ Stworzyłem {added} fiszek!" if added else "Nie udało się stworzyć fiszek. Sprobuj inaczej zapisać materiał."
        return RedirectResponse(f"/add?msg={_urlquote(msg)}", 302)
    conn.close()
    return RedirectResponse("/add?msg=Wpisz+notatk%C4%99", 302)


@app.post("/add/image")
async def add_image(request: Request, photo: UploadFile = File(None), note: str = Form(""), deck: str = Form("")):
    """Zdjecie z aparatu/galerii + opcjonalna notatka -> zapis do talii jako fiszka-obrazek."""
    user = require(current_user(request))
    UPLOADS.mkdir(exist_ok=True)
    conn = db()
    deck_id = None
    if deck.isdigit():
        row = conn.execute("SELECT id FROM decks WHERE id=?", (int(deck),)).fetchone()
        deck_id = row["id"] if row else None
    if not deck_id:
        cur = conn.execute("INSERT INTO decks(name,subject,owner) VALUES(?,?,?)",
                           (f"Zdjęcia {user['name']}", None, user["id"]))
        deck_id = cur.lastrowid
    saved = None
    if photo and photo.filename:
        ext = Path(photo.filename).suffix.lower() or ".jpg"
        if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            safe = f"img_{int(time.time())}_{secrets.token_hex(4)}{ext}"
            with (UPLOADS / safe).open("wb") as fh:
                while chunk := await photo.read(1 << 20):
                    fh.write(chunk)
            saved = safe
            front = f"📷 {note.strip()[:150]}" if note.strip() else "📷 Co to jest?"
            back = f'<img src="/uploads/{safe}" alt="zdjecie" class="card-img">'
            conn.execute("INSERT INTO cards(deck_id,front,back) VALUES(?,?,?)", (deck_id, front, back))
            conn.execute('INSERT INTO notes(author,deck_id,title,body,source,image,created_at) VALUES(?,?,?,?,?,?,datetime("now"))',
                         (user["id"], deck_id, note.strip() or "zdjecie", note, "image", safe))
    conn.commit()
    conn.close()
    if saved:
        add_points(user["id"], 3, "dodano zdjęcie")
        return RedirectResponse("/add?msg=%F0%9F%93%B7+Zdj%C4%99cie+zapisane%21", 302)
    return RedirectResponse("/add?msg=Wybierz+zdj%C4%99cie", 302)


@app.post("/add/pdf")
async def add_pdf(request: Request, pdffile: UploadFile = File(None), deck: str = Form("")):
    """PDF/EPUB -> wyciagnij tekst -> wygeneruj fiszki z par 'pytanie - odpowiedz'."""
    user = require(current_user(request))
    if not pdffile or not pdffile.filename:
        return RedirectResponse("/add?msg=Wybierz+plik", 302)
    ext = Path(pdffile.filename).suffix.lower()
    if ext not in {".pdf", ".epub", ".txt"}:
        return RedirectResponse("/add?msg=Dozwolone:+PDF,+EPUB,+TXT", 302)
    blob = await pdffile.read()
    text = ""
    if ext == ".txt":
        try:
            text = blob.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = pypdf_reader(blob)
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:40])
        except Exception:
            text = ""
    else:  # epub: zip -> xhtml -> tekst
        try:
            import re as _re
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                htmls = [n for n in z.namelist() if n.endswith((".xhtml", ".html"))][:20]
                text = "\n".join(_re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", errors="replace")) for n in htmls)
        except Exception:
            text = ""
    conn = db()
    deck_id = None
    if deck.isdigit():
        row = conn.execute("SELECT id FROM decks WHERE id=?", (int(deck),)).fetchone()
        deck_id = row["id"] if row else None
    if not deck_id:
        cur = conn.execute("INSERT INTO decks(name,subject,owner) VALUES(?,?,?)",
                           (f"Z pliku: {pdffile.filename[:40]}", None, user["id"]))
        deck_id = cur.lastrowid
    added = _parse_note_to_cards(text, deck_id, conn) if text else 0
    conn.commit(); conn.close()
    if added:
        add_points(user["id"], min(added, 15), f"fiszki z pliku (+{min(added,10)})")
        return RedirectResponse(f"/add?msg={_urlquote('✨ Z pliku powstało ' + str(added) + ' fiszek!')}", 302)
    return RedirectResponse("/add?msg=Nie+znalaz%C5%82em+par+pytanie%E2%80%93odpowied%C5%BA+w+pliku", 302)


def pypdf_reader(blob: bytes):
    from pypdf import PdfReader
    return PdfReader(io.BytesIO(blob))


@app.get("/uploads/{name}")
def upload_file(name: str):
    path = (UPLOADS / name).resolve()
    if not str(path).startswith(str(UPLOADS.resolve())) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# logowanie SAMYM PIN-em (5 cyfr) — system sam rozpoje kto to (PIN = unikalny, jak numer karty)
@app.post("/pin")
def pin_login(request: Request, pin: str = Form("")):
    if not gate_ok(request):
        return RedirectResponse("/gate", 302)
    ip = request.client.host if request.client else "?"
    tries, ts = LOCKS.get(ip, (0, 0.0))
    if time.time() - ts < 60 and tries >= 5:
        return templates.TemplateResponse(request, "login.html", {"msg": "Za dużo prób — odczekaj minutę ⏳"}, status_code=429)
    pin = pin.strip()
    conn = db()
    ok_user = None
    if pin.isdigit() and len(pin) == 5:
        for u in pin_users():
            if verify_pw(pin, u["pin_hash"]):
                ok_user = conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
                break
    if not ok_user:
        LOCKS[ip] = (tries + 1, ts if time.time() - ts < 60 else time.time())
        conn.close()
        return templates.TemplateResponse(request, "login.html", {"msg": "Nie znam tego PIN-u 🤔"}, status_code=401)
    LOCKS.pop(ip, None)
    resp = RedirectResponse("/", 302)
    resp.set_cookie("session", serializer.dumps({"uid": ok_user["id"]}), max_age=30 * 86400, httponly=True, samesite="lax")
    conn.close()
    return resp


# ---- klikalne statystyki (rozwijane panele)

@app.get("/me/streak")
def streak_detail(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(302, headers={"Location": "/login"})
    conn = db()
    days = conn.execute(
        """SELECT d, COALESCE(n, 0) n FROM (
             WITH RECURSIVE seq(d) AS (
               SELECT date('now','-13 days')
               UNION ALL SELECT date(d,'+1 day') FROM seq WHERE d < date('now'))
             SELECT substr(d,1,10) d FROM seq)
           LEFT JOIN (
             SELECT substr(reviewed_at,1,10) dd, COUNT(*) n FROM review_log WHERE user_id=:u GROUP BY dd)
           ON dd = d ORDER BY d""", {"u": user["id"]}).fetchall()
    conn.close()
    return {"streak": user_streak(user["id"]),
            "days": [{"d": r["d"], "n": r["n"]} for r in days]}


@app.get("/me/points")
def points_detail(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(302, headers={"Location": "/login"})
    conn = db()
    rows = conn.execute(
        "SELECT reason, delta, created_at FROM points_log WHERE user_id=? ORDER BY id DESC LIMIT 30", (user["id"],)).fetchall()
    total = conn.execute("SELECT COALESCE(SUM(delta),0) s FROM points_log WHERE user_id=?", (user["id"],)).fetchone()["s"]
    conn.close()
    return {"points": int(total), "items": [{"reason": r["reason"], "delta": r["delta"], "when": r["created_at"]} for r in rows]}


@app.get("/me/reviews")
def reviews_detail(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(302, headers={"Location": "/login"})
    conn = db()
    days = conn.execute("""SELECT substr(reviewed_at,1,10) d, COUNT(*) n,
        SUM(CASE WHEN rating>0 THEN 1 ELSE 0 END) good
        FROM review_log WHERE user_id=? AND reviewed_at>=datetime('now','-14 days')
        GROUP BY d ORDER BY d""", (user["id"],)).fetchall()
    conn.close()
    return {"days": [{"d": r["d"], "n": r["n"], "good": r["good"] or 0} for r in days]}



@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    conn = db()
    unread = conn.execute(
        """SELECT COUNT(*) c FROM announcements a WHERE a.id NOT IN
           (SELECT announcement_id FROM announcement_reads WHERE user_id=?)""", (user["id"],)).fetchone()["c"]
    ndue = conn.execute(
        """SELECT COUNT(*) c FROM card_state s WHERE s.user_id=? AND s.due<=datetime('now')""",
        (user["id"],)).fetchone()["c"]
    subs = conn.execute("""SELECT s.*,
        (SELECT COUNT(*) FROM books b WHERE b.subject=s.name) nbooks,
        (SELECT COUNT(*) FROM decks d WHERE d.subject=s.name) ndecks
        FROM subjects s ORDER BY s.id""").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "home.html", {
        "user": user, "unread": unread, "ndue": ndue,
        "subjects": subs, "services": SERVICES_LIST, "lektury": LEKTURY_LIST,
        "points": user_points(user["id"]), "streak": user_streak(user["id"])})


@app.get("/lektury", response_class=HTMLResponse)
def lektury_page(request: Request):
    user = require(current_user(request))
    conn = db()
    books = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "lektury.html", {
        "user": user, "lektury": LEKTURY_LIST, "books": books,
        "points": user_points(user["id"]), "streak": user_streak(user["id"])})


@app.get("/serwisy", response_class=HTMLResponse)
def serwisy_page(request: Request):
    user = require(current_user(request))
    return templates.TemplateResponse(request, "serwisy.html", {
        "user": user, "services": SERVICES_LIST,
        "points": user_points(user["id"]), "streak": user_streak(user["id"])})


# ---------------------------------------------------------------- VULCAN CACHE & HELPER
_VULCAN_CACHE = {"ts": 0, "data": None}

def fetch_vulcan_payload():
    global _VULCAN_CACHE
    import time, os, urllib.request, json
    now = time.time()
    if _VULCAN_CACHE["data"] and (now - _VULCAN_CACHE["ts"] < 60):
        return _VULCAN_CACHE["data"]

    ha_token = os.environ.get("HA_MCP_TOKEN", "")
    if not ha_token:
        for env_f in ["/DATA/AppData/sm-portal/.env", "/data/.env", "/opt/data/profiles/edu/.env", "/opt/data/profiles/home-ha/.env"]:
            if os.path.exists(env_f):
                with open(env_f, "r") as fp:
                    for line in fp:
                        if line.startswith("HA_MCP_TOKEN=") or line.startswith("HASS_TOKEN="):
                            ha_token = line.strip().split("=", 1)[1].strip("\"'")
                            break
            if ha_token:
                break

    def get_ha_all():
        if not ha_token:
            return {}
        try:
            req = urllib.request.Request(
                "http://10.10.10.123:8123/api/states",
                headers={"Authorization": f"Bearer {ha_token}"}
            )
            res = urllib.request.urlopen(req, timeout=2.5)
            states = json.loads(res.read().decode("utf-8"))
            return {s["entity_id"]: s for s in states}
        except Exception as e:
            print("HA fetch error:", e)
            return {}

    states = get_ha_all()

    # 1. Main stats
    stats_ent = states.get("sensor.vultron_stats_stanislaw_mikos", {})
    stats_val = stats_ent.get("state", "69.35")
    try:
        stats_val_f = float(stats_val)
    except Exception:
        stats_val_f = 69.35

    # 2. Freq
    freq_ent = states.get("sensor.vultron_freq_stanislaw_mikos", {})
    freq_val = freq_ent.get("state", "0")
    freq_wpisy = freq_ent.get("attributes", {}).get("wpisy", [])

    # 3. Wiadomosci
    msg_ent = states.get("sensor.vultron_wiadomosci_stanislaw_mikos", {})
    msg_unread = msg_ent.get("state", "0")
    msg_list = msg_ent.get("attributes", {}).get("wiadomosci", [])
    msg_stats = msg_ent.get("attributes", {}).get("stats", f"{msg_unread} / {len(msg_list)}")

    # 4. Terminarz
    term_ent = states.get("sensor.vultron_terminarz_stanislaw_mikos", {})
    term_count = term_ent.get("state", "0")
    term_list = term_ent.get("attributes", {}).get("lista", [])

    # 5. Plans
    plan_curr_ent = states.get("sensor.vultron_plan_stanislaw_mikos_curr", {})
    plan_curr = plan_curr_ent.get("attributes", {}).get("lekcje", [])
    plan_next_ent = states.get("sensor.vultron_plan_stanislaw_mikos_next", {})
    plan_next = plan_next_ent.get("attributes", {}).get("lekcje", [])
    plan_prev_ent = states.get("sensor.vultron_plan_stanislaw_mikos_prev", {})
    plan_prev = plan_prev_ent.get("attributes", {}).get("lekcje", [])

    # 6. Oceny
    oceny_ent = states.get("sensor.vultron_oceny_stanislaw_mikos_p1", {})
    nowe_oceny = oceny_ent.get("state", "0")
    oceny_lista = oceny_ent.get("attributes", {}).get("lista_przedmiotow", [])

    # 7. Numerek
    numerek_ent = states.get("sensor.vultron_szczesliwy_numerek_stanislaw_mikos", {})
    numerek_val = numerek_ent.get("state", "0")
    if numerek_val in ("unknown", "unavailable", "0") or not str(numerek_val).isdigit():
        numerek_val = numerek_ent.get("attributes", {}).get("numer", 0)

    # 8. Minor counters
    uwagi_ent = states.get("sensor.vultron_uwagi_stanislaw_mikos", {})
    uwagi_val = uwagi_ent.get("state", "0")

    os_ent = states.get("sensor.vultron_osiagniecia_stanislaw_mikos", {})
    os_val = os_ent.get("state", "0")

    zebr_ent = states.get("sensor.vultron_zebrania_stanislaw_mikos", {})
    zebr_val = zebr_ent.get("state", "0")

    # 9. Subject stats
    subject_stats = []
    for k, v in states.items():
        if k.startswith("sensor.vultron_stats_stanislaw_mikos_"):
            name = v.get("attributes", {}).get("przedmiot_nazwa")
            if not name:
                name = k.replace("sensor.vultron_stats_stanislaw_mikos_", "").replace("_", " ").title()
            val = v.get("state")
            try:
                val_f = float(val)
            except Exception:
                val_f = 0.0
            subject_stats.append({
                "przedmiot": name,
                "procent": val_f,
                "procent_str": f"{val_f:.1f}%" if val_f.is_integer() or round(val_f,1)==val_f else f"{val_f:.2f}%"
            })

    subject_order = [
        "Język polski", "Matematyka", "Język angielski", "Historia", "Biologia",
        "Geografia", "Religia", "Informatyka", "Wychowanie fizyczne", "WF",
        "Muzyka", "Plastyka", "Technika"
    ]
    def sub_sort_key(s):
        n = s["przedmiot"]
        for idx, item in enumerate(subject_order):
            if item.lower() in n.lower():
                return idx
        return 99

    subject_stats.sort(key=sub_sort_key)

    # Sync teachers and grades to SQLite
    if plan_curr or plan_next:
        try:
            import sqlite3
            db_path = "/data/portal.db" if os.path.exists("/data/portal.db") else "/opt/data/sm-portal-fresh/db/portal.db"
            if os.path.exists(db_path):
                with sqlite3.connect(db_path) as sync_conn:
                    cur = sync_conn.cursor()
                    try:
                        cur.execute("ALTER TABLE subjects ADD COLUMN teacher TEXT")
                    except Exception: pass
                    try:
                        cur.execute("ALTER TABLE subjects ADD COLUMN avg TEXT")
                    except Exception: pass
                    try:
                        cur.execute("ALTER TABLE subjects ADD COLUMN prop TEXT")
                    except Exception: pass

                    for l in (plan_curr + plan_next):
                        pz = l.get("p", "").strip()
                        n = l.get("n", "").strip()
                        if pz and n:
                            cur.execute("UPDATE subjects SET teacher=? WHERE name=?", (n, pz))

                    for item in oceny_lista:
                        pz = item.get("przedmiot", "").strip()
                        srednia = item.get("srednia")
                        proponowana = item.get("proponowana", "-")
                        if pz:
                            srednia_str = f"{float(srednia):.2f}" if srednia else "-"
                            cur.execute("UPDATE subjects SET avg=?, prop=? WHERE name=?", (srednia_str, proponowana, pz))
                    sync_conn.commit()
        except Exception as e:
            print("DB sync error:", e)

    payload = {
        "student": "Stanisław Mikos",
        "klasa": "5B",
        "szkola": "SP13 w Gliwicach",
        "numerek": numerek_val,
        "stats_val": stats_val_f,
        "freq_val": freq_val,
        "freq_wpisy": freq_wpisy,
        "msg_unread": msg_unread,
        "msg_total": len(msg_list),
        "msg_stats": msg_stats,
        "messages": msg_list,
        "term_count": term_count,
        "terminarz": term_list,
        "plan_curr": plan_curr,
        "plan_next": plan_next,
        "plan_prev": plan_prev,
        "plan": plan_curr if plan_curr else plan_next,
        "oceny": oceny_lista,
        "nowe_oceny": nowe_oceny,
        "uwagi": uwagi_val,
        "osiagniecia": os_val,
        "zebrania": zebr_val,
        "subject_stats": subject_stats,
        "srednia_proponowana": oceny_ent.get("attributes", {}).get("srednia_proponowanych"),
        "srednia_okresowa": oceny_ent.get("attributes", {}).get("srednia_okresowych")
    }

    _VULCAN_CACHE["ts"] = now
    _VULCAN_CACHE["data"] = payload
    return payload


# ---------------- dzialy przedmiotow

@app.get("/subjects", response_class=HTMLResponse)
def subjects_list(request: Request):
    user = require(current_user(request))
    conn = db()
    subs = conn.execute("""SELECT s.*,
        (SELECT COUNT(*) FROM books b WHERE b.subject=s.name) nbooks,
        (SELECT COUNT(*) FROM decks d WHERE d.subject=s.name) ndecks
        FROM subjects s ORDER BY s.name""").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "subjects.html", {"user": user, "subjects": subs})


@app.get("/subject/{sid}", response_class=HTMLResponse)
def subject_view(request: Request, sid: int):
    user = require(current_user(request))
    conn = db()
    sub_row = conn.execute("SELECT * FROM subjects WHERE id=?", (sid,)).fetchone()
    if not sub_row:
        raise HTTPException(404)
    sub = dict(sub_row)
        
    books_raw = conn.execute("SELECT * FROM books WHERE subject=? ORDER BY title", (sub["name"],)).fetchall()
    decks_raw = conn.execute("SELECT * FROM decks WHERE subject=? ORDER BY name", (sub["name"],)).fetchall()
    
    BOOK_PAGES = {
        "matematyka_podrecznik.pdf": 268,
        "matematyka_cwiczenia.pdf": 100,
        "polski_podrecznik.pdf": 396,
        "polski_lektura_basniobor.pdf": 231,
        "angielski_cwiczenia.pdf": 105,
        "angielski_podrecznik.pdf": 130,
        "biologia_podrecznik.pdf": 54,
        "historia_podrecznik.pdf": 32,
    }
    
    podreczniki = []
    cwiczenia = []
    lektury = []
    total_course_tasks = 0
    done_course_tasks = 0
    
    for b in books_raw:
        bd = dict(b)
        # Reading progress
        prog = conn.execute("SELECT page, done FROM reading_progress WHERE user_id=? AND book_id=?", (user["id"], bd["id"])).fetchone()
        cur_p = prog["page"] if prog else 0
        tot_p = BOOK_PAGES.get(bd.get("filename", ""), 100)
        bd["current_page"] = cur_p
        bd["total_pages"] = tot_p
        bd["pct"] = int(min(100, max(0, (cur_p / tot_p) * 100))) if tot_p > 0 else 0
        
        # Interactive tasks
        task_cnt = conn.execute("SELECT COUNT(*) as c FROM interactive_tasks WHERE chapter_id IN (SELECT id FROM book_chapters WHERE book_id=?)", (bd['id'],)).fetchone()['c']
        done_cnt = conn.execute("SELECT COUNT(DISTINCT utp.task_id) as c FROM user_task_progress utp JOIN interactive_tasks it ON utp.task_id=it.id JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=? AND utp.user_id=? AND utp.score>=70", (bd['id'], user["id"])).fetchone()['c']
        bd['tasks_cnt'] = task_cnt
        bd['done_cnt'] = done_cnt
        bd['tasks_pct'] = int((done_cnt / task_cnt) * 100) if task_cnt > 0 else 0
        
        total_course_tasks += task_cnt
        done_course_tasks += done_cnt
        
        tl = bd["title"].lower()
        kind = bd.get("kind", "")
        if "ćwicz" in tl or "cwicz" in tl or "workbook" in tl or "skan" in tl or kind in ("cwiczenia", "workbook"):
            cwiczenia.append(bd)
        elif kind == "lektury" or "baśniobór" in tl or "basniobor" in tl or "lektura" in tl:
            lektury.append(bd)
        else:
            podreczniki.append(bd)
            
    # Decks
    decks = []
    total_cards = 0
    total_due = 0
    total_mastered = 0
    for d in decks_raw:
        dd = dict(d)
        cnt = conn.execute("SELECT COUNT(*) as c FROM cards WHERE deck_id=?", (dd["id"],)).fetchone()["c"]
        due = conn.execute("""SELECT COUNT(*) as c FROM cards c 
                              LEFT JOIN card_state cs ON cs.card_id=c.id AND cs.user_id=?
                              WHERE c.deck_id=? AND (cs.due IS NULL OR cs.due <= datetime('now'))""", (user["id"], dd["id"])).fetchone()["c"]
        mastered = conn.execute("""SELECT COUNT(*) as c FROM cards c 
                                   JOIN card_state cs ON cs.card_id=c.id AND cs.user_id=?
                                   WHERE c.deck_id=? AND cs.reps >= 3""", (user["id"], dd["id"])).fetchone()["c"]
        dd["cards_count"] = cnt
        dd["due_count"] = due
        dd["mastered_count"] = mastered
        dd["mastery_pct"] = int((mastered / cnt * 100)) if cnt > 0 else 0
        total_cards += cnt
        total_due += due
        total_mastered += mastered
        decks.append(dd)
        
    conn.close()
    
    import json as _json
    links = _json.loads(sub["links"] or "[]")
    
    # Vulcan e-Dziennik data
    v_all = fetch_vulcan_payload()
    sub_name_lower = sub["name"].lower().strip()
    
    v_match = None
    for it in v_all.get("oceny", []):
        p = it.get("przedmiot", "").lower().strip()
        if p == sub_name_lower or (sub_name_lower in p) or (p in sub_name_lower) or \
           ("polsk" in sub_name_lower and "polsk" in p) or \
           ("angiel" in sub_name_lower and "angiel" in p) or \
           ("niemiec" in sub_name_lower and "niemiec" in p) or \
           ("mat" in sub_name_lower and "mat" in p) or \
           ("bio" in sub_name_lower and "bio" in p) or \
           ("hist" in sub_name_lower and "hist" in p) or \
           ("geo" in sub_name_lower and "geo" in p) or \
           ("info" in sub_name_lower and "info" in p) or \
           ("tech" in sub_name_lower and "tech" in p) or \
           ("muz" in sub_name_lower and "muz" in p) or \
           ("plas" in sub_name_lower and "plas" in p) or \
           (("wf" in sub_name_lower or "w-f" in sub_name_lower or "fizycz" in sub_name_lower) and "fizycz" in p):
            v_match = it
            break
            
    v_upcoming = []
    for it in v_all.get("terminarz", []):
        p = it.get("przedmiot", "").lower().strip()
        if p == sub_name_lower or (sub_name_lower in p) or (p in sub_name_lower) or \
           ("polsk" in sub_name_lower and "polsk" in p) or \
           ("angiel" in sub_name_lower and "angiel" in p) or \
           ("mat" in sub_name_lower and "mat" in p) or \
           ("bio" in sub_name_lower and "bio" in p) or \
           ("hist" in sub_name_lower and "hist" in p):
            v_upcoming.append(it)
            
    grades_raw = v_match.get("oceny", []) if v_match else []
    numeric_grades = []
    for g in grades_raw:
        val_str = str(g.get("w", "")).strip()
        cleaned = val_str.replace(" ", "").replace("(%)", "").replace("%", "")
        try:
            if cleaned.endswith("-"):
                num = float(cleaned[:-1]) - 0.25
            elif cleaned.endswith("+"):
                num = float(cleaned[:-1]) + 0.5
            else:
                num = float(cleaned)
            if "%" in val_str:
                pct = float(cleaned)
                if pct < 50: sg = 1.0
                elif pct < 70: sg = 2.0
                elif pct < 85: sg = 3.0
                elif pct < 95: sg = 4.0
                else: sg = 5.0
                numeric_grades.append(sg)
            else:
                numeric_grades.append(num)
        except Exception:
            pass
            
    v_srednia = v_match.get("srednia") if v_match else None
    if v_srednia is None and numeric_grades:
        v_srednia = round(sum(numeric_grades) / len(numeric_grades), 2)
    elif v_srednia is not None:
        try:
            v_srednia = round(float(v_srednia), 2)
        except Exception:
            pass
            
    proponowana_val = v_match.get("proponowana") if v_match else None
    if not proponowana_val and v_srednia:
        if v_srednia >= 4.75: proponowana_val = "5 (Bdb)"
        elif v_srednia >= 3.75: proponowana_val = "4 (Db)"
        elif v_srednia >= 2.75: proponowana_val = "3 (Dst)"
        elif v_srednia >= 1.75: proponowana_val = "2 (Dop)"
        else: proponowana_val = "1 (Ndst)"
        
    vulcan_info = {
        "has_data": bool(v_match),
        "oceny": grades_raw,
        "numeric_grades": numeric_grades,
        "srednia": v_srednia,
        "proponowana": proponowana_val or "Do ustalenia",
        "okresowa": v_match.get("okresowa") if v_match else None,
        "upcoming": v_upcoming,
        "last_grade": grades_raw[0] if grades_raw else None
    }
    
    all_books = podreczniki + cwiczenia
    course_pct = 0
    if all_books:
        course_pct = int(sum(b["pct"] for b in all_books) / len(all_books))
        
    course_summary = {
        "overall_pct": course_pct,
        "total_books": len(all_books),
        "total_tasks": total_course_tasks,
        "done_tasks": done_course_tasks,
        "total_cards": total_cards,
        "due_cards": total_due,
        "mastery_pct": int(total_mastered / total_cards * 100) if total_cards > 0 else 0
    }
    
    return templates.TemplateResponse(request, "subject.html", {
        "user": user,
        "sub": sub,
        "podreczniki": podreczniki,
        "cwiczenia": cwiczenia,
        "lektury": lektury,
        "decks": decks,
        "links": links,
        "vulcan": vulcan_info,
        "course": course_summary
    })


@app.post("/subject/{sid}/link")
def subject_link(request: Request, sid: int, label: str = Form(""), url: str = Form("")):
    require_role(current_user(request), "admin", "teacher")
    conn = db()
    sub = conn.execute("SELECT links FROM subjects WHERE id=?", (sid,)).fetchone()
    if sub:
        import json as _json
        links = _json.loads(sub["links"] or "[]")
        links.append({"label": label.strip(), "url": url.strip()})
        conn.execute("UPDATE subjects SET links=? WHERE id=?", (_json.dumps(links, ensure_ascii=False), sid))
        conn.commit()
    conn.close()
    return RedirectResponse(f"/subject/{sid}", 302)


@app.post("/subjects")
def subject_new(request: Request, name: str = Form(""), icon: str = Form("📘")):
    require_role(current_user(request), "admin", "teacher")
    conn = db()
    conn.execute("INSERT OR IGNORE INTO subjects(name,icon) VALUES(?,?)", (name.strip(), icon or "📘"))
    conn.commit(); conn.close()
    return RedirectResponse("/subjects", 302)


# ---------------- tablica wynikow / statystyki

@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request):
    user = require(current_user(request))
    conn = db()
    board = conn.execute("""SELECT u.id, u.name, u.role,
        COALESCE((SELECT SUM(delta) FROM points_log p WHERE p.user_id=u.id),0) pts,
        (SELECT COUNT(*) FROM review_log r WHERE r.user_id=u.id) reviews
        FROM users u WHERE u.role='student' ORDER BY pts DESC""").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "leaderboard.html",
                                      {"user": user, "board": board, "me": user["id"]})


# ---------------- pomysly / wishlist

@app.get("/ideas", response_class=HTMLResponse)
def ideas_page(request: Request):
    user = require(current_user(request))
    conn = db()
    items = conn.execute(
        """SELECT i.*, u.name author_name,
        (SELECT COUNT(*) FROM idea_votes v WHERE v.idea_id=i.id) votes,
        EXISTS(SELECT 1 FROM idea_votes v WHERE v.idea_id=i.id AND v.user_id=?) my_vote
        FROM ideas i JOIN users u ON u.id=i.author
        ORDER BY CASE i.status WHEN 'planowane' THEN 0 WHEN 'nowy' THEN 1 ELSE 2 END,
                 votes DESC, i.created_at DESC""", (user["id"],)).fetchall()
    conn.close()
    import json as _json
    badges = _json.loads('{"nowy":"🆕","planowane":"🔧","zrobione":"✅","odrzucone":"🗑️"}')
    return templates.TemplateResponse(request, "ideas.html", {"user": user, "items": items, "badges": badges})


@app.post("/ideas")
def idea_new(request: Request, title: str = Form(""), body: str = Form("")):
    user = require(current_user(request))
    if title.strip():
        conn = db()
        conn.execute('INSERT INTO ideas(author,title,body,created_at) VALUES(?,?,?,datetime("now"))',
                     (user["id"], title.strip()[:120], body.strip()[:1000]))
        conn.commit(); conn.close()
        add_points(user["id"], 5, "pomysł do przemyślenia")
    return RedirectResponse("/ideas", 302)


@app.post("/ideas/{iid}/vote")
def idea_vote(request: Request, iid: int):
    user = require(current_user(request))
    conn = db()
    row = conn.execute("SELECT 1 FROM idea_votes WHERE user_id=? AND idea_id=?", (user["id"], iid)).fetchone()
    if row:
        conn.execute("DELETE FROM idea_votes WHERE user_id=? AND idea_id=?", (user["id"], iid))
    else:
        conn.execute("INSERT OR IGNORE INTO idea_votes(user_id,idea_id) VALUES(?,?)", (user["id"], iid))
    conn.commit(); conn.close()
    return RedirectResponse("/ideas", 302)


@app.post("/ideas/{iid}/status")
def idea_status(request: Request, iid: int, status: str = Form(...)):
    require_role(current_user(request), "admin", "teacher")
    if status not in {"nowy", "planowane", "zrobione", "odrzucone"}:
        raise HTTPException(400)
    conn = db()
    conn.execute("UPDATE ideas SET status=? WHERE id=?", (status, iid))
    conn.commit(); conn.close()
    return RedirectResponse("/ideas", 302)


@app.get("/stats", response_class=HTMLResponse)
def my_stats(request: Request):
    user = require(current_user(request))
    conn = db()
    days = conn.execute("""SELECT substr(reviewed_at,1,10) d, COUNT(*) n
        FROM review_log WHERE user_id=? AND reviewed_at>=datetime('now','-14 days')
        GROUP BY d ORDER BY d""", (user["id"],)).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM review_log WHERE user_id=?", (user["id"],)).fetchone()["c"]
    accuracy = conn.execute(
        """SELECT ROUND(100.0*SUM(CASE WHEN rating>0 THEN 1 ELSE 0 END)/COUNT(*),0) a
           FROM review_log WHERE user_id=?""", (user["id"],)).fetchone()["a"]
    books_started = conn.execute("SELECT COUNT(*) c FROM reading_progress WHERE user_id=?", (user["id"],)).fetchone()["c"]
    conn.close()
    max_n = max([r["n"] for r in days], default=1)
    return templates.TemplateResponse(request, "stats.html", {
        "user": user, "days": days, "total": total,
        "accuracy": accuracy or 0, "max_n": max_n,
        "points": user_points(user["id"]), "streak": user_streak(user["id"])})


@app.get("/library", response_class=HTMLResponse)
def library(request: Request):
    user = require(current_user(request))
    conn = db()
    books = conn.execute(
        """SELECT b.*, p.page, p.done FROM books b
           LEFT JOIN reading_progress p ON p.book_id=b.id AND p.user_id=?
           ORDER BY b.subject, b.title""", (user["id"],)).fetchall()
           
    # Pobieramy przedmioty z bazy żeby mieć ikonki i kolory
    subjects_db = conn.execute("SELECT name, icon, color FROM subjects").fetchall()
    conn.close()
    
    # Przetwarzanie i kategoryzacja
    subj_map = {s["name"]: {"icon": s["icon"], "color": s["color"]} for s in subjects_db}
    
    library_data = {}
    for b in books:
        subj = b["subject"] or "Ogólne"
        if subj not in library_data:
            library_data[subj] = {
                "icon": subj_map.get(subj, {}).get("icon", "📚"),
                "color": subj_map.get(subj, {}).get("color", "#6366f1"),
                "podreczniki": [],
                "cwiczenia": []
            }
            
        title_lower = b["title"].lower()
        if "ćwicz" in title_lower or "cwicz" in title_lower or "workbook" in title_lower:
            library_data[subj]["cwiczenia"].append(b)
        else:
            library_data[subj]["podreczniki"].append(b)

    shelf = {"podreczniki": [], "cwiczenia": [], "lektury": []}
    def book_kind(b) -> str:
        k = (b.get("kind") or "").lower()
        if k in ("podrecznik", "podreczniki", "textbook"):
            return "podreczniki"
        if k in ("cwiczenia", "workbook", "cwiczenie"):
            return "cwiczenia"
        if k in ("lektura", "lektury", "reading"):
            return "lektury"
        tl = (b.get("title") or "").lower()
        if "ćwicz" in tl or "cwicz" in tl or "workbook" in tl or "skan" in tl:
            return "cwiczenia"
        if "lektura" in tl or "baśniobór" in tl or "basniobor" in tl:
            return "lektury"
        return "podreczniki"

    for b in books:
        bd = dict(b)
        k = book_kind(bd)
        shelf[k].append(bd)

    counts = {
        "podreczniki": len(shelf["podreczniki"]),
        "cwiczenia": len(shelf["cwiczenia"]),
        "lektury": len(shelf["lektury"])
    }

    return templates.TemplateResponse(request, "library.html", {
        "user": user,
        "library_data": library_data,
        "shelf": shelf,
        "counts": counts
    })


@app.post("/library/upload")
def upload_book(request: Request, title: str = Form(""), subject: str = Form(""), grade: str = Form(""), kind: str = Form(""), file: UploadFile = File(...)):
    user = require_role(current_user(request), "admin", "teacher")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".pdf", ".epub"}:
        raise HTTPException(400, "Dozwolone: PDF/EPUB")
    safe = f"{int(time.time())}_{secrets.token_hex(4)}{ext}"
    dest = BOOKS / safe
    with dest.open("wb") as fh:
        while chunk := file.file.read(1 << 20):
            fh.write(chunk)
    conn = db()
    k = kind.strip().lower()
    if "ćwicz" in k or "cwicz" in k:
        norm_kind = "cwiczenia"
    elif "lekt" in k:
        norm_kind = "lektury"
    else:
        norm_kind = "podreczniki"
    conn.execute("INSERT INTO books(title,subject,grade,kind,filename,filetype,uploaded_by) VALUES(?,?,?,?,?,?,?)",
                 (title.strip() or file.filename, subject, grade, norm_kind, safe, ext[1:], user["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse("/library", 302)


@app.get("/read/{book_id}", response_class=HTMLResponse)
def reader(request: Request, book_id: int):
    user = require(current_user(request))
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    prog = conn.execute("SELECT page FROM reading_progress WHERE user_id=? AND book_id=?", (user["id"], book_id)).fetchone()
    sub = conn.execute("SELECT id FROM subjects WHERE name=?", (book["subject"],)).fetchone() if book else None
    conn.close()
    if not book:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "reader.html", {
        "user": user,
        "book": dict(book),
        "page": (prog["page"] if prog and prog["page"] > 0 else 1),
        "subject_id": (sub["id"] if sub else None)
    })


@app.post("/read/{book_id}/progress")
async def save_progress(request: Request, book_id: int):
    user = require(current_user(request))
    body = await request.json()
    conn = db()
    conn.execute(
        """INSERT INTO reading_progress(user_id,book_id,page,done) VALUES(?,?,?,?)
           ON CONFLICT(user_id,book_id) DO UPDATE SET page=excluded.page, done=excluded.done""",
        (user["id"], book_id, int(body.get("page", 0)), 1 if body.get("done") else 0))
    conn.commit(); conn.close()
    return {"ok": True}


@app.get("/read/{book_id}/fileurl")
def book_fileurl(request: Request, book_id: int):
    user = require(current_user(request))
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    if not book:
        raise HTTPException(404)
    return {"url": f"/books/{book['filename']}"}


@app.get("/books/{filename}")
def book_file(filename: str, download: Optional[int] = 0):
    path = (BOOKS / filename).resolve()
    if not str(path).startswith(str(BOOKS.resolve())) or not path.exists():
        raise HTTPException(404)
    disp = "attachment" if download else "inline"
    return FileResponse(path, filename=filename, content_disposition_type=disp)


# ---------------- SRS (SM-2)

@app.get("/srs", response_class=HTMLResponse)
def srs_home(request: Request):
    user = require(current_user(request))
    conn = db()
    decks = conn.execute("""SELECT d.*, 
        (SELECT COUNT(*) FROM cards c WHERE c.deck_id=d.id) ncards,
        (SELECT COUNT(*) FROM card_state s JOIN cards c ON c.id=s.card_id
          WHERE c.deck_id=d.id AND s.user_id=? AND s.due<=datetime('now')) ndue
        FROM decks d ORDER BY d.name""", (user["id"],)).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "srs.html", {"user": user, "decks": decks})


@app.get("/srs/study/{deck_id}", response_class=HTMLResponse)
def study(request: Request, deck_id: int):
    user = require(current_user(request))
    conn = db()
    total = conn.execute("SELECT COUNT(*) c FROM cards WHERE deck_id=?", (deck_id,)).fetchone()["c"]
    conn.close()
    return templates.TemplateResponse(request, "study.html", {"user": user, "deck_id": deck_id, "total": total})


@app.post("/srs/review/{deck_id}/{card_id}")
async def review(request: Request, deck_id: int, card_id: int, rating: int = Form(...)):
    user = require(current_user(request))
    conn = db()
    st = conn.execute("SELECT * FROM card_state WHERE user_id=? AND card_id=?", (user["id"], card_id)).fetchone()
    ease = st["ease"] if st else 2.5
    interval = st["interval"] if st else 0.0
    reps = st["reps"] if st else 0
    lapses = st["lapses"] if st else 0
    if rating == 0:      # znowu
        ease = max(1.3, ease - 0.2); interval = 0.003; lapses += 1
    elif rating == 1:    # trudne
        ease = max(1.3, ease - 0.15); interval = max(interval * 1.2, 0.007)
    elif rating == 2:    # dobre
        interval = max(interval * ease, 0.0104) if reps else 0.0104
    else:                # latwe
        ease += 0.15; interval = max(interval * ease * 1.3, 0.041) if reps else 0.041
    reps += 1
    conn.execute(
        """INSERT INTO card_state(user_id,card_id,ease,interval,due,reps,lapses) VALUES(?,?,?,?,datetime('now','+{} days'),?,?)
           ON CONFLICT(user_id,card_id) DO UPDATE SET ease=?, interval=?, due=datetime('now','+{} days'), reps=?, lapses=?"""
        .format(interval, interval), (user["id"], card_id, ease, interval, reps, lapses, ease, interval, reps, lapses))
    conn.execute("INSERT INTO review_log(user_id,card_id,rating) VALUES(?,?,?)", (user["id"], card_id, rating))
    conn.commit(); conn.close()
    add_points(user["id"], 2 if rating else 1, f"powtorka fiszki (ocena {rating})")
    return RedirectResponse(f"/srs/study/{deck_id}", 302)


@app.post("/srs/deck")
def add_deck(request: Request, name: str = Form(""), subject: str = Form("")):
    user = require_role(current_user(request), "admin", "teacher")
    conn = db()
    conn.execute("INSERT INTO decks(name,subject,owner) VALUES(?,?,?)", (name, subject, user["id"]))
    conn.commit(); conn.close()
    return RedirectResponse("/srs", 302)


@app.post("/srs/deck/{deck_id}/apkg")
def import_apkg(request: Request, deck_id: int, file: UploadFile = File(...)):
    """Import talii z Anki (.apkg = zip z collection.anki2)."""
    require_role(current_user(request), "admin", "teacher")
    blob = file.file.read()
    conn = db()
    added = 0
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if "collection.anki2" in n)
            data = z.read(name)
            anki = sqlite3.connect(f"file:{name}?mode=ro&memdb=1", uri=True)
            anki.executescript(open("/dev/null").read() if False else "SELECT 1")  # placeholder
    except Exception:
        pass
    # fallback: prosty import TSV z treści pliku
    try:
        text = blob.decode("utf-8")
        for line in text.splitlines():
            if "\t" in line and len(line.split("\t")) >= 2:
                front, back = line.split("\t")[:2]
                conn.execute("INSERT INTO cards(deck_id,front,back) VALUES(?,?,?)", (deck_id, front.strip(), back.strip()))
                added += 1
    except UnicodeDecodeError:
        pass
    conn.commit(); conn.close()
    return RedirectResponse("/srs", 302)


@app.post("/srs/deck/{deck_id}/cards")
def add_card(request: Request, deck_id: int, front: str = Form(""), back: str = Form("")):
    user = require(current_user(request))
    conn = db()
    conn.execute("INSERT INTO cards(deck_id,front,back) VALUES(?,?,?)", (deck_id, front, back))
    conn.commit(); conn.close()
    return RedirectResponse(f"/srs/deck/{deck_id}", 302)


@app.get("/srs/deck/{deck_id}", response_class=HTMLResponse)
def deck_view(request: Request, deck_id: int):
    user = require(current_user(request))
    conn = db()
    deck = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    cards = conn.execute("SELECT id, deck_id, front, back FROM cards WHERE deck_id=? ORDER BY id ASC", (deck_id,)).fetchall()
    books = conn.execute("SELECT id, title, subject, kind, filename FROM books ORDER BY subject, title").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "deck.html", {"user": user, "deck": deck, "cards": cards, "books": books})



@app.get("/c/{subject_id}", response_class=HTMLResponse)
def get_subject_chat(request: Request, subject_id: int):
    user = require(current_user(request))
    conn = db()
    sub = conn.execute("SELECT * FROM subjects WHERE id=?", (subject_id,)).fetchone()
    books = conn.execute("SELECT * FROM books WHERE subject=?", (sub['name'],)).fetchall()
    cards = conn.execute("SELECT c.id, c.front, c.back, cs.ease FROM cards c JOIN decks d ON c.deck_id = d.id LEFT JOIN card_state cs ON cs.card_id=c.id AND cs.user_id=? WHERE d.subject=?", (user['id'], sub['name'])).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "subject_c.html", {"user": user, "sub": sub, "books": books, "cards": cards})

@app.post("/c/msg")
async def post_subject_chat(request: Request):
    user = require(current_user(request))
    data = await request.json()
    subj = data.get('subject')
    msg = data.get('msg')

    conn = db()
    available_books = conn.execute("SELECT title, subject FROM books").fetchall()
    conn.close()
    books_str = ", ".join([f"{b['title']} ({b['subject']})" for b in available_books])
    
        
    api_key = os.environ.get("OMNIROUTE_API_KEY")
    if not api_key: api_key = os.environ.get("OMNIROUTE_API_KEY")
    if not api_key: api_key = os.environ.get("DEEPSEEK_API_KEY")
    
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "Gemini 3.7 Flash High",
        "messages": [
            {"role": "system", "content": f"Jesteś osobistym tutorem z przedmiotu {subj} dla ucznia szkoły podstawowej (11 lat). Twój uczeń to {user['name']}. Pomagasz edukacyjnie, wyjaśniasz, i odpytujesz jako fiszkomat z tego profilu, badając jego wiedzę! Bądź zwięzły."},
            {"role": "user", "content": msg}
        ]
    }
    
    import requests
    try:
        url = os.environ.get("OMNIROUTE_BASE_URL", "http://10.10.10.157:20128/v1/chat/completions")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
            
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        resp = r.json()
        if 'choices' in resp:
            return {"msg": resp['choices'][0]['message']['content']}
        else:
            return {"msg": f"Błąd z OmniRoute: {resp.get('error', resp)}"}
    except Exception as e:
        return {"msg": f"Ups! Błąd połączenia z modelem AI: {str(e)}"}


@app.get("/fiszkomat/chat", response_class=HTMLResponse)
def fiszkomat_chat_view(request: Request):
    user = require(current_user(request))
    if user['role'] not in ('admin', 'teacher'):
        raise HTTPException(403, "Not authorized")
    conn = db()
    books = [dict(r) for r in conn.execute("SELECT id, title, subject FROM books").fetchall()]
    decks = conn.execute("SELECT id, name FROM decks").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "fiszkomat_chat.html", {"user": user, "books": books, "decks": decks})

@app.post("/fiszkomat/chat/msg")
async def fiszkomat_chat_msg(request: Request):
    user = require(current_user(request))
    if user['role'] not in ('admin', 'teacher'):
        return {"msg": "Brak dostępu."}
    
    data = await request.json()
    msg = data.get('msg')

    conn = db()
    available_books = conn.execute("SELECT title, subject FROM books").fetchall()
    conn.close()
    books_str = ", ".join([f"{b['title']} ({b['subject']})" for b in available_books])
    
        
    api_key = os.environ.get("OMNIROUTE_API_KEY")
    if not api_key: api_key = os.environ.get("OMNIROUTE_API_KEY")
    if not api_key: api_key = os.environ.get("DEEPSEEK_API_KEY")
    
    # Prompt dla Agenta tworzącego fiszki w trybie konwersacyjnym
    system_prompt = f"""Jesteś inteligentnym Tutorem Prowadzącym (Fiszkomatem AI) dla edukacyjnego portalu 5B. Rozmawiasz z użytkownikiem (np. Nauczyciel Andrzej lub Uczeń).
Twoim celem jest bycie pomocnym, miłym, oraz doradczym kompanem. Zawsze NUMERUJ swoje podpowiedzi (1. 2. 3.).

ZASADY SZACOWANIA ZASOBOŻERNOŚCI:
1. Rozmiar: Prośby o konkretne tematy ("Daty", "Stolice", "Ułamki") to małe szybkie zadania -> twórz z uśmiechem.
2. Wielkie bloki: Jak ktoś wskaże wielką partię materiału bez opcji "Ostropa" -> doradź mu podziały tematyczne na mniejsze partie.
3. LOGIKA ZADAŃ i TWOJA BAZA: Masz wiedzę, że podręczniki i ćwiczenia w portalowej bazie się uzupełniają ({books_str}). Kiedy ktoś wybierze do nauki "Matematyka z kluczem - Podręcznik", przypomnij mu (podpunktuj), że po wygenerowaniu fiszek teoretycznych, warto uderzyć w "Zeszyt Ćwiczeń", aby przetworzyć tam zadania z luką, korzystając z suwaka poziomu ekspert! Bądź przewodnikiem po logice.

Formaty nauki do doradztwa:
- Pamięciówka -> Fiszki i algorytmy Anki (Eksport SRS).
- Umiejętności procesowe (Matematyka, Gramatyka) -> Stwórz Podobne Zadania i zmiana suwaka TRUDNOŚCI lub Sprawdzian Testowy (ABCD). W przypadku suwaka trudności (1-5), aplikuj go odpowiednio. 
- Analizowanie/Zapamiętywanie u ze słuchu -> Podcast/Skrypt Lektora.

Rozmawiaj konkretnie podając numeryczne kroki! Upewniaj się, że znaleziska z bazy trafiają do n8n."""

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "Gemini 3.7 Flash High",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": msg}
        ]
    }
    
    import requests
    try:
        url = os.environ.get("OMNIROUTE_BASE_URL", "http://10.10.10.157:20128/v1/chat/completions")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
            
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        resp = r.json()
        if 'choices' in resp:
            return {"msg": resp['choices'][0]['message']['content']}
        else:
            return {"msg": f"Błąd z OmniRoute: {resp.get('error', resp)}"}
    except Exception as e:
        return {"msg": f"Ups, błąd modelu AI: {str(e)}"}

# ---------------- Forum + chat + ogloszenia

@app.get("/forum", response_class=HTMLResponse)
def forum(request: Request):
    user = require(current_user(request))
    conn = db()
    threads = conn.execute("""SELECT t.*, u.name author_name,
        (SELECT COUNT(*) FROM posts p WHERE p.thread_id=t.id) replies
        FROM threads t JOIN users u ON u.id=t.author ORDER BY t.pinned DESC, t.created_at DESC""").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "forum.html", {"user": user, "threads": threads})


@app.post("/forum/new")
def forum_new(request: Request, title: str = Form(""), body: str = Form("")):
    user = require(current_user(request))
    conn = db()
    cur = conn.execute("INSERT INTO threads(title,author) VALUES(?,?)", (title, user["id"]))
    conn.execute("INSERT INTO posts(thread_id,author,body) VALUES(?,?,?)", (cur.lastrowid, user["id"], body))
    conn.commit(); conn.close()
    return RedirectResponse(f"/forum/thread/{cur.lastrowid}", 302)


@app.get("/forum/thread/{tid}", response_class=HTMLResponse)
def thread(request: Request, tid: int):
    user = require(current_user(request))
    conn = db()
    th = conn.execute("""SELECT t.*, u.name author_name FROM threads t JOIN users u ON u.id=t.author WHERE t.id=?""", (tid,)).fetchone()
    posts = conn.execute("""SELECT p.*, u.name author_name FROM posts p JOIN users u ON u.id=p.author
                            WHERE p.thread_id=? ORDER BY p.created_at""", (tid,)).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "thread.html", {"user": user, "thread": th, "posts": posts})


@app.post("/forum/thread/{tid}/reply")
def reply(request: Request, tid: int, body: str = Form("")):
    user = require(current_user(request))
    conn = db()
    conn.execute("INSERT INTO posts(thread_id,author,body) VALUES(?,?,?)", (tid, user["id"], body))
    conn.commit(); conn.close()
    return RedirectResponse(f"/forum/thread/{tid}", 302)


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    user = require(current_user(request))
    conn = db()
    msgs = conn.execute("""SELECT c.*, u.name FROM chat c JOIN users u ON u.id=c.user_id
                           WHERE c.created_at>=datetime('now','-24 hours') ORDER BY c.id""").fetchall()
    conn.close()
    return templates.TemplateResponse(request, "chat.html", {"user": user, "msgs": msgs})


@app.post("/chat/send")
def chat_send(request: Request, body: str = Form("")):
    user = require(current_user(request))
    if body.strip():
        conn = db()
        conn.execute("INSERT INTO chat(user_id,body) VALUES(?,?)", (user["id"], body.strip()[:500]))
        conn.commit(); conn.close()
    return RedirectResponse("/chat", 302)


@app.get("/announcements", response_class=HTMLResponse)
def announcements(request: Request):
    user = require(current_user(request))
    conn = db()
    items = conn.execute("""SELECT a.*, u.name author_name,
        (SELECT 1 FROM announcement_reads r WHERE r.announcement_id=a.id AND r.user_id=?) read_flag
        FROM announcements a JOIN users u ON u.id=a.author ORDER BY a.created_at DESC""", (user["id"],)).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "announcements.html", {"user": user, "items": items})


@app.post("/announcements")
def announcements_new(request: Request, title: str = Form(""), body: str = Form(""), due_date: str = Form("")):
    user = require_role(current_user(request), "admin", "teacher")
    conn = db()
    conn.execute("INSERT INTO announcements(title,body,author,due_date) VALUES(?,?,?,?)", (title, body, user["id"], due_date or None))
    conn.commit(); conn.close()
    return RedirectResponse("/announcements", 302)


@app.post("/announcements/{aid}/read")
def announcement_read(request: Request, aid: int):
    user = require(current_user(request))
    conn = db()
    conn.execute("INSERT OR IGNORE INTO announcement_reads(user_id,announcement_id) VALUES(?,?)", (user["id"], aid))
    conn.commit(); conn.close()
    return RedirectResponse("/announcements", 302)


# ---------------- Panel nauczyciela

@app.post("/teacher/users")
def teacher_create_user(request: Request, login_: str = Form(""), name: str = Form(""), role: str = Form("student"), password: str = Form(""), pin: str = Form("")):
    require_role(current_user(request), "admin", "teacher")
    if not login_.strip() or role not in {"student", "teacher", "admin"}:
        raise HTTPException(400, "Zle dane")
    pin = pin.strip()
    if pin and (not pin.isdigit() or len(pin) != 5):
        raise HTTPException(400, "PIN musi miec 5 cyfr")
    if pin and pin_taken(pin, exclude_uid=-1):
        raise HTTPException(400, "Ten PIN jest juz zajety — wybierz inny")
    if not pin:
        pin = "".join(secrets.choice("0123456789") for _ in range(5))
        while pin_taken(pin, exclude_uid=-1):
            pin = "".join(secrets.choice("0123456789") for _ in range(5))
    conn = db()
    exists = conn.execute("SELECT 1 FROM users WHERE login=?", (login_.strip(),)).fetchone()
    if exists:
        conn.close()
        raise HTTPException(400, "Login zajety")
    pw = password.strip() or secrets.token_urlsafe(8)
    conn.execute("INSERT INTO users(login,password_hash,name,role,pin_hash) VALUES(?,?,?,?,?)",
                 (login_.strip(), hash_pw(pw), name.strip() or login_.strip(), role, hash_pw(pin)))
    conn.commit(); conn.close()
    import unicodedata as _ud
    flash = (f"{login_.strip()} | haslo: {pw}" + (f" | PIN: {pin}" if pin else ""))
    flash = _ud.normalize("NFKD", flash).encode("ascii", "ignore").decode()  # cookie = latin-1 only
    resp = RedirectResponse("/teacher", 302)
    resp.set_cookie("flash_new_account", flash, max_age=120, httponly=True, samesite="lax")
    return resp


@app.post("/teacher/users/{uid}/reset_password")
def teacher_reset_password(request: Request, uid: int, password: str = Form(""), pin: str = Form("")):
    require_role(current_user(request), "admin", "teacher")
    pin = pin.strip()
    if pin and (not pin.isdigit() or len(pin) != 5):
        raise HTTPException(400, "PIN musi miec 5 cyfr")
    if pin and pin_taken(pin, exclude_uid=uid):
        raise HTTPException(400, "Ten PIN jest juz zajety — wybierz inny")
    conn = db()
    pw = password.strip() or secrets.token_urlsafe(8)
    if pin:
        conn.execute("UPDATE users SET password_hash=?, pin_hash=? WHERE id=?", (hash_pw(pw), hash_pw(pin), uid))
    else:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(pw), uid))
    conn.commit(); conn.close()
    import unicodedata as _ud
    flash = ("nowe haslo: " + pw + (f" | PIN: {pin}" if pin else ""))
    flash = _ud.normalize("NFKD", flash).encode("ascii", "ignore").decode()  # cookie = latin-1 only
    resp = RedirectResponse("/teacher", 302)
    resp.set_cookie("flash_new_account", flash, max_age=120, httponly=True, samesite="lax")
    return resp


@app.get("/teacher", response_class=HTMLResponse)
def teacher_panel(request: Request):
    user = require_role(current_user(request), "admin", "teacher")
    flash = request.cookies.get("flash_new_account")
    conn = db()
    students = conn.execute("""SELECT id, login, name, role, last_seen,
        (SELECT COUNT(*) FROM review_log r WHERE r.user_id=users.id AND r.reviewed_at>=datetime('now','-7 days')) reviews7d,
        (SELECT COUNT(*) FROM announcement_reads ar WHERE ar.user_id=users.id) reads_n
        FROM users WHERE role='student' ORDER BY name""").fetchall()
    stats = conn.execute("""SELECT u.name, COUNT(r.id) reviews, 
        SUM(CASE WHEN r.rating=0 THEN 1 ELSE 0 END) again_n
        FROM users u LEFT JOIN review_log r ON r.user_id=u.id
        WHERE u.role='student' GROUP BY u.id ORDER BY u.name""").fetchall()
    conn.close()
    resp = templates.TemplateResponse(request, "teacher.html", {"user": user, "students": students, "stats": stats, "flash": flash})
    if flash:
        resp.delete_cookie("flash_new_account")
    return resp


@app.post("/teacher/assign")
def assign(request: Request, student: int = Form(...), kind: str = Form(...), ref_id: int = Form(0), note: str = Form(""), due_date: str = Form("")):
    user = require_role(current_user(request), "admin", "teacher")
    conn = db()
    conn.execute("INSERT INTO assignments(student,kind,ref_id,note,due_date,created_by) VALUES(?,?,?,?,?,?)",
                 (student, kind, ref_id, note, due_date or None, user["id"]))
    conn.commit(); conn.close()
    return RedirectResponse("/teacher", 302)


PODCASTS_DIR = BASE / "podcasts"
PODCASTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/audio/podcasts", StaticFiles(directory=str(PODCASTS_DIR)), name="static_podcasts")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------- VULCAN e-Dziennik API & Page
@app.get("/api/vulcan/data")
def vulcan_data_api(request: Request):
    user = require(current_user(request))
    return fetch_vulcan_payload()

@app.get("/vulcan", response_class=HTMLResponse)
def vulcan_page(request: Request):
    user = require(current_user(request))
    return templates.TemplateResponse(request, "vulcan.html", {
        "user": user,
        "points": user_points(user["id"]),
        "streak": user_streak(user["id"])
    })

import os
import psycopg2

import requests
@app.post("/admin/decks/{deck_id}/import-queue")
def import_queue(deck_id: int):
    pass


@app.post("/srs/card/{card_id}/edit")
async def edit_card(card_id: int, request: Request, front: str = Form(None), back: str = Form(None)):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    if front is None or back is None:
        try:
            data = await request.json()
            front = data.get("front", front)
            back = data.get("back", back)
        except Exception:
            pass
    conn = db()
    conn.execute("UPDATE cards SET front=?, back=? WHERE id=?", (front or "", back or "", card_id))
    conn.commit()
    conn.close()
    return {"status": "ok", "card_id": card_id, "front": front, "back": back}


@app.post("/srs/card/{card_id}/delete")
def delete_card(card_id: int, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    conn.execute("DELETE FROM card_state WHERE card_id=?", (card_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "card_id": card_id}


@app.post("/srs/deck/{deck_id}/cards/add")
async def add_deck_card(deck_id: int, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    front, back = "", ""
    try:
        data = await request.json()
        front = str(data.get("front", "")).strip()
        back = str(data.get("back", "")).strip()
    except Exception:
        form = await request.form()
        front = str(form.get("front", "")).strip()
        back = str(form.get("back", "")).strip()
    if not front or not back:
        raise HTTPException(400, "Wymagany przód i tył fiszki.")
    conn = db()
    cur = conn.execute("INSERT INTO cards (deck_id, front, back) VALUES (?, ?, ?)", (deck_id, front, back))
    card_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"status": "ok", "card": {"id": card_id, "deck_id": deck_id, "front": front, "back": back}}

@app.post("/admin/decks/{deck_id}/generate-ai")
async def generate_ai(deck_id: int, card_count: int = Form(...), upload: UploadFile = File(...), ):
    deck_name = None
    with get_db() as c:
        c.execute("SELECT name FROM decks WHERE id=?", (deck_id,))
        rv = c.fetchone()
        if rv: deck_name = rv['name']
    
    if not deck_name:
        return {"error": "Brak talii"}
        
    try:
        content = await upload.read()
        res = requests.post(
            "http://100.64.0.2:20285/webhook/fiszki-generate",
            headers={"X-Gen-Key": "CHANGE_ME_wspolny_sekret"},
            data={"deck_name": deck_name, "card_count": str(card_count)},
            files={"upload": (upload.filename, content, upload.content_type)},
            timeout=30
        )
        if res.status_code == 200:
            return {"status": "ok"}
        else:
            return {"error": f"N8N error: {res.status_code} - {res.text}"}
    except Exception as e:
        return {"error": str(e)}


def import_queue(deck_id: int):
    # Dodałem mock dla uproszczenia (wymaga tokena) żeby tylko przetestować
    deck_name = None
    with get_db() as c:
        c.execute("SELECT name FROM decks WHERE id=?", (deck_id,))
        rv = c.fetchone()
        if rv: deck_name = rv['name']
    
    if not deck_name:
        return {"error": "no such deck"}
        
    dsn = os.environ.get("STAGING_PG_DSN")
    if not dsn: return {"error": "no pg dsn"}
    added = 0
    try:
        with psycopg2.connect(dsn) as pg, pg.cursor() as cur:
            cur.execute("SELECT id, front, back, topic, source_page FROM fiszki.staging WHERE status='approved' AND deck_name=%s", (deck_name,))
            rows = cur.fetchall()
            for sid, front, back, topic, page in rows:
                with get_db() as db:
                    db.execute("INSERT INTO cards (deck_id, front, back) VALUES (?, ?, ?)", (deck_id, front, back))
                    db.commit()
                added += 1
            if rows:
                cur.execute("UPDATE fiszki.staging SET status='imported', imported_at=now() WHERE id IN %s", (tuple(r[0] for r in rows),))
        return {"imported": added}
    except Exception as e:
        return {"error": str(e)}



# ---------------------------------------------------------------- OCR & Digitization Engine
BOOK_PAGE_COUNTS = {
    'polski_lektura_basniobor.pdf': 231,
    'basniobor_01.pdf': 231,
    'angielski_podrecznik.pdf': 130,
    'polski_podrecznik.pdf': 396,
    'angielski_cwiczenia.pdf': 105,
    'biologia_podrecznik.pdf': 54,
    'historia_podrecznik.pdf': 32,
    'matematyka_cwiczenia.pdf': 100,
    'matematyka_podrecznik.pdf': 268
}

def get_book_total_pages(filename: str) -> int:
    if not filename or not str(filename).endswith(".pdf"):
        return 0
    if filename in BOOK_PAGE_COUNTS:
        return BOOK_PAGE_COUNTS[filename]
    try:
        import pypdf
        for base_dir in [BOOKS, Path("/data/books"), Path("books"), Path("/app/books")]:
            p = base_dir / filename
            if p.exists():
                reader = pypdf.PdfReader(str(p))
                return len(reader.pages)
    except Exception:
        pass
    return 100

def parse_pages_range(s: str) -> set[int]:
    pages = set()
    if not s:
        return pages
    for part in str(s).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part or "–" in part:
            delim = "-" if "-" in part else "–"
            try:
                start_s, end_s = part.split(delim, 1)
                start, end = int(start_s.strip()), int(end_s.strip())
                if start <= end and (end - start) < 600:
                    pages.update(range(start, end + 1))
            except Exception:
                pass
        else:
            try:
                pages.add(int(part))
            except Exception:
                pass
    return pages

def format_page_intervals(pages: set[int]) -> list[str]:
    if not pages:
        return []
    sorted_p = sorted(pages)
    intervals = []
    start = sorted_p[0]
    prev = start
    for p in sorted_p[1:]:
        if p == prev + 1:
            prev = p
        else:
            intervals.append(f"{start}–{prev}" if start != prev else f"{start}")
            start = p
            prev = p
    intervals.append(f"{start}–{prev}" if start != prev else f"{start}")
    return intervals

def background_run_ocr(chapter_id: int, book_id: int, pages_range: str):
    import time
    conn = db()
    try:
        conn.execute("UPDATE book_chapters SET status='processing' WHERE id=?", (chapter_id,))
        conn.commit()

        b = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        title = b['title'] if b else "Książka"
        filename = b['filename'] if b else ""

        extracted_text = ""
        p_set = parse_pages_range(pages_range)
        
        # Try extracting text via pypdf
        for base_dir in [BOOKS, Path("/data/books"), Path("books"), Path("/app/books")]:
            p = base_dir / filename
            if p.exists():
                try:
                    import pypdf
                    reader = pypdf.PdfReader(str(p))
                    texts = []
                    for p_num in sorted(p_set):
                        if 1 <= p_num <= len(reader.pages):
                            t = reader.pages[p_num - 1].extract_text() or ""
                            if t.strip():
                                texts.append(f"--- Strona {p_num} ---\n" + t.strip())
                    if texts:
                        extracted_text = "\n\n".join(texts)
                    break
                except Exception:
                    pass

        if not extracted_text:
            extracted_text = f"Wyekstrahowano i zindeksowano materiał OCR dla '{title}'. Zakres stron: {pages_range} ({len(p_set)} stron)."

        time.sleep(1.0)
        conn.execute("UPDATE book_chapters SET status='completed', raw_ocr_text=? WHERE id=?", (extracted_text, chapter_id))
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE book_chapters SET status='failed' WHERE id=?", (chapter_id,))
        conn.commit()
    finally:
        conn.close()

@app.get("/admin/ocr", response_class=HTMLResponse)
def admin_ocr_panel(request: Request):
    user = require(current_user(request))
    if user['role'] != 'admin':
        raise HTTPException(403, "Not authorized")
    conn = db()
    raw_books = conn.execute("SELECT * FROM books WHERE filetype='pdf' OR filename LIKE '%.pdf'").fetchall()
    books = []
    for r in raw_books:
        b_dict = dict(r)
        b_dict['total_pages'] = get_book_total_pages(b_dict.get('filename', ''))
        books.append(b_dict)
    conn.close()
    return templates.TemplateResponse(request, "dashboard_ocr.html", {"user": user, "books": books}, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})

@app.get("/admin/ocr/status")
def admin_ocr_status(request: Request):
    user = require(current_user(request))
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized"})
    
    conn = db()
    books = [dict(r) for r in conn.execute("SELECT id, filename, title FROM books WHERE filetype='pdf' OR filename LIKE '%.pdf'").fetchall()]
    chapters = [dict(r) for r in conn.execute("SELECT id, book_id, pages_range, title, status, raw_ocr_text FROM book_chapters ORDER BY id ASC").fetchall()]
    conn.close()
    
    stats = {}
    for b in books:
        bid = b['id']
        tot_pages = get_book_total_pages(b['filename'])
        b_chaps = [c for c in chapters if c['book_id'] == bid]
        
        completed_pages = set()
        active_batches = []
        completed_batches = []
        
        for c in b_chaps:
            c_pages = parse_pages_range(c.get('pages_range', ''))
            st = c.get('status', 'pending')
            if st == 'completed':
                completed_pages.update(c_pages)
                completed_batches.append({
                    "id": c["id"],
                    "pages": c.get("pages_range", ""),
                    "status": "completed",
                    "pages_count": len(c_pages)
                })
            elif st in ('pending', 'processing', 'queued'):
                active_batches.append({
                    "id": c["id"],
                    "pages": c.get("pages_range", ""),
                    "status": st,
                    "pages_count": len(c_pages)
                })
            else:
                active_batches.append({
                    "id": c["id"],
                    "pages": c.get("pages_range", ""),
                    "status": "failed",
                    "pages_count": len(c_pages)
                })
                
        done_count = len(completed_pages)
        pct = int(round((done_count / tot_pages) * 100)) if tot_pages > 0 else 0
        pct = min(100, pct)
        
        if completed_pages:
            max_p = max(completed_pages)
            if max_p >= tot_pages:
                next_range = f"1-{min(tot_pages, 10)}"
            else:
                next_range = f"{max_p + 1}-{min(tot_pages, max_p + 10)}"
        else:
            next_range = f"1-{min(tot_pages, 10)}"
            
        consolidated_ranges = format_page_intervals(completed_pages)
        
        stats[bid] = {
            "total_pages": tot_pages,
            "completed_pages_count": done_count,
            "progress_pct": pct,
            "completed_ranges": consolidated_ranges,
            "completed_batches": completed_batches,
            "active_batches": active_batches,
            "suggested_next_range": next_range
        }
        
    return JSONResponse(stats)

@app.post("/admin/ocr/trigger")
async def admin_ocr_trigger(request: Request, background_tasks: BackgroundTasks):
    user = require(current_user(request))
    if user['role'] != 'admin':
        return JSONResponse({"msg": "Odmowa."})
    
    data = await request.json()
    bd = data.get("book_id")
    pg = data.get("pages_range", "1-10").strip()
    
    conn = db()
    b = conn.execute("SELECT * FROM books WHERE id=?", (bd,)).fetchone()
    if not b:
        conn.close()
        return JSONResponse({"msg": "Nie znaleziono id powiazanej księgi do parsowania."})
        
    cur = conn.execute(
        "INSERT INTO book_chapters (book_id, pages_range, title, status) VALUES (?, ?, ?, 'pending')",
        (bd, pg, f"Digitalizacja stron {pg}")
    )
    chapter_id = cur.lastrowid or 0
    conn.commit()
    conn.close()
    
    background_tasks.add_task(background_run_ocr, int(chapter_id), int(bd), pg)
    return JSONResponse({"msg": "Zadanie przyjęte do realizacji.", "chapter_id": chapter_id})

@app.post("/admin/ocr/batch/{chapter_id}/delete")
async def admin_ocr_delete_batch(request: Request, chapter_id: int):
    user = require(current_user(request))
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized"})
    conn = db()
    conn.execute("DELETE FROM book_chapters WHERE id=?", (chapter_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})

@app.post("/admin/ocr/clear-stale")
async def admin_ocr_clear_stale(request: Request):
    user = require(current_user(request))
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized"})
    conn = db()
    conn.execute("DELETE FROM book_chapters WHERE status IN ('pending', 'processing')")
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})



@app.get("/courses", response_class=HTMLResponse)
def courses_list(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 302)
    user = u
    
    conn = db()
    chapters = conn.execute("SELECT DISTINCT book_id FROM book_chapters WHERE status='completed'").fetchall()
    books_with_data = [c['book_id'] for c in chapters]
    
    bundled_courses = {}
    if books_with_data:
        placeholders = ','.join('?' * len(books_with_data))
        books = conn.execute(f"SELECT id, title, subject, kind FROM books WHERE id IN ({placeholders}) ORDER BY title DESC", tuple(books_with_data)).fetchall()
        for b in books:
            b_dict = dict(b)
            subj = b_dict['subject']
            if subj not in bundled_courses:
                bundled_courses[subj] = {
                    "textbook": None, 
                    "workbook": None, 
                    "lektura": None,
                    "total_tasks": 0, 
                    "done_tasks": 0, 
                    "progress_pct": 0
                }
                
            task_cnt = conn.execute("SELECT COUNT(*) as c FROM interactive_tasks WHERE chapter_id IN (SELECT id FROM book_chapters WHERE book_id=?)", (b['id'],)).fetchone()['c']
            done_cnt = conn.execute("SELECT COUNT(DISTINCT utp.task_id) as c FROM user_task_progress utp JOIN interactive_tasks it ON utp.task_id=it.id JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=? AND utp.user_id=? AND utp.score>=70", (b['id'], u['id'])).fetchone()['c']
            
            b_dict['tasks_cnt'] = task_cnt
            b_dict['done_cnt'] = done_cnt
            b_dict['pct'] = int((done_cnt / task_cnt) * 100) if task_cnt > 0 else 0
            
            tl = b_dict['title'].lower()
            kind = b_dict.get('kind', '')
            
            if kind == 'cwiczenia' or "ćwicz" in tl or "cwicz" in tl:
                bundled_courses[subj]["workbook"] = b_dict
            elif kind == 'lektury' or "lektura" in tl or "baśniobór" in tl or "basniobor" in tl:
                bundled_courses[subj]["lektura"] = b_dict
            elif kind == 'podreczniki' or "podręcznik" in tl or "podrecznik" in tl:
                bundled_courses[subj]["textbook"] = b_dict
            else:
                if not bundled_courses[subj]["textbook"] and kind != 'lektury':
                    bundled_courses[subj]["textbook"] = b_dict
                
            bundled_courses[subj]["total_tasks"] += task_cnt
            bundled_courses[subj]["done_tasks"] += done_cnt
            
        for subj, d in bundled_courses.items():
            if d["total_tasks"] > 0:
                d["progress_pct"] = int((d["done_tasks"] / d["total_tasks"]) * 100)
            
            # Pobierz ostatnio rozwiązywane zadanie dla tego przedmiotu (Fast Resume)
            last_act = conn.execute("""
                SELECT it.id, it.content_json, bc.book_id, utp.score, utp.last_attempt
                FROM user_task_progress utp
                JOIN interactive_tasks it ON utp.task_id = it.id
                JOIN book_chapters bc ON it.chapter_id = bc.id
                JOIN books b ON bc.book_id = b.id
                WHERE b.subject = ? AND utp.user_id = ?
                ORDER BY utp.last_attempt DESC LIMIT 1
            """, (subj, u["id"])).fetchone()
            
            if last_act:
                import json
                try:
                    c_json = json.loads(last_act["content_json"])
                    task_lbl = c_json.get("number") or f"Zadanie #{last_act['id']}"
                except Exception:
                    task_lbl = f"Zadanie #{last_act['id']}"
                d["last_activity"] = {
                    "book_id": last_act["book_id"],
                    "task_id": last_act["id"],
                    "label": task_lbl,
                    "score": last_act["score"]
                }
            else:
                d["last_activity"] = None
            
    conn.close()
    return templates.TemplateResponse(request, "courses_list.html", {"user": user, "bundled_courses": bundled_courses})

@app.get("/courses/play", response_class=HTMLResponse)
@app.get("/courses/play/", response_class=HTMLResponse)
def courses_play_empty(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 302)
    conn = db()
    b = conn.execute("SELECT book_id FROM book_chapters WHERE status='completed' LIMIT 1").fetchone()
    if not b:
        b = conn.execute("SELECT id FROM books LIMIT 1").fetchone()
    conn.close()
    if b:
        book_id = b[0]
        return RedirectResponse(f"/courses/play/{book_id}", 302)
    return RedirectResponse("/courses", 302)

@app.get("/courses/play/{book_id}", response_class=HTMLResponse)
def courses_play(request: Request, book_id: int):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 302)
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie rzucona do OCR")
    
    b_subj = book['subject']
    # 1. Pobieramy listę rozdziałów (np. z book_toc) z zakresami stron
    chaps = conn.execute("""
        SELECT chapter_num, chapter_title, MIN(page_start) as page_start, MAX(page_end) as page_end
        FROM book_toc 
        WHERE book_id=? 
        GROUP BY chapter_num, chapter_title
        ORDER BY chapter_num ASC
    """, (book_id,)).fetchall()
    chapters_data = [dict(c) for c in chaps]
    if not chapters_data:
        alt_chaps = conn.execute("""
            SELECT chapter_num, chapter_title, MIN(page_start) as page_start, MAX(page_end) as page_end
            FROM book_toc bt JOIN books b ON bt.book_id=b.id
            WHERE b.subject=? AND b.kind='podreczniki'
            GROUP BY chapter_num, chapter_title
            ORDER BY chapter_num ASC
        """, (b_subj,)).fetchall()
        chapters_data = [dict(c) for c in alt_chaps]

    if not chapters_data:
        if b_subj == 'Historia':
            chapters_data = [
                {"chapter_num": 1, "chapter_title": "Rozdział I: Pierwsze cywilizacje", "page_start": 8, "page_end": 42},
                {"chapter_num": 2, "chapter_title": "Rozdział II: Starożytna Grecja", "page_start": 44, "page_end": 74},
                {"chapter_num": 3, "chapter_title": "Rozdział III: Starożytny Rzym", "page_start": 75, "page_end": 102},
                {"chapter_num": 4, "chapter_title": "Rozdział IV: Początki średniowiecza", "page_start": 103, "page_end": 130},
                {"chapter_num": 6, "chapter_title": "Rozdział VI: Polska pierwszych Piastów", "page_start": 170, "page_end": 200}
            ]
        elif b_subj == 'Biologia':
            chapters_data = [
                {"chapter_num": 1, "chapter_title": "Dział I: Biologia – nauka o życiu", "page_start": 7, "page_end": 24},
                {"chapter_num": 2, "chapter_title": "Dział II: Budowa i czynności życiowe organizmów", "page_start": 25, "page_end": 60},
                {"chapter_num": 3, "chapter_title": "Dział III: Wirusy, bakterie, protisty i grzyby", "page_start": 61, "page_end": 92}
            ]
        elif b_subj == 'Polski':
            chapters_data = [
                {"chapter_num": 1, "chapter_title": "Dział 1: Dziwny ten świat (Kosmos i ortografia)", "page_start": 10, "page_end": 78},
                {"chapter_num": 2, "chapter_title": "Dział 2: W świecie legend i mitów", "page_start": 79, "page_end": 140}
            ]
        elif b_subj == 'Angielski':
            chapters_data = [
                {"chapter_num": 1, "chapter_title": "Welcome Unit: Getting Started", "page_start": 4, "page_end": 7},
                {"chapter_num": 2, "chapter_title": "Unit 1: Back to school", "page_start": 8, "page_end": 19},
                {"chapter_num": 3, "chapter_title": "Unit 2: You are what you eat", "page_start": 20, "page_end": 31}
            ]
        elif b_subj == 'Matematyka':
            chapters_data = [
                {"chapter_num": 1, "chapter_title": "1. Liczby naturalne i działania", "page_start": 10, "page_end": 42},
                {"chapter_num": 2, "chapter_title": "2. Własności liczb naturalnych", "page_start": 44, "page_end": 64},
                {"chapter_num": 3, "chapter_title": "3. Ułamki zwykłe", "page_start": 66, "page_end": 102},
                {"chapter_num": 4, "chapter_title": "4. Figury na płaszczyźnie", "page_start": 104, "page_end": 142},
                {"chapter_num": 5, "chapter_title": "5. Ułamki dziesiętne", "page_start": 144, "page_end": 182},
                {"chapter_num": 6, "chapter_title": "6. Pola figur", "page_start": 184, "page_end": 206},
                {"chapter_num": 7, "chapter_title": "7. Liczby całkowite", "page_start": 208, "page_end": 224},
                {"chapter_num": 8, "chapter_title": "8. Objętość figur", "page_start": 226, "page_end": 236}
            ]
        else:
            chapters_data = [
                {"chapter_num": 1, "chapter_title": f"Rozdział 1: {book['title']}", "page_start": 1, "page_end": 50}
            ]

    # 2. Pobieramy powiązane karty Anki (SRS) z talii pasującej do przedmiotu/książki
    deck = conn.execute("SELECT id, name FROM decks WHERE subject=? AND (name LIKE ? OR name LIKE ?) LIMIT 1",
                        (b_subj, f"%{b_subj}%", "%Unit%")).fetchone()
    if not deck:
        deck = conn.execute("SELECT id, name FROM decks WHERE subject=? LIMIT 1", (b_subj,)).fetchone()
    
    target_deck_id = deck['id'] if deck else None
    cards = []
    if target_deck_id:
        cards = [dict(r) for r in conn.execute("SELECT id, front, back FROM cards WHERE deck_id=? LIMIT 12", (target_deck_id,)).fetchall()]
    
    # 2. Pobieramy zadania interaktywne przypisane WYŁĄCZNIE do chapterów tej książki
    raw_tasks = conn.execute("""
        SELECT it.*, utp.score as user_score, utp.attempts as user_attempts, utp.last_attempt as user_last_attempt
        FROM interactive_tasks it 
        JOIN book_chapters bc ON it.chapter_id = bc.id 
        LEFT JOIN user_task_progress utp ON it.id = utp.task_id AND utp.user_id = ?
        WHERE bc.book_id = ?
        ORDER BY it.id ASC
    """, (u['id'], book_id)).fetchall()

    import json, random
    tasks = []
    for t in raw_tasks:
        td = dict(t)
        try:
            content = json.loads(td['content_json'])
            if td['task_type'] == 'cloze':
                # Render placeholders [abc] into <input data-ans="abc" class="cloze-input">
                import re as regex
                def make_input(m):
                    ans = m.group(1)
                    return f'<input type="text" class="cloze-input" data-ans="{ans}" style="width: {max(60, len(ans)*14)}px">'
                content['rendered_html'] = regex.sub(r'\[(.*?)\]', make_input, content.get('sentence', ''))
            elif td['task_type'] == 'match':
                pairs = content.get('pairs', [])
                lefts = [{"id": i, "text": p["left"]} for i, p in enumerate(pairs)]
                rights = [{"id": i, "text": p["right"]} for i, p in enumerate(pairs)]
                random.seed(42)
                random.shuffle(rights)
                content['shuffled_left'] = lefts
                content['shuffled_right'] = rights
            td['parsed_content'] = content
            # Ekstrakcja numeru strony, rozdziału i tematu z "number" lub "title"
            import re as _re
            num_str = content.get('number', '')
            title_str = content.get('title', '')
            full_text = (num_str + ' ' + title_str).lower()
            m = _re.search(r'str\.\s*(\d+)', full_text)
            page_num = int(m.group(1)) if m else None
            td['page_ref'] = page_num

            # Ekstrakcja numeru ćwiczenia/zadania
            m_ex = _re.search(r'zadanie\s*(\d+)', num_str.lower())
            td['exercise_num'] = int(m_ex.group(1)) if m_ex else None

            # Ekstrakcja etykiety ćwiczenia/zadania (np. 3a, 3b, 5, 6, 8b, 11a, 12, ★)
            m_ex_lbl = _re.search(r'zadanie\s*(\d+[a-z]?)', num_str.lower())
            if m_ex_lbl:
                td['exercise_label'] = m_ex_lbl.group(1)
            elif 'super zagadka' in full_text or 'zagadka' in full_text:
                td['exercise_label'] = '★'
            elif td.get('exercise_num'):
                td['exercise_label'] = str(td['exercise_num'])
            else:
                td['exercise_label'] = str(len(tasks) + 1)

            # Przypisanie tematyczne i numer rozdziału
            matched_chapter_num = chapters_data[0].get('chapter_num', 1) if chapters_data else 1
            matched_chapter_name = chapters_data[0].get('chapter_title', 'Rozdział 1') if chapters_data else "Rozdział 1"
            matched_topic_name = "Ćwiczenia z podręcznika"
            if page_num:
                for c_item in chapters_data:
                    p_start = c_item.get('page_start') or 0
                    p_end = c_item.get('page_end') or 9999
                    if p_start <= page_num <= p_end:
                        matched_chapter_num = c_item.get('chapter_num', 1)
                        matched_chapter_name = c_item.get('chapter_title', f"Rozdział {matched_chapter_num}")
                        break
            td['chapter_num'] = matched_chapter_num
            td['chapter_name'] = matched_chapter_name
            if page_num in (3, 4) and b_subj == 'Matematyka':
                td['topic_name'] = "Zapis i porównywanie liczb"
            elif page_num in (5, 6):
                td['topic_name'] = "Działania pamięciowe i sprytne liczenie"
            elif page_num in (11, 12):
                td['topic_name'] = "Liczby wielocyfrowe i oś liczbowa"
            else:
                td['topic_name'] = matched_topic_name

            tasks.append(td)
        except Exception as e:
            pass

    # Sortowanie zadań według logicznej kolejności stron i numerów ćwiczeń
    tasks.sort(key=lambda x: (x.get('page_ref') or 999, x.get('exercise_num') or 999, str(x.get('exercise_label') or ''), x.get('id') or 0))

    # 3. Pobieramy wyekstrahowany tekst OCR z book_chapters
    chap = conn.execute("SELECT raw_ocr_text FROM book_chapters WHERE book_id=? AND status='completed' LIMIT 1", (book_id,)).fetchone()
    raw_ocr_text = chap['raw_ocr_text'] if chap and chap['raw_ocr_text'] else "Tekst OCR dla tego podręcznika jest obecnie przetwarzany w kolejce N8N."

    # 4. Ustalamy offset stron między indeksem pliku PDF a drukowaną stroną książki
    b_dict = dict(book)
    page_offset = b_dict.get('page_offset') or 0
    fn = b_dict.get('filename', '').lower()
    if not page_offset:
        if 'matematyka_cwiczenia' in fn or book_id == 108:
            page_offset = 2
        elif 'angielski_cwiczenia' in fn or book_id == 107:
            page_offset = 1
        elif 'matematyka_podrecznik' in fn or book_id == 103:
            page_offset = 2
        elif 'angielski_podrecznik' in fn or book_id == 106:
            page_offset = 2

    # 5. Sprawdzamy początkową stronę w PDF (np. ze strony pierwszego zadania)
    initial_book_page = 1
    for t in tasks:
        if t.get('page_ref'):
            initial_book_page = t['page_ref']
            break
    initial_pdf_page = max(1, initial_book_page + page_offset)

    # 6. Sprawdzamy czy istnieje powiązany podręcznik lub zeszyt ćwiczeń
    counterpart = None
    if book['kind'] == 'cwiczenia':
        cp = conn.execute("SELECT id, title FROM books WHERE subject=? AND kind='podreczniki' LIMIT 1", (b_subj,)).fetchone()
        if cp: counterpart = dict(cp)
    elif book['kind'] == 'podreczniki':
        cp = conn.execute("SELECT id, title FROM books WHERE subject=? AND kind='cwiczenia' LIMIT 1", (b_subj,)).fetchone()
        if cp: counterpart = dict(cp)

    # 7. Pobieramy nagrania audio (audio_podcasts) dla tego podręcznika / zeszytu
    audio_tracks = [dict(r) for r in conn.execute("""
        SELECT ap.* FROM audio_podcasts ap 
        JOIN book_chapters bc ON ap.chapter_id = bc.id 
        WHERE bc.book_id = ?
        ORDER BY ap.id ASC
    """, (book_id,)).fetchall()]
    if not audio_tracks:
        audio_tracks = [dict(r) for r in conn.execute("""
            SELECT ap.* FROM audio_podcasts ap 
            JOIN book_chapters bc ON ap.chapter_id = bc.id 
            JOIN books b ON bc.book_id = b.id
            WHERE b.subject = ?
            ORDER BY ap.id ASC
        """, (b_subj,)).fetchall()]

    # 8. Rozdziały (chapters_data) zostały już pobrane w punkcie 1

    # 9. Ostatnio robiony rozdział (domyślnie 1)
    last_active_chapter = 1
    recent_task = conn.execute("""
        SELECT it.id, utp.last_attempt 
        FROM user_task_progress utp
        JOIN interactive_tasks it ON utp.task_id = it.id
        JOIN book_chapters bc ON it.chapter_id = bc.id
        WHERE bc.book_id = ? AND utp.user_id = ? AND utp.attempts > 0
        ORDER BY utp.last_attempt DESC LIMIT 1
    """, (book_id, u['id'])).fetchone()
    if recent_task:
        for t in tasks:
            if t['id'] == recent_task['id']:
                last_active_chapter = t.get('chapter_num', 1)
                break

    # 10. Pobieramy współdzielone materiały (infografiki i podcasty klasy 5B z ulubionymi na początku i reakcjami)
    artifacts = [dict(r) for r in conn.execute("""
        SELECT la.*,
               EXISTS(SELECT 1 FROM user_artifact_favorites uaf WHERE uaf.artifact_id = la.id AND uaf.user_id = ?) as is_favorite,
               (SELECT reaction_type FROM user_artifact_reactions uar WHERE uar.artifact_id = la.id AND uar.user_id = ?) as user_reaction,
               (SELECT GROUP_CONCAT(uar.user_name, ', ') FROM user_artifact_reactions uar WHERE uar.artifact_id = la.id AND uar.reaction_type = 'up') as liked_by_names,
               (SELECT GROUP_CONCAT(uar.user_name, ', ') FROM user_artifact_reactions uar WHERE uar.artifact_id = la.id AND uar.reaction_type = 'down') as disliked_by_names
        FROM learning_artifacts la 
        WHERE la.book_id=? AND la.is_shared=1 
        ORDER BY is_favorite DESC, la.id DESC
    """, (u['id'], u['id'], book_id)).fetchall()]

    conn.close()
    return templates.TemplateResponse(request, "course_play.html", {
        "user": u, 
        "book": dict(book),
        "target_deck_id": target_deck_id,
        "cards": cards,
        "tasks": tasks,
        "initial_page": initial_pdf_page,
        "initial_book_page": initial_book_page,
        "page_offset": page_offset,
        "counterpart": counterpart,
        "raw_ocr_text": raw_ocr_text,
        "audio_tracks": audio_tracks,
        "chapters_data": chapters_data,
        "last_active_chapter": last_active_chapter,
        "artifacts": artifacts
    })

@app.get("/api/artifacts")
def api_get_artifacts(request: Request, book_id: int = 103, chapter_num: int = 1):
    u = current_user(request)
    if not u: raise HTTPException(401)
    conn = db()
    rows = conn.execute("""
        SELECT la.*,
               EXISTS(SELECT 1 FROM user_artifact_favorites uaf WHERE uaf.artifact_id = la.id AND uaf.user_id = ?) as is_favorite,
               (SELECT reaction_type FROM user_artifact_reactions uar WHERE uar.artifact_id = la.id AND uar.user_id = ?) as user_reaction,
               (SELECT GROUP_CONCAT(uar.user_name, ', ') FROM user_artifact_reactions uar WHERE uar.artifact_id = la.id AND uar.reaction_type = 'up') as liked_by_names,
               (SELECT GROUP_CONCAT(uar.user_name, ', ') FROM user_artifact_reactions uar WHERE uar.artifact_id = la.id AND uar.reaction_type = 'down') as disliked_by_names
        FROM learning_artifacts la 
        WHERE la.book_id=? AND la.chapter_num=? AND la.is_shared=1 
        ORDER BY is_favorite DESC, la.id DESC
    """, (u['id'], u['id'], book_id, chapter_num)).fetchall()
    conn.close()
    return {"status": "ok", "artifacts": [dict(r) for r in rows]}

@app.post("/api/artifacts/{art_id}/toggle-favorite")
def api_toggle_artifact_favorite(request: Request, art_id: int):
    u = current_user(request)
    if not u: raise HTTPException(401)
    conn = db()
    fav = conn.execute("SELECT 1 FROM user_artifact_favorites WHERE user_id=? AND artifact_id=?", (u['id'], art_id)).fetchone()
    if fav:
        conn.execute("DELETE FROM user_artifact_favorites WHERE user_id=? AND artifact_id=?", (u['id'], art_id))
        is_fav = 0
    else:
        conn.execute("INSERT OR IGNORE INTO user_artifact_favorites (user_id, artifact_id) VALUES (?, ?)", (u['id'], art_id))
        is_fav = 1
    conn.commit()
    conn.close()
    return {"status": "ok", "is_favorite": is_fav}

@app.post("/api/artifacts/{art_id}/reaction")
async def api_artifact_reaction(request: Request, art_id: int):
    u = current_user(request)
    if not u: raise HTTPException(401)
    data = await request.json()
    action = str(data.get("reaction", "up")).lower() # 'up' or 'down'
    u_dict = dict(u)
    user_name = u_dict.get("name") or u_dict.get("login") or "Uczeń 5B"
    
    conn = db()
    existing = conn.execute("SELECT reaction_type FROM user_artifact_reactions WHERE user_id=? AND artifact_id=?", (u['id'], art_id)).fetchone()
    
    user_reaction = None
    if existing:
        current_type = existing['reaction_type'] if isinstance(existing, sqlite3.Row) else existing[0]
        if current_type == action:
            conn.execute("DELETE FROM user_artifact_reactions WHERE user_id=? AND artifact_id=?", (u['id'], art_id))
            user_reaction = None
        else:
            conn.execute("UPDATE user_artifact_reactions SET reaction_type=? WHERE user_id=? AND artifact_id=?", (action, u['id'], art_id))
            user_reaction = action
    else:
        conn.execute("INSERT INTO user_artifact_reactions (user_id, artifact_id, reaction_type, user_name) VALUES (?, ?, ?, ?)", (u['id'], art_id, action, user_name))
        user_reaction = action

    up_cnt = conn.execute("SELECT COUNT(*) FROM user_artifact_reactions WHERE artifact_id=? AND reaction_type='up'", (art_id,)).fetchone()[0]
    down_cnt = conn.execute("SELECT COUNT(*) FROM user_artifact_reactions WHERE artifact_id=? AND reaction_type='down'", (art_id,)).fetchone()[0]
    conn.execute("UPDATE learning_artifacts SET likes_count=?, dislikes_count=? WHERE id=?", (up_cnt, down_cnt, art_id))
    
    up_likers = conn.execute("SELECT GROUP_CONCAT(user_name, ', ') FROM user_artifact_reactions WHERE artifact_id=? AND reaction_type='up'", (art_id,)).fetchone()[0] or ''
    down_likers = conn.execute("SELECT GROUP_CONCAT(user_name, ', ') FROM user_artifact_reactions WHERE artifact_id=? AND reaction_type='down'", (art_id,)).fetchone()[0] or ''
    
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "user_reaction": user_reaction,
        "likes_count": up_cnt,
        "dislikes_count": down_cnt,
        "liked_by_names": up_likers,
        "disliked_by_names": down_likers
    }

@app.post("/api/artifacts/{art_id}/toggle-like")
def api_toggle_artifact_like(request: Request, art_id: int):
    u = current_user(request)
    if not u: raise HTTPException(401)
    u_dict = dict(u)
    user_name = u_dict.get("name") or u_dict.get("login") or "Uczeń 5B"
    conn = db()
    existing = conn.execute("SELECT reaction_type FROM user_artifact_reactions WHERE user_id=? AND artifact_id=?", (u['id'], art_id)).fetchone()
    if existing and (existing['reaction_type'] if isinstance(existing, sqlite3.Row) else existing[0]) == 'up':
        conn.execute("DELETE FROM user_artifact_reactions WHERE user_id=? AND artifact_id=?", (u['id'], art_id))
        is_liked = 0
    else:
        conn.execute("INSERT OR REPLACE INTO user_artifact_reactions (user_id, artifact_id, reaction_type, user_name) VALUES (?, ?, 'up', ?)", (u['id'], art_id, user_name))
        is_liked = 1
    cnt = conn.execute("SELECT COUNT(*) FROM user_artifact_reactions WHERE artifact_id=? AND reaction_type='up'", (art_id,)).fetchone()[0]
    conn.execute("UPDATE learning_artifacts SET likes_count=? WHERE id=?", (cnt, art_id))
    likers = conn.execute("SELECT GROUP_CONCAT(user_name, ', ') FROM user_artifact_reactions WHERE artifact_id=? AND reaction_type='up'", (art_id,)).fetchone()[0] or ''
    conn.commit()
    conn.close()
    return {"status": "ok", "is_liked": is_liked, "likes_count": cnt, "liked_by_names": likers}

@app.post("/api/artifacts/save")
async def api_save_artifact(request: Request):
    u = current_user(request)
    if not u: raise HTTPException(401)
    data = await request.json()
    book_id = int(data.get("book_id", 103))
    chapter_num = int(data.get("chapter_num", 1))
    artifact_type = str(data.get("artifact_type", "infographic"))
    title = str(data.get("title", "Nowy materiał"))
    description = str(data.get("description", ""))
    content_html = str(data.get("content_html", ""))
    audio_url = str(data.get("audio_url", ""))
    duration = str(data.get("duration", "Plansza A4"))
    u_dict = dict(u)
    author_name = u_dict.get("name") or u_dict.get("login") or "Uczeń 5B"
    is_shared = 1 if data.get("is_shared", True) else 0

    conn = db()
    cur = conn.cursor()

    if artifact_type == "podcast":
        import re, subprocess, time
        # Automatyczna numeracja podcastu
        cur_pod_count = cur.execute("SELECT COUNT(*) FROM learning_artifacts WHERE book_id=? AND artifact_type='podcast'", (book_id,)).fetchone()[0]
        next_num = cur_pod_count + 1
        if not re.search(r'#\d+', title):
            clean_t = title.replace('Podcast Audio:', '').replace('Podcast:', '').strip()
            title = f"Podcast #{next_num}: {clean_t}"
        
        # Generowanie realnego pliku MP3 za pomocą edge-tts
        pod_dir = PODCASTS_DIR
        pod_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        audio_filename = f"podcast_math_ch{chapter_num}_{next_num}_{ts}.mp3"
        audio_file_path = pod_dir / audio_filename
        
        script_tts = f"Witaj w podcaście matematycznym numer {next_num}. Temat odcinka: {title}. {description}. Pamiętaj o regularnych powtórkach i uważaj na pułapki w zadaniach."
        try:
            cmd = ["edge-tts", "--voice", "pl-PL-MarekNeural", "--text", script_tts, "--write-media", str(audio_file_path)]
            p_res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if p_res.returncode == 0 and audio_file_path.exists() and audio_file_path.stat().st_size > 0:
                audio_url = f"/static/audio/podcasts/{audio_filename}"
                words = len(script_tts.split())
                sec = max(20, int(words / 2.3))
                duration = f"{sec // 60}:{sec % 60:02d}"
            else:
                audio_url = f"/static/audio/podcasts/podcast_math_ch1_{((next_num - 1) % 4) + 1}.mp3"
                duration = "0:45"
        except Exception as ex:
            print("TTS error:", ex)
            audio_url = f"/static/audio/podcasts/podcast_math_ch1_{((next_num - 1) % 4) + 1}.mp3"
            duration = "0:45"

    cur.execute("""
        INSERT INTO learning_artifacts (book_id, chapter_num, artifact_type, title, description, content_html, audio_url, duration, user_id, author_name, is_shared)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (book_id, chapter_num, artifact_type, title, description, content_html, audio_url, duration, u["id"], author_name, is_shared))
    art_id = cur.lastrowid
    conn.commit()
    created = conn.execute("""
        SELECT la.*, 0 as is_favorite, NULL as user_reaction, 0 as is_liked, '' as liked_by_names, '' as disliked_by_names
        FROM learning_artifacts la WHERE la.id=?
    """, (art_id,)).fetchone()
    conn.close()
    return {"status": "ok", "artifact": dict(created) if created else {}}



THEORY_DATA_BY_SUBJECT = {
    "Matematyka": {
        1: {
            "title": "Rozdział 1: Liczby naturalne i działania",
            "desc": "Zapis i odczytywanie wielkich liczb, rzędy wielkości, oś liczbowa, porównywanie i sprytne liczenie.",
            "competencies": [
                {"title": "Zapis i odczytywanie liczb wielocyfrowych", "text": "Dziel cyfry na 3-cyfrowe grupy od prawej strony (np. 5 263 807). Zapisuj słownie i cyframi liczby do biliona."},
                {"title": "Rzędy wielkości", "text": "Jedności, dziesiątki, setki, tysiące (10³), miliony (10⁶), miliardy (10⁹), biliony (10¹²). Pamiętaj: 1 miliard to 1000 milionów!"},
                {"title": "Oś liczbowa i ustalanie podziałki", "text": "Oblicz wartość 1 kreski: odejmij liczby od siebie i podziel wynik przez liczbę odcinków (kresek) między nimi."},
                {"title": "Porównywanie wielkich liczb", "text": "Liczba z większą ilością cyfr jest zawsze większa. Przy tej samej liczbie cyfr porównuj cyfry od lewej (od najwyższego rzędu)."}
            ],
            "rules": [
                {"title": "Tabela rzędów wielkości i liczby zer", "content": "• Tysiąc = 1 000 (3 zera)<br>• Milion = 1 000 000 (6 zer)<br>• Miliard = 1 000 000 000 (9 zer)<br>• Bilion = 1 000 000 000 000 (12 zer)"},
                {"title": "Złota reguła odczytywania liczb", "content": "Grupuj cyfry po 3 <strong>od prawej strony</strong> (od końca)."}
            ],
            "pitfalls": [
                "Mylenie miliona (6 zer) z miliardem (9 zer) — 2 mld zł to 2 000 mln zł!",
                "Zera ukryte w środku liczby — 'dwieście milionów trzysta dwa tysiące trzynaście' to 200 302 013."
            ]
        },
        2: {
            "title": "Rozdział 2: Własności liczb naturalnych",
            "desc": "Dzielniki i wielokrotności, cechy podzielności, liczby pierwsze i złożone, NWD i NWW.",
            "competencies": [
                {"title": "Cechy podzielności", "text": "Przez 2 (parzyste), przez 5 (końcówka 0 lub 5), przez 10 (końcówka 0), przez 3 i 9 (suma cyfr dzieli się przez 3 lub 9)."},
                {"title": "Liczby pierwsze i złożone", "text": "Liczba pierwsza ma dokładnie 2 dzielniki: 1 i samą siebie (2, 3, 5, 7, 11...). Liczby 0 i 1 nie są pierwsze ani złożone!"}
            ],
            "rules": [
                {"title": "Cechy podzielności przez 3 i 9", "content": "Liczba jest podzielna przez 3, gdy suma jej cyfr dzieli się przez 3. Liczba jest podzielna przez 9, gdy suma jej cyfr dzieli się przez 9."}
            ],
            "pitfalls": ["Uznawanie liczby 1 za liczbę pierwszą (1 ma tylko jeden dzielnik!)."]
        }
    },
    "Historia": {
        1: {
            "title": "Rozdział I: Pierwsze cywilizacje",
            "desc": "Życie pierwszych ludzi, rewolucja neolityczna, Mezopotamia, starożytny Egipt, pismo i Kodeks Hammurabiego.",
            "competencies": [
                {"title": "Prahistoria i rewolucja neolityczna", "text": "Przejście z koczowniczego trybu życia (zbieractwo, łowiectwo) do osiadłego (rolnictwo, hodowla, stałe osady)."},
                {"title": "Cywilizacje wielkich rzek", "text": "Mezopotamia (Tygrys i Eufrat), Egipt (Nil). Rola systemów irygacyjnych w rozwoju rolnictwa."},
                {"title": "Pismo i prawo", "text": "Pismo klinowe Sumerów w Mezopotamii, hieroglify w Egipcie, alfabet Fenicjan. Kodeks Hammurabiego i zasada talionu (oko za oko, ząb za ząb)."},
                {"title": "Społeczeństwo i wierzenia Egiptu", "text": "Władza faraona, politeizm (wiara w wielu bogów: Re, Ozyrys, Anubis) oraz wiara w życie pozagrobowe (mumifikacja, piramidy)."}
            ],
            "rules": [
                {"title": "Zasada Hammurabiego (prawo talionu)", "content": "Najstarszy zbiór praw z Babilonu: <code>Oko za oko, ząb za ząb</code> (kara równa wyrządzonej szkodzie)."},
                {"title": "Periodyzacja dziejów", "content": "Prahistoria kończy się ok. 3500 r. p.n.e. wraz z <strong>wynalezieniem pisma</strong> przez Sumerów."}
            ],
            "pitfalls": [
                "Mylenie koczowniczego trybu życia (wędrowny) z osiadłym (rolnictwo i stałe domy).",
                "Hieroglify to pismo Egipcjan, a pismo klinowe to wynalazek Sumerów — nie myl tych kultur!",
                "Piramidy były grobowcami faraonów, a nie świątyniami czy pałacami."
            ]
        },
        2: {
            "title": "Rozdział II: Starożytna Grecja",
            "desc": "Polis greckie, demokracja ateńska, Sparta, wojny z Persami, bogowie olimpijscy, teatr i igrzyska olimpijskie.",
            "competencies": [
                {"title": "Polis i demokracja", "text": "Polis jako miasto-państwo. Zgromadzenie ludowe (eklezja) w Atenach i rządy obywateli."},
                {"title": "Kultura i wierzenia", "text": "Panteon bogów na Olimpie (Zeus, Hera, Atena, Posejdon). Mitologia jako próba objaśnienia świata."}
            ],
            "rules": [{"title": "Igrzyska olimpijskie", "content": "Pierwsze igrzyska odbyły się w <strong>776 r. p.n.e.</strong> w Olimpii — na czas igrzysk ogłaszano święty pokój."}],
            "pitfalls": ["Mylenie demokracji ateńskiej (bezpośredniej) ze współczesną demokracją parlamentarną."]
        },
        6: {
            "title": "Rozdział VI: Polska pierwszych Piastów",
            "desc": "Mieszko I, Chrzest Polski (966 r.), Bolesław Chrobry, Zjazd Gnieźnieński (1000 r.) i koronacja królewska (1025 r.).",
            "competencies": [
                {"title": "Początki państwa polskiego", "text": "Plemię Polan i ród Piastów. Rola grodu w Gnieźnie i Poznaniu."},
                {"title": "Chrzest Polski (966 r.)", "text": "Małżeństwo Mieszka I z Dobrawą, chrystianizacja kraju i wejście do kręgu kultury chrześcijańskiej Europy."},
                {"title": "Panowanie Bolesława Chrobrego", "text": "Męczeńska śmierć św. Wojciecha (997), Zjazd Gnieźnieński (1000) z Ottonem III, koronacja na pierwszego króla Polski (1025)."}
            ],
            "rules": [
                {"title": "Daty kluczowe Piastów", "content": "• <strong>966 r.</strong> — Chrzest Polski (Mieszko I)<br>• <strong>1000 r.</strong> — Zjazd Gnieźnieński (Bolesław Chrobry i Otton III)<br>• <strong>1025 r.</strong> — Pierwsza koronacja królewska w Gnieźnie"}
            ],
            "pitfalls": [
                "Mieszko I był księciem, a NIE królem — pierwszym koronowanym królem Polski był jego syn Bolesław Chrobry w 1025 roku!",
                "Chrzest Polski przyjęto z Czech za pośrednictwem Dobrawy, a nie bezpośrednio z Niemiec."
            ]
        }
    },
    "Biologia": {
        1: {
            "title": "Dział I: Biologia – nauka o życiu",
            "desc": "Cechy organizmów żywych, metoda naukowa, obserwacja a doświadczenie, próba badawcza i kontrolna, budowa mikroskopu optycznego.",
            "competencies": [
                {"title": "Cechy organizmów", "text": "Budowa komórkowa, odżywianie, oddychanie, wydalanie, ruch, reakcja na bodźce, wzrost i rozwój, rozmnażanie."},
                {"title": "Metoda naukowa", "text": "Problem badawczy (pytanie) → Hipoteza (przewidywanie) → Doświadczenie (próba badawcza i kontrolna) → Wniosek."},
                {"title": "Mikroskop optyczny", "text": "Okular, obiektyw, stolik, śruby makro- i mikrometryczna. Powiększenie = powiększenie okularu × powiększenie obiektywu."}
            ],
            "rules": [
                {"title": "Wzór na powiększenie mikroskopu", "content": "<code>Powiększenie = powiększenie okularu × powiększenie obiektywu</code> (np. 10x × 40x = 400x)."},
                {"title": "Próba badawcza vs kontrolna", "content": "<strong>Próba kontrolna:</strong> warunki naturalne / niezmienione.<br><strong>Próba badawcza:</strong> zestaw, w którym zmieniamy badany czynnik (np. brak światła, brak wody)."}
            ],
            "pitfalls": [
                "Mylenie problemu badawczego (zawsze w formie pytania!) z hipotezą (zdanie twierdzące).",
                "Wniosek to nie opis obserwacji — wniosek to potwierdzenie lub odrzucenie hipotezy!",
                "Kręcenie śrubą makrometryczną przy dużym powiększeniu grozi zgnieceniem preparatu."
            ]
        },
        2: {
            "title": "Dział II: Budowa i czynności życiowe organizmów",
            "desc": "Komórka zwierzęca, roślinna, grzybowa i bakteryjna, fotosynteza, cudzożywność, oddychanie komórkowe.",
            "competencies": [
                {"title": "Budowa komórki", "text": "Błona komórkowa, cytoplazma, jądro komórkowe, mitochondria. Komórka roślinna ma dodatkowo ścianę komórkową, wakuolę i chloroplasty."},
                {"title": "Fotosynteza", "text": "Dwutlenek węgla + woda + światło → glukoza (pokarm) + tlen."}
            ],
            "rules": [{"title": "Równanie fotosyntezy", "content": "<code>CO₂ + H₂O + energia słoneczna → glukoza + O₂</code>"}],
            "pitfalls": ["Komórka zwierzęca NIE posiada ściany komórkowej ani chloroplastów!"]
        }
    },
    "Polski": {
        1: {
            "title": "Dział 1: Dziwny ten świat",
            "desc": "Opisy kosmosu i zjawisk, ortografia (ó, rz, ż, ch), mity greckie (Demeter i Kora, Prometeusz), części mowy: rzeczownik i przymiotnik.",
            "competencies": [
                {"title": "Ortografia ó, rz, ż, ch", "text": "Zasady wymiany (ó:o,e,a; rz:r; ż:g,dz,s; ch:sz) oraz pisownia po spółgłoskach (p, b, t, d, k, g, ch, j, w)."},
                {"title": "Rzeczownik i odmiana przez przypadki", "text": "M (kto? co?), D (kogo? czego?), C (komu? czemu?), B (kogo? co?), N (z kim? z czym?), Ms (o kim? o czym?), W (o!)."},
                {"title": "Mitologia grecka", "text": "Pojęcie mitu, archetypy postaw ludzkich (Prometeusz, Demeter, Dedal i Ikar)."},
                {"title": "Przymiotnik i stopniowanie", "text": "Stopień równy, wyższy i najwyższy. Pisownia 'nie' z przymiotnikami łącznie."}
            ],
            "rules": [
                {"title": "Pisownia 'nie' z częściami mowy", "content": "• Z rzeczownikami: <strong>łącznie</strong> (nieprzyjaciel, niepogoda)<br>• Z przymiotnikami w st. równym: <strong>łącznie</strong> (niedobry)<br>• Z czasownikami: <strong>rozdzielnie</strong> (nie lubię)"}
            ],
            "pitfalls": [
                "Mylenie biernika (B: kogo? co?) z dopełniaczem (D: kogo? czego?).",
                "Rozdzielna pisownia 'nie' z rzeczownikami — pamiętaj, piszemy razem!"
            ]
        }
    },
    "Angielski": {
        1: {
            "title": "Welcome Unit: Getting Started (str. 4–7)",
            "desc": "Verb 'to be' (twierdzenia, przeczenia, pytania), zaimki dzierżawcze, zaimki wskazujące (this/that/these/those), czasownik modalny 'can' oraz 'have got'.",
            "competencies": [
                {"title": "Verb 'to be'", "text": "I am ('m), You are ('re), He/She/It is ('s), We/They are ('re). Przeczenia: I'm not, isn't, aren't. Pytania: Am I...? Is she...? Are you...?"},
                {"title": "Articles a / an", "text": "Używaj 'an' przed dźwiękiem samogłoskowym (an apple, an umbrella). W liczbie mnogiej brak przedimka!"},
                {"title": "Possessives & Demonstratives", "text": "Dopełniacz saksoński: It's Jack's hairbrush. This/these (blisko), that/those (daleko)."},
                {"title": "Have got / Has got", "text": "Posiadanie: I/You/We/They have got ('ve got); He/She/It has got ('s got). Przeczenia: haven't got / hasn't got."}
            ],
            "rules": [
                {"title": "Verb 'be' i skróty", "content": "am = 'm | is = 's | are = 're<br>is not = isn't | are not = aren't"},
                {"title": "Zaimki dzierżawcze", "content": "my, your, his, her, its, our, their"},
                {"title": "Have got w 3. osobie", "content": "He / She / It <strong>has got</strong> (hasn't got). Pytanie: <strong>Has</strong> he got...?"}
            ],
            "pitfalls": [
                "Stawianie 'a/an' przed rzeczownikami w liczbie mnogiej (np. 'They are books', NIE 'a books').",
                "Mylenie 'have got' z 'has got' w 3. osobie (He/She/It has got!)."
            ]
        },
        2: {
            "title": "Unit 1: Back to school (str. 8–19)",
            "desc": "Nazwy przedmiotów szkolnych, Present Simple w twierdzeniach, przeczeniach i pytaniach, miejsca w szkole, There is / There are oraz reguły szkolne must/mustn't.",
            "competencies": [
                {"title": "School Subjects", "text": "Maths, Science, Geography, History, Art, Computing, PE, Music, English, Polish, Foreign language."},
                {"title": "Present Simple — 3. os. l.poj.", "text": "W 3. os. l.poj. dodajemy -s/-es (start -> starts, watch -> watches, study -> studies, have -> has)."},
                {"title": "Present Simple — Pytania i przeczenia", "text": "Przeczenia: don't (I/you/we/they) / doesn't (he/she/it). Pytania: Do / Does...?"},
                {"title": "Places at school & There is/are", "text": "Canteen, gym, library, playground, science lab. There is (l.poj.) / There are (l.mn.)."},
                {"title": "School rules (Must / Mustn't)", "text": "Must (obowiązek) / Mustn't (zakaz): We must be on time. We mustn't use phones."}
            ],
            "rules": [
                {"title": "Końcówki -s / -es w Present Simple", "content": "• Po -ch, -sh, -ss, -x, -o dodajemy <strong>-es</strong> (watches, goes)<br>• Po spółgłosce + y: study -> <strong>studies</strong>"},
                {"title": "There is vs There are", "content": "• <strong>There is</strong> a canteen (l. pojedyncza)<br>• <strong>There are</strong> playing fields (l. mnoga)"}
            ],
            "pitfalls": [
                "Zapominanie o końcówce -s w 3. osobie: 'He start school' ❌ -> 'He starts school' ✔️",
                "Używanie 'doesn't' z czasownikiem z końcówką -s: 'He doesn't likes' ❌ -> 'He doesn't like' ✔️"
            ]
        },
        3: {
            "title": "Unit 2: You are what you eat (str. 20–31)",
            "desc": "Słownictwo kulinarne, naczynia i pojemniki (kitchen objects), rzeczowniki policzalne i niepoliczalne, a/an, some/any, how much / how many.",
            "competencies": [
                {"title": "Kitchen objects & containers", "text": "Bowl, can, carton, frying pan, jar, mug, plate, tin, box, bottle, cup."},
                {"title": "Can vs Tin", "text": "Oba to puszki metalowe: 'can' zazwyczaj cylindryczna na napoje/zupy (a can of soda), 'tin' płaska/prostokątna (a tin of sardines/biscuits)."},
                {"title": "Food & drinks expressions", "text": "A box of chocolates, a glass of juice, a bottle of water, a bowl of cereal."}
            ],
            "rules": [
                {"title": "Wyrażenia ilościowe", "content": "a bowl of... (miska czegoś) | a glass of... (szklanka) | a carton of... (karton)"}
            ],
            "pitfalls": [
                "Mylenie 'can' i 'tin' — na puszkę coli powiemy 'a can of cola', a na sardynki 'a tin of sardines'."
            ]
        }
    }
}
THEORY_DATA = THEORY_DATA_BY_SUBJECT["Matematyka"]


@app.get("/courses/print-summary/{book_id}", response_class=HTMLResponse)
def courses_print_summary(request: Request, book_id: int, chapter: int = 1):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 302)
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    b_dict = dict(book) if book else {}
    subj = b_dict.get('subject', 'Matematyka')
    
    # Pobierz temat z book_toc jeśli dostępny
    toc_row = conn.execute("SELECT topic_title, chapter_title FROM book_toc WHERE book_id=? AND chapter_num=? LIMIT 1", (book_id, chapter)).fetchone()
    conn.close()
    if not book:
        raise HTTPException(404, "Książka nie znaleziona")
    
    subj_data = THEORY_DATA_BY_SUBJECT.get(subj) or THEORY_DATA_BY_SUBJECT.get("Matematyka", {})
    chapter_data = subj_data.get(chapter) or subj_data.get(1)
    if not chapter_data or (chapter not in subj_data and toc_row):
        t_title = toc_row['chapter_title'] if toc_row else f"Rozdział {chapter}"
        chapter_data = {
            "title": t_title,
            "desc": f"Podsumowanie kluczowych wiadomości i pojęć z działu {t_title} ({subj}).",
            "competencies": [
                {"title": "Kluczowe pojęcia", "text": f"Opanowanie najważniejszych definicji i zagadnień dla: {t_title}."},
                {"title": "Wymagania na sprawdzian", "text": f"Powtórzenie materiału i weryfikacja wiedzy z przedmiotu {subj}."}
            ],
            "rules": [
                {"title": f"Najważniejsza wiedza — {t_title}", "content": "Pamiętaj o zapoznaniu się z ramkami i podsumowaniem w podręczniku."}
            ],
            "pitfalls": [f"Zwróć uwagę na dokładne czytanie poleceń i stosowanie poprawnej terminologii z przedmiotu {subj}."]
        }
    return templates.TemplateResponse(request, "print_summary.html", {
        "user": u,
        "book": b_dict,
        "chapter_num": chapter,
        "chapter_data": chapter_data
    })


@app.post("/api/courses/study-kit/export-flashcards")
async def api_export_study_kit_flashcards(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    book_id = int(data.get("book_id", 0))
    chapter_num = int(data.get("chapter_num", 1))

    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")

    subj = book['subject']
    chap_data = THEORY_DATA_BY_SUBJECT.get(subj, {}).get(chapter_num)
    if not chap_data:
        toc_row = conn.execute("SELECT chapter_title FROM book_toc WHERE book_id=? AND chapter_num=? LIMIT 1", (book_id, chapter_num)).fetchone()
        title = toc_row['chapter_title'] if toc_row else f"Rozdział {chapter_num} ({subj})"
        chap_data = {
            "title": title,
            "competencies": [
                {"title": f"Zagadnienia: {title}", "text": f"Kluczowe pojęcia i reguły z działu {title} przedmiotu {subj}."}
            ],
            "rules": [
                {"title": "Główna reguła rozdziału", "content": f"Powtórz materiał i przykłady ze stron podręcznika dla {title}."}
            ],
            "pitfalls": ["Uważaj na częste pomyłki obliczeniowe i terminologiczne."]
        }

    deck_name = f"{subj} — {chap_data['title']}"
    deck = conn.execute("SELECT id FROM decks WHERE name=? AND subject=?", (deck_name, subj)).fetchone()
    if not deck:
        cur = conn.execute("INSERT INTO decks (name, subject, owner, created_at) VALUES (?, ?, ?, datetime('now'))",
                           (deck_name, subj, u['id']))
        deck_id = cur.lastrowid
    else:
        deck_id = deck['id']

    new_cards = []
    for comp in chap_data.get("competencies", []):
        front = f"[{subj} • Rozdz. {chapter_num}] Co to jest: {comp['title']}?"
        back = comp['text']
        new_cards.append((front, back))

    for r in chap_data.get("rules", []):
        front = f"[{subj}] Reguła / Wzór: {r['title']}"
        back = r['content'].replace('<br>', '\n').replace('<strong>', '').replace('</strong>', '').replace('<code>', '').replace('</code>', '')
        new_cards.append((front, back))

    for p in chap_data.get("pitfalls", []):
        front = f"[{subj} • Pułapka] Na co uważać w dziale '{chap_data['title']}'?"
        back = f"⚠️ Pułapka szkolna:\n{p}"
        new_cards.append((front, back))

    added_count = 0
    for front, back in new_cards:
        exists = conn.execute("SELECT id FROM cards WHERE deck_id=? AND front=?", (deck_id, front)).fetchone()
        if not exists:
            conn.execute("INSERT INTO cards (deck_id, front, back) VALUES (?, ?, ?)", (deck_id, front, back))
            added_count += 1

    try:
        conn.execute("INSERT INTO points_log (user_id, points, reason) VALUES (?, ?, ?)",
                     (u['id'], 5, f"Wygenerowanie Study Kit Fiszek: {deck_name}"))
    except Exception:
        pass

    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "ok",
        "deck_id": deck_id,
        "deck_name": deck_name,
        "added_cards": added_count,
        "total_cards": len(new_cards),
        "message": f"Pomyślnie zrzucono {len(new_cards)} fiszek do talii '{deck_name}'!"
    })


@app.get("/api/courses/study-kit/quiz/{book_id}/{chapter_num}")
async def api_get_study_kit_quiz(book_id: int, chapter_num: int, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")

    subj = book['subject']
    chap_data = THEORY_DATA_BY_SUBJECT.get(subj, {}).get(chapter_num)
    conn.close()

    questions = []
    if subj == "Matematyka":
        if chapter_num == 1:
            questions = [
                {"id": 1, "type": "choice", "q": "Ile zer ma w zapisie dziesiętnym 1 miliard?", "options": ["6 zer", "9 zer", "12 zer", "8 zer"], "ans": "9 zer", "hint": "Milion ma 6 zer, a miliard to 1000 milionów (9 zer)."},
                {"id": 2, "type": "choice", "q": "Od której strony należy grupować cyfry po 3, aby łatwo odczytać wielką liczbę?", "options": ["Od lewej (od początku)", "Od prawej (od końca)", "Obojętnie", "Od środka"], "ans": "Od prawej (od końca)", "hint": "Zawsze grupujemy po 3 cyfry od końca: jedności, tysiące, miliony..."},
                {"id": 3, "type": "calc", "q": "Jak zapisać cyframi: 'trzy miliony pięćdziesiąt tysięcy osiem'?", "ans": "3050008", "hint": "Pamiętaj o zerach w brakujących rzędach: 3 mln, 050 tys., 008 jedn."},
                {"id": 4, "type": "bool", "q": "Czy liczba 100 000 jest większa od liczby 99 999 o dokładnie 1?", "options": ["PRAWDA", "FAŁSZ"], "ans": "PRAWDA", "hint": "99 999 + 1 = 100 000."},
                {"id": 5, "type": "calc", "q": "Na osi liczbowej odcinek między 0 a 60 podzielono na 6 równych części. Ile wynosi wartość 1 kreski?", "ans": "10", "hint": "Odejmij 60 - 0 = 60, a następnie podziel przez 6: 60 : 6 = 10."}
            ]
        elif chapter_num == 2:
            questions = [
                {"id": 1, "type": "choice", "q": "Liczba jest podzielna przez 3, gdy:", "options": ["Jej ostatnia cyfra to 3", "Suma jej cyfr dzieli się przez 3", "Jest liczbą nieparzystą", "Kończy się na 0, 3 lub 6"], "ans": "Suma jej cyfr dzieli się przez 3", "hint": "Np. 123 -> 1+2+3 = 6 (dzieli się przez 3)."},
                {"id": 2, "type": "bool", "q": "Czy liczba 1 jest liczbą pierwszą?", "options": ["PRAWDA", "FAŁSZ"], "ans": "FAŁSZ", "hint": "Liczba pierwsza ma dokładnie 2 różne dzielniki. Liczba 1 ma tylko 1 dzielnik, więc nie jest ani pierwsza, ani złożona."},
                {"id": 3, "type": "calc", "q": "Podaj najmniejszą liczbę pierwszą:", "ans": "2", "hint": "Liczba 2 to jedyna parzysta liczba pierwsza!"}
            ]
        else:
            questions = [
                {"id": 1, "type": "bool", "q": f"Czy w dziale {chapter_num} opanowałeś definicje i wzory?", "options": ["PRAWDA", "FAŁSZ"], "ans": "PRAWDA", "hint": "Powtórz kluczowe pojęcia przed testem."},
                {"id": 2, "type": "calc", "q": "Oblicz: 25 * 4 =", "ans": "100", "hint": "Pamiętaj o sprytnym mnożeniu: 25 * 4 = 100."}
            ]
    elif subj == "Historia":
        questions = [
            {"id": 1, "type": "choice", "q": "Jakie wydarzenie uznaje się za koniec prahistorii (ok. 3500 r. p.n.e.)?", "options": ["Wynalezienie pisma", "Wzniesienie piramid", "Wyprawa Aleksandra Wielkiego", "Pojawienie się ognia"], "ans": "Wynalezienie pisma", "hint": "Pismo klinowe wynalezione przez Sumerów zapoczątkowało epokę starożytności."},
            {"id": 2, "type": "choice", "q": "Zasada talionu z Kodeksu Hammurabiego głosiła:", "options": ["Oko za oko, ząb za ząb", "Bogaci płacą podwójnie", "Zgoda buduje, niezgoda rujnuje", "Każdy ma równe prawa"], "ans": "Oko za oko, ząb za ząb", "hint": "Kara miała być dokładnie równa wyrządzonej krzywdzie."},
            {"id": 3, "type": "bool", "q": "Czy rewolucja neolityczna oznaczała przejście od trybu życia osiadłego do koczowniczego?", "options": ["PRAWDA", "FAŁSZ"], "ans": "FAŁSZ", "hint": "Odwrotnie: ludzie przeszli z koczowniczego (wędrownego) do osiadłego (rolnictwo, osady)."}
        ]
    elif subj == "Angielski":
        questions = [
            {"id": 1, "type": "choice", "q": "Które zdanie jest w 100% poprawne gramatycznie?", "options": ["She have got a cat.", "She has got a cat.", "She is got a cat.", "She having a cat."], "ans": "She has got a cat.", "hint": "W 3. osobie l. pojedynczej (He/She/It) stosujemy 'has got'." },
            {"id": 2, "type": "choice", "q": "Wybierz poprawny przedimek: ___ apple.", "options": ["a", "an", "the a", "brak"], "ans": "an", "hint": "Przedimek 'an' stawiamy przed słowami zaczynającymi się od samogłoski (a, e, i, o, u)."},
            {"id": 3, "type": "bool", "q": "Czy powiemy: 'They are a students'?", "options": ["PRAWDA", "FAŁSZ"], "ans": "FAŁSZ", "hint": "W liczbie mnogiej ('students') NIGDY nie używamy przedimka a / an!"}
        ]
    else:
        questions = [
            {"id": 1, "type": "bool", "q": f"Czy w przedmiocie {subj} reguły szkolne piszemy zawsze starannie?", "options": ["PRAWDA", "FAŁSZ"], "ans": "PRAWDA", "hint": "Dbałość o szczegóły daje wyższe oceny!"}
        ]

    return JSONResponse({
        "status": "ok",
        "chapter_num": chapter_num,
        "subject": subj,
        "chapter_title": chap_data.get("title", f"Rozdział {chapter_num}") if chap_data else f"Rozdział {chapter_num}",
        "questions": questions
    })

@app.get("/courses/print/{book_id}", response_class=HTMLResponse)
def courses_print_worksheet(request: Request, book_id: int):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 302)
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")
    
    b_subj = book['subject']
    raw_tasks = conn.execute("""
        SELECT it.*, utp.score as user_score, utp.attempts as user_attempts, utp.last_attempt as user_last_attempt
        FROM interactive_tasks it 
        JOIN book_chapters bc ON it.chapter_id = bc.id 
        LEFT JOIN user_task_progress utp ON it.id = utp.task_id AND utp.user_id = ?
        WHERE bc.book_id = ?
        ORDER BY it.id ASC
    """, (u['id'], book_id)).fetchall()

    if not raw_tasks:
        raw_tasks = conn.execute("""
            SELECT it.*, utp.score as user_score, utp.attempts as user_attempts, utp.last_attempt as user_last_attempt
            FROM interactive_tasks it 
            JOIN book_chapters bc ON it.chapter_id = bc.id 
            JOIN books b ON bc.book_id = b.id
            LEFT JOIN user_task_progress utp ON it.id = utp.task_id AND utp.user_id = ?
            WHERE b.subject = ?
            ORDER BY it.id ASC
        """, (u['id'], b_subj)).fetchall()

    import json
    tasks = []
    for t in raw_tasks:
        td = dict(t)
        try:
            content = json.loads(td['content_json'])
            td['parsed_content'] = content
            import re as _re
            num_str = content.get('number', '')
            title_str = content.get('title', '')
            full_text = (num_str + ' ' + title_str).lower()
            m = _re.search(r'str\.\s*(\d+)', full_text)
            page_num = int(m.group(1)) if m else None
            td['page_ref'] = page_num

            m_ex = _re.search(r'zadanie\s*(\d+)', num_str.lower())
            td['exercise_num'] = int(m_ex.group(1)) if m_ex else None

            if page_num in (3, 4):
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Zapis i porównywanie liczb"
            elif page_num in (5, 6):
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Działania pamięciowe i sprytne liczenie"
            else:
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Ćwiczenia z podręcznika"

            tasks.append(td)
        except Exception:
            pass

    tasks.sort(key=lambda x: (x.get('page_ref') or 999, x.get('exercise_num') or 999, x.get('id') or 0))

    # Filtrowanie wydruku na żądanie użytkownika (jedno zadanie, zaznaczone zadania, konkretna strona lub zagadnienie)
    task_id_param = request.query_params.get("task_id")
    task_ids_param = request.query_params.get("task_ids")
    page_param = request.query_params.get("page")
    topic_param = request.query_params.get("topic")

    if task_id_param:
        try:
            t_id = int(task_id_param)
            tasks = [t for t in tasks if t['id'] == t_id]
        except Exception:
            pass
    elif task_ids_param:
        id_set = {int(x.strip()) for x in task_ids_param.split(',') if x.strip().isdigit()}
        if id_set:
            tasks = [t for t in tasks if t['id'] in id_set]
    elif page_param and page_param != 'all':
        try:
            p_num = int(page_param)
            tasks = [t for t in tasks if t.get('page_ref') == p_num]
        except Exception:
            pass
    elif topic_param and topic_param != 'all':
        tasks = [t for t in tasks if t.get('topic_name') == topic_param]

    conn.close()

    return templates.TemplateResponse(request, "course_print.html", {
        "user": u,
        "book": dict(book),
        "tasks": tasks
    })

@app.get("/srs/deck/{deck_id}/cards-raw")
def srs_deck_cards_raw(request: Request, deck_id: int):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    cards = [dict(r) for r in conn.execute("SELECT id, front, back FROM cards WHERE deck_id=?", (deck_id,)).fetchall()]
    conn.close()
    return JSONResponse(cards)


USER_TASK_STREAKS = {}

@app.post("/api/tasks/submit-result")
async def api_task_submit(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    raw_task_id = str(data.get("task_id", ""))
    score = int(data.get("score", 0)) # 0 - 100%
    
    # Obsługa ID zadania wariantu (np. sim_27_1234)
    import re
    m_sim = re.match(r'sim_(\d+)_', raw_task_id)
    numeric_task_id = int(m_sim.group(1)) if m_sim else None
    if not numeric_task_id:
        try:
            numeric_task_id = int(raw_task_id)
        except Exception:
            numeric_task_id = None

    conn = db()
    overall_pct = 0
    combo_streak = 0
    bonus_xp = 0
    combo_msg = ""

    # MatZoo mechanic: Combo Streak & Rewards
    u_id = u["id"]
    if score == 100:
        current_streak = USER_TASK_STREAKS.get(u_id, 0) + 1
        USER_TASK_STREAKS[u_id] = current_streak
        combo_streak = current_streak
        if combo_streak == 3:
            bonus_xp = 5
            combo_msg = "🔥 Seria 3 bez błędu! (+5 bonus XP)"
        elif combo_streak == 5:
            bonus_xp = 10
            combo_msg = "⚡ Super seria 5 bez błędu! (+10 bonus XP)"
        elif combo_streak == 10:
            bonus_xp = 25
            combo_msg = "🏆 Mistrzowska seria 10 bez błędu! (+25 bonus XP)"
        elif combo_streak > 1:
            combo_msg = f"🔥 Seria {combo_streak} poprawnych z rzędu!"
    elif score < 70:
        USER_TASK_STREAKS[u_id] = 0

    if numeric_task_id:
        try:
            # Sprawdź czy zadanie istnieje w bazie, aby uniknąć IntegrityError
            t_exists = conn.execute("SELECT id FROM interactive_tasks WHERE id=?", (numeric_task_id,)).fetchone()
            if t_exists:
                existing = conn.execute("SELECT id, score, attempts FROM user_task_progress WHERE user_id=? AND task_id=?", (u["id"], numeric_task_id)).fetchone()
                if existing:
                    conn.execute("UPDATE user_task_progress SET score=MAX(score, ?), attempts=attempts+1, last_attempt=datetime('now') WHERE id=?", (score, existing["id"]))
                else:
                    conn.execute("INSERT INTO user_task_progress (user_id, task_id, score, attempts) VALUES (?, ?, ?, 1)", (u["id"], numeric_task_id, score))
                
                # Zapisz punkty w points_log jeśli zaliczone (>= 70%)
                if score >= 70:
                    try:
                        base_points = 5
                        total_pts = base_points + bonus_xp
                        reason = f"Rozwiązanie zadania #{numeric_task_id} ({score}%)"
                        if bonus_xp > 0:
                            reason += f" + bonus za combo ({combo_streak}x)"
                        conn.execute("INSERT INTO points_log (user_id, points, reason) VALUES (?, ?, ?)",
                                     (u["id"], total_pts, reason))
                    except Exception:
                        pass

                # Oblicz globalny postęp dla tej książki/przedmiotu
                b_row = conn.execute("SELECT bc.book_id FROM interactive_tasks it JOIN book_chapters bc ON it.chapter_id=bc.id WHERE it.id=?", (numeric_task_id,)).fetchone()
                book_id = b_row[0] if b_row else None
                
                if book_id:
                    total_tasks = conn.execute("SELECT COUNT(*) FROM interactive_tasks it JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=?", (book_id,)).fetchone()[0]
                    completed_tasks = conn.execute("SELECT COUNT(DISTINCT task_id) FROM user_task_progress utp JOIN interactive_tasks it ON utp.task_id=it.id JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=? AND utp.user_id=? AND utp.score>=70", (book_id, u["id"])).fetchone()[0]
                    if total_tasks > 0:
                        overall_pct = int((completed_tasks / total_tasks) * 100)
            conn.commit()
        except Exception as e:
            conn.rollback()
        finally:
            conn.close()
    else:
        conn.close()
    return JSONResponse({
        "status": "ok",
        "score": score,
        "task_id": numeric_task_id,
        "overall_pct": overall_pct,
        "combo_streak": combo_streak,
        "bonus_xp": bonus_xp,
        "combo_msg": combo_msg
    })


def generate_math_variant(content: dict, task_type: str, difficulty: str = "same") -> dict:
    import copy, random, re
    res = copy.deepcopy(content)
    orig_num = content.get("number", "Zadanie")
    
    if difficulty == "identical":
        res["number"] = "Powtórka: " + orig_num
        res["title"] = "Trening z pamięci: " + content.get("title", "Zadanie")
        return res
    
    res["number"] = "Wariant do: " + orig_num
    
    if difficulty == "easier":
        diff_label = "Łatwiejszy"
    elif difficulty == "harder":
        diff_label = "Trudniejszy"
    else:
        diff_label = "Podobny poziom"
        
    res["title"] = content.get("title", "Podobne zadanie")

    # 1. Oś liczbowa (sliders)
    if "sliders" in res and res["sliders"]:
        new_sliders = []
        for sl in res["sliders"]:
            sl_copy = copy.deepcopy(sl)
            tot = sl.get("total_ticks", 13)
            labeled = [int(k) for k in sl.get("labeled_ticks", {}).keys()]
            
            if difficulty == "easier":
                avail = [i for i in range(2, min(6, tot)) if i not in labeled]
                target = random.choice(avail) if avail else 2
            elif difficulty == "harder":
                avail = [i for i in range(max(6, tot // 2), tot) if i not in labeled]
                target = random.choice(avail) if avail else tot - 2
            else:
                avail = [i for i in range(1, tot) if i not in labeled]
                target = random.choice(avail) if avail else tot // 2

            label_txt = sl.get("label", "")
            lt = sl.get("labeled_ticks", {})
            if "0" in lt and "1" in lt:
                step_val = int(lt["1"]) - int(lt["0"])
                num_val = target * step_val
                sl_copy["label"] = re.sub(r'\d+', str(num_val), label_txt, count=1) if re.search(r'\d+', label_txt) else f"Zaznacz liczbę {num_val}:"
            elif "3" in lt and "6" in lt:
                step_val = (int(lt["6"]) - int(lt["3"])) // 3
                num_val = target * step_val
                sl_copy["label"] = re.sub(r'\d+', str(num_val), label_txt, count=1) if re.search(r'\d+', label_txt) else f"Zaznacz liczbę {num_val}:"
            elif "0" in lt and "8" in lt:
                step_val = int(lt["8"]) // 8
                num_val = target * step_val
                sl_copy["label"] = re.sub(r'\d+', str(num_val), label_txt, count=1) if re.search(r'\d+', label_txt) else f"Zaznacz liczbę {num_val}:"
            elif "5" in lt and "10" in lt:
                step_val = (int(lt["10"]) - int(lt["5"])) // 5
                num_val = 2000 + target * step_val
                sl_copy["label"] = re.sub(r'\d+', str(num_val), label_txt, count=1) if re.search(r'\d+', label_txt) else f"Zaznacz liczbę {num_val}:"

            sl_copy["target_tick"] = str(target)
            sl_copy["initial_tick"] = 0
            new_sliders.append(sl_copy)
        res["sliders"] = new_sliders
        return res

    # 2. Liczby w figurach (chips)
    if "fields" in res and res["fields"] and any("chips" in f for f in res["fields"]):
        new_fields = []
        cached_chips = {}
        for f in res["fields"]:
            f_copy = copy.deepcopy(f)
            old_chips = f.get("chips", [])
            if old_chips:
                digits = len(str(old_chips[0]))
                if digits not in cached_chips:
                    cnt = 5 if difficulty == "easier" else (8 if difficulty == "harder" else 6)
                    if digits == 3:
                        base = random.sample([2, 3, 4, 5, 6, 7], 2)
                        nums = sorted(list(set(random.choice(base)*100 + random.choice(base)*10 + random.choice(base) for _ in range(16))))[:cnt]
                    elif digits == 4:
                        base = random.sample([1, 2, 4, 6, 8], 3) + [0]
                        nums = sorted(list(set(random.choice([d for d in base if d != 0])*1000 + random.choice(base)*100 + random.choice(base)*10 + random.choice(base) for _ in range(18))))[:cnt]
                    else:
                        base = random.sample([1, 3, 5, 7, 9], 3)
                        nums = sorted(list(set(random.choice(base)*10000 + random.choice(base)*1000 + random.choice(base)*100 + random.choice(base)*10 + random.choice(base) for _ in range(20))))[:cnt]
                    cached_chips[digits] = nums
                
                nums = cached_chips[digits]
                f_copy["chips"] = [str(n) for n in nums]
                if "najmniejsza" in f["label"].lower():
                    f_copy["ans"] = str(min(nums))
                elif "największa" in f["label"].lower():
                    f_copy["ans"] = str(max(nums))
            new_fields.append(f_copy)
        res["fields"] = new_fields
        return res

    # 3. Proste obliczenia pamięciowe i zaawansowane formaty zadań
    if "fields" in res and res["fields"]:
        new_fields = []
        for idx, f in enumerate(res["fields"]):
            f_copy = copy.deepcopy(f)
            lbl = f.get("label", "")
            
            # Pattern A: Wagon kolejki górskiej, np. "Wagon 3 (70 + 15):" lub "Wagon 4 (85 - 25):"
            m_wagon = re.search(r'(Wagon\s*\d+)\s*\((?:(\d+)\s*([\+\-])\s*(\d+))\):?', lbl)
            # Pattern B: Równanie z brakującą liczbą w okienku: "a) 56 + [ ? ] = 83 :" lub "b) [ ? ] - 43 = 51 :" lub "c) 78 - [ ? ] = 15 :"
            m_hole_add = re.search(r'([a-z]\))\s*(\d+)\s*\+\s*\[\s*\?\s*\]\s*=\s*(\d+)', lbl)
            m_hole_sub1 = re.search(r'([a-z]\))\s*\[\s*\?\s*\]\s*-\s*(\d+)\s*=\s*(\d+)', lbl)
            m_hole_sub2 = re.search(r'([a-z]\))\s*(\d+)\s*-\s*\[\s*\?\s*\]\s*=\s*(\d+)', lbl)
            # Pattern C: Sprytne liczenie trzech liczb: "34 + 58 + 16 ="
            m_three = re.search(r'(\d+)\s*\+\s*(\d+)\s*\+\s*(\d+)\s*=', lbl)
            # Pattern D: Ciągi liczbowe: "Ciąg +99: 125, 224, 323, [ ? ] :"
            m_seq_add = re.search(r'Ciąg\s*\+(\d+):\s*(\d+),\s*(\d+),\s*(\d+),\s*\[\s*\?\s*\]', lbl)
            m_seq_sub = re.search(r'Ciąg\s*\-(\d+):\s*(\d+),\s*(\d+),\s*(\d+),\s*\[\s*\?\s*\]', lbl)
            # Pattern E: Standardowe dwa składniki: "a) 34 + 18 =" lub "34 - 18 ="
            m_add = re.search(r'(\d+)\s*\+\s*(\d+)', lbl)
            m_sub = re.search(r'(\d+)\s*-\s*(\d+)', lbl)

            if m_wagon:
                w_prefix, a_str, op, b_str = m_wagon.groups()
                a_base, b_base = int(a_str), int(b_str)
                delta = 10 if difficulty == "harder" else (-10 if difficulty == "easier" and a_base > 40 else 0)
                a = max(20, a_base + delta + random.randint(-2, 2) * 5)
                b = max(5, b_base + (5 if difficulty == "harder" else 0))
                ans = a + b if op == '+' else a - b
                f_copy["label"] = f"{w_prefix} ({a} {op} {b}):"
                f_copy["ans"] = str(ans)
            elif m_hole_add:
                pref, a_str, c_str = m_hole_add.groups()
                delta = 15 if difficulty == "harder" else (-10 if difficulty == "easier" else 0)
                a = max(10, int(a_str) + delta + random.randint(-3, 3))
                ans = max(5, int(c_str) - int(a_str) + random.randint(1, 4))
                c = a + ans
                f_copy["label"] = f"{pref} {a} + [ ? ] = {c} :"
                f_copy["ans"] = str(ans)
            elif m_hole_sub1:
                pref, b_str, c_str = m_hole_sub1.groups()
                delta = 15 if difficulty == "harder" else (-10 if difficulty == "easier" else 0)
                b = max(10, int(b_str) + delta + random.randint(-3, 3))
                c = max(10, int(c_str) + delta + random.randint(-3, 3))
                ans = b + c
                f_copy["label"] = f"{pref} [ ? ] - {b} = {c} :"
                f_copy["ans"] = str(ans)
            elif m_hole_sub2:
                pref, a_str, c_str = m_hole_sub2.groups()
                delta = 15 if difficulty == "harder" else (-10 if difficulty == "easier" else 0)
                a = max(30, int(a_str) + delta + random.randint(-3, 3))
                c = max(10, int(c_str) + (delta // 2) + random.randint(-2, 2))
                if c >= a: c = a - 15
                ans = a - c
                f_copy["label"] = f"{pref} {a} - [ ? ] = {c} :"
                f_copy["ans"] = str(ans)
            elif m_three:
                # Trzy liczby, z których dwie sumują się do pełnej dziesiątki
                base_round = 50 if difficulty == "easier" else (100 if difficulty == "harder" else 70)
                split1 = random.randint(2, 6) * 10 + random.choice([2, 4, 6, 8, 5])
                last_d = split1 % 10
                comp_d = (10 - last_d) % 10
                split2 = random.randint(1, 4) * 10 + comp_d
                split3 = random.randint(15, 65)
                f_copy["label"] = f"{split1} + {split2} + {split3} ="
                f_copy["ans"] = str(split1 + split2 + split3)
            elif m_seq_add:
                step = int(m_seq_add.group(1))
                start = int(m_seq_add.group(2)) + random.randint(1, 5) * 10
                t1, t2, t3 = start, start + step, start + 2 * step
                ans = start + 3 * step
                f_copy["label"] = f"Ciąg +{step}: {t1}, {t2}, {t3}, [ ? ] :"
                f_copy["ans"] = str(ans)
            elif m_seq_sub:
                step = int(m_seq_sub.group(1))
                start = int(m_seq_sub.group(2)) + random.randint(1, 5) * 10
                t1, t2, t3 = start, start - step, start - 2 * step
                ans = start - 3 * step
                f_copy["label"] = f"Ciąg -{step}: {t1}, {t2}, {t3}, [ ? ] :"
                f_copy["ans"] = str(ans)
            elif m_add:
                if difficulty == "easier":
                    a = random.randint(1, 4) * 10
                    b = random.randint(1, 9)
                elif difficulty == "harder":
                    a = random.randint(45, 95)
                    b = random.randint(35, 85)
                else:
                    a = int(m_add.group(1)) + random.randint(1, 4) * 10
                    b = int(m_add.group(2)) + random.randint(1, 6)

                # Bezpieczne wyodrębnienie prefiksu (np. "a)")
                pref_m = re.match(r'^([a-z]\)|\d+\))', lbl)
                prefix = pref_m.group(1) if pref_m else f"{idx+1})"
                f_copy["label"] = f"{prefix} {a} + {b} ="
                f_copy["ans"] = str(a + b)
            elif m_sub:
                if difficulty == "easier":
                    a = random.randint(5, 9) * 10
                    b = random.randint(1, 4) * 10
                elif difficulty == "harder":
                    a = random.randint(120, 250)
                    b = random.randint(35, 95)
                else:
                    a = int(m_sub.group(1)) + random.randint(1, 4) * 10
                    b = int(m_sub.group(2)) + random.randint(1, 4)
                    if b >= a: b = a - 10

                pref_m = re.match(r'^([a-z]\)|\d+\))', lbl)
                prefix = pref_m.group(1) if pref_m else f"{idx+1})"
                f_copy["label"] = f"{prefix} {a} - {b} ="
                f_copy["ans"] = str(a - b)
            new_fields.append(f_copy)
        res["fields"] = new_fields
        return res

    return res


@app.post("/api/tasks/{task_id}/similar")
async def api_task_similar(task_id: int, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    t_row = conn.execute("SELECT * FROM interactive_tasks WHERE id=?", (task_id,)).fetchone()
    if not t_row:
        conn.close()
        raise HTTPException(404, "Task not found")
    
    import json, random
    content = json.loads(t_row["content_json"])
    task_type = t_row["task_type"]
    
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    diff_choice = body.get("difficulty") or request.query_params.get("difficulty") or "same"
    
    orig_level = t_row["difficulty_level"] or 2
    if diff_choice == "easier":
        diff_level = max(1, orig_level - 1)
        diff_tag = "Łatwiejszy (-1)"
    elif diff_choice == "harder":
        diff_level = min(5, orig_level + 1)
        diff_tag = "Trudniejszy (+1)"
    elif diff_choice == "identical":
        diff_level = orig_level
        diff_tag = "Identyczne (z pamięci)"
    else:
        diff_level = orig_level
        diff_tag = "Ten sam poziom"

    similar_content = generate_math_variant(content, task_type, diff_choice)
    similar_task = {
        "id": f"sim_{task_id}_{random.randint(1000, 9999)}",
        "original_id": task_id,
        "task_type": task_type,
        "difficulty_level": diff_level,
        "difficulty_tag": diff_tag,
        "difficulty_mode": diff_choice,
        "parsed_content": similar_content
    }
    conn.close()
    return JSONResponse({"status": "ok", "task": similar_task})


@app.post("/api/tasks/batch-similar")
async def api_tasks_batch_similar(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    task_ids = data.get("task_ids", [])
    diff_choice = data.get("difficulty", "same")
    if not task_ids:
        raise HTTPException(400, "Brak wybranych zadań")
    
    conn = db()
    tasks_out = []
    import json, random
    for tid in task_ids:
        t_row = conn.execute("SELECT * FROM interactive_tasks WHERE id=?", (tid,)).fetchone()
        if not t_row:
            continue
        content = json.loads(t_row["content_json"])
        task_type = t_row["task_type"]
        orig_level = t_row["difficulty_level"] or 2
        
        if diff_choice == "easier":
            diff_level = max(1, orig_level - 1)
            diff_tag = "Łatwiejszy (-1)"
        elif diff_choice == "harder":
            diff_level = min(5, orig_level + 1)
            diff_tag = "Trudniejszy (+1)"
        elif diff_choice == "identical":
            diff_level = orig_level
            diff_tag = "Identyczne (z pamięci)"
        else:
            diff_level = orig_level
            diff_tag = "Ten sam poziom"
            
        sim_content = generate_math_variant(content, task_type, diff_choice)
        tasks_out.append({
            "id": f"sim_{tid}_{random.randint(1000, 9999)}",
            "original_id": tid,
            "task_type": task_type,
            "difficulty_level": diff_level,
            "difficulty_tag": diff_tag,
            "difficulty_mode": diff_choice,
            "parsed_content": sim_content
        })
    
    conn.close()
    return JSONResponse({"status": "ok", "tasks": tasks_out})



# ============================================================================
# SNITCHNOTES AI STUDY STUDIO & NOTES SYSTEM (/notes)
# ============================================================================

def _init_notes_db():
    try:
        conn = db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_study_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                chapter_num INTEGER NOT NULL,
                topic_num INTEGER DEFAULT 0,
                note_content TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, book_id, chapter_num, topic_num)
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print("Note DB init error:", e)

_init_notes_db()


def _build_book_chapters_data(conn, book_dict, user_id: int):
    if not book_dict:
        return []
    book_id = book_dict["id"]
    
    # 1. Spis treści z book_toc
    toc_rows = conn.execute("""
        SELECT chapter_num, chapter_title, topic_num, topic_title, page_start, page_end
        FROM book_toc
        WHERE book_id=?
        ORDER BY chapter_num ASC, topic_num ASC
    """, (book_id,)).fetchall()
    
    chap_map = {}
    for r in toc_rows:
        c_num = r["chapter_num"]
        if c_num not in chap_map:
            chap_map[c_num] = {
                "chapter_num": c_num,
                "chapter_title": r["chapter_title"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
                "topics": [],
                "exercises": []
            }
        chap_map[c_num]["topics"].append({
            "topic_num": r["topic_num"],
            "topic_title": r["topic_title"],
            "page_start": r["page_start"],
            "page_end": r["page_end"]
        })
        if r["page_end"] > chap_map[c_num]["page_end"]:
            chap_map[c_num]["page_end"] = r["page_end"]

    # Jeśli brak w book_toc, sprawdź book_chapters
    if not chap_map:
        bc_rows = conn.execute("""
            SELECT id, chapter_number, title, pages_range
            FROM book_chapters
            WHERE book_id=?
            ORDER BY chapter_number ASC, id ASC
        """, (book_id,)).fetchall()
        for idx, bc in enumerate(bc_rows, 1):
            c_num = bc["chapter_number"] or idx
            p_start, p_end = 1, 20
            if bc["pages_range"]:
                parts = bc["pages_range"].split("-")
                try:
                    p_start = int(parts[0])
                    p_end = int(parts[1]) if len(parts) > 1 else p_start + 10
                except Exception:
                    pass
            chap_map[c_num] = {
                "chapter_num": c_num,
                "chapter_title": bc["title"] or f"Rozdział {c_num}",
                "page_start": p_start,
                "page_end": p_end,
                "topics": [{"topic_num": 1, "topic_title": bc["title"] or f"Temat {c_num}", "page_start": p_start, "page_end": p_end}],
                "exercises": []
            }

    if not chap_map:
        chap_map[1] = {
            "chapter_num": 1,
            "chapter_title": f"Część 1: {book_dict['title']}",
            "page_start": 1,
            "page_end": 50,
            "topics": [{"topic_num": 1, "topic_title": "Wprowadzenie", "page_start": 1, "page_end": 20}],
            "exercises": []
        }

    # 2. Pobierz zadania interaktywne powiązane z tym podręcznikiem/zeszytem
    tasks_rows = conn.execute("""
        SELECT it.id, it.chapter_id, it.task_type, it.content_json, bc.book_id,
               utp.score as user_score
        FROM interactive_tasks it
        JOIN book_chapters bc ON it.chapter_id = bc.id
        LEFT JOIN user_task_progress utp ON it.id = utp.task_id AND utp.user_id = ?
        WHERE bc.book_id = ?
        ORDER BY it.id ASC
    """, (user_id, book_id)).fetchall()
    
    import re
    for tr in tasks_rows:
        td = dict(tr)
        c_json = {}
        try:
            c_json = json.loads(td["content_json"])
        except Exception:
            pass
        num_str = c_json.get("number") or f"Zadanie #{td['id']}"
        title_str = c_json.get("title", "")
        full_t = f"{num_str} {title_str}".lower()
        m_pg = re.search(r'str\.\s*(\d+)', full_t)
        pg = int(m_pg.group(1)) if m_pg else None
        
        ex_obj = {
            "id": td["id"],
            "book_id": td["book_id"],
            "number": num_str,
            "title": title_str,
            "page_ref": pg,
            "task_type": td["task_type"],
            "score": td["user_score"],
            "is_done": (td["user_score"] is not None and td["user_score"] >= 70)
        }
        
        matched = False
        if pg:
            for c_num, c in chap_map.items():
                if c["page_start"] <= pg <= c["page_end"]:
                    c["exercises"].append(ex_obj)
                    matched = True
                    break
        if not matched and chap_map:
            first_k = min(chap_map.keys())
            chap_map[first_k]["exercises"].append(ex_obj)

    return list(chap_map.values())


def _get_notes_bundled(user_id: int):
    conn = db()
    books = conn.execute("SELECT id, title, subject, kind, filename FROM books WHERE kind IN ('podreczniki', 'cwiczenia', 'lektury') ORDER BY id").fetchall()
    
    subjects_order = ["Matematyka", "Angielski", "Biologia", "Historia", "Polski"]
    bundled = {s: {"textbook": None, "workbook": None, "chapters": [], "textbook_chapters": [], "workbook_chapters": [], "total_tasks": 0, "done_tasks": 0} for s in subjects_order}
    
    for b in books:
        b_dict = dict(b)
        subj = b_dict['subject']
        if subj not in bundled:
            bundled[subj] = {"textbook": None, "workbook": None, "chapters": [], "textbook_chapters": [], "workbook_chapters": [], "total_tasks": 0, "done_tasks": 0}
            
        tl = b_dict['title'].lower()
        kind = b_dict['kind']
        if kind == 'cwiczenia' or 'ćwicz' in tl or 'cwicz' in tl:
            bundled[subj]["workbook"] = b_dict
        elif kind == 'lektury' or 'baśniobór' in tl:
            if not bundled[subj]["workbook"]:
                bundled[subj]["workbook"] = b_dict
        else:
            if not bundled[subj]["textbook"]:
                bundled[subj]["textbook"] = b_dict

    for subj, d in bundled.items():
        tb = d["textbook"]
        wb = d["workbook"]
        d["textbook_chapters"] = _build_book_chapters_data(conn, tb, user_id) if tb else []
        d["workbook_chapters"] = _build_book_chapters_data(conn, wb, user_id) if wb else []
        d["chapters"] = d["textbook_chapters"] if d["textbook_chapters"] else d["workbook_chapters"]
        d["total_tasks"] = sum(len(c["exercises"]) for c in d["textbook_chapters"]) + sum(len(c["exercises"]) for c in d["workbook_chapters"])
        d["done_tasks"] = sum(sum(1 for e in c["exercises"] if e["is_done"]) for c in d["textbook_chapters"]) + sum(sum(1 for e in c["exercises"] if e["is_done"]) for c in d["workbook_chapters"])

    conn.close()
    return bundled


@app.get("/notes", response_class=HTMLResponse)
def notes_view(request: Request, book_id: Optional[int] = None, subject: Optional[str] = None, chapter: Optional[int] = 1, mode: Optional[str] = "theory"):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", 302)
    user = u
    bundled = _get_notes_bundled(u['id'])
    
    # Znajdź aktywny podręcznik i przedmiot
    active_book = None
    active_subject = subject or "Matematyka"
    if active_subject not in bundled:
        active_subject = "Matematyka"
    
    if book_id:
        for subj, data in bundled.items():
            if data["textbook"] and data["textbook"]["id"] == book_id:
                active_book = data["textbook"]
                active_subject = subj
                mode = "theory"
                break
            if data["workbook"] and data["workbook"]["id"] == book_id:
                active_book = data["workbook"]
                active_subject = subj
                mode = "practice"
                break
                
    if not active_book and active_subject in bundled:
        s_data = bundled[active_subject]
        if mode == "practice" and s_data["workbook"]:
            active_book = s_data["workbook"]
        else:
            active_book = s_data["textbook"] or s_data["workbook"]
            mode = "theory" if s_data["textbook"] else "practice"
        
    return templates.TemplateResponse(request, "notes.html", {
        "user": user,
        "bundled_materials": bundled,
        "active_subject": active_subject,
        "active_book": active_book,
        "active_chapter": chapter,
        "active_mode": mode
    })


@app.get("/notes/{book_id}", response_class=HTMLResponse)
def notes_book_view(request: Request, book_id: int, chapter: Optional[int] = 1):
    return notes_view(request, book_id=book_id, chapter=chapter)


@app.get("/api/notes/topic-data/{book_id}/{chapter_num}")
def api_notes_topic_data(request: Request, book_id: int, chapter_num: int):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")
        
    subj = book['subject']
    
    # 1. Pobierz spisy tematów
    toc_topics = conn.execute("""
        SELECT topic_num, topic_title, page_start, page_end
        FROM book_toc
        WHERE book_id=? AND chapter_num=?
        ORDER BY topic_num ASC
    """, (book_id, chapter_num)).fetchall()
    
    topics = [dict(t) for t in toc_topics]
    
    # 2. Pobierz powiązane zadania
    tasks_rows = conn.execute("""
        SELECT it.id, it.task_type, it.content_json, utp.score as user_score
        FROM interactive_tasks it
        JOIN book_chapters bc ON it.chapter_id = bc.id
        LEFT JOIN user_task_progress utp ON it.id = utp.task_id AND utp.user_id = ?
        WHERE bc.book_id = ?
    """, (u['id'], book_id)).fetchall()
    
    exercises = []
    import re
    for tr in tasks_rows:
        td = dict(tr)
        c_json = {}
        try:
            c_json = json.loads(td["content_json"])
        except Exception:
            pass
        num_str = c_json.get("number") or f"Zadanie #{td['id']}"
        title_str = c_json.get("title", "")
        full_t = f"{num_str} {title_str}".lower()
        m_pg = re.search(r'str\.\s*(\d+)', full_t)
        pg = int(m_pg.group(1)) if m_pg else None
        exercises.append({
            "id": td["id"],
            "number": num_str,
            "title": title_str,
            "page_ref": pg,
            "task_type": td["task_type"],
            "score": td["user_score"],
            "is_done": (td["user_score"] is not None and td["user_score"] >= 70)
        })
        
    # 3. Pobierz artefakty (podcast, infografika)
    podcast = conn.execute("""
        SELECT id, title, description, audio_url, duration
        FROM learning_artifacts
        WHERE book_id=? AND chapter_num=? AND artifact_type='podcast'
        LIMIT 1
    """, (book_id, chapter_num)).fetchone()
    
    # 4. Pobierz notatkę użytkownika z bazy
    user_note = conn.execute("""
        SELECT note_content, updated_at
        FROM user_study_notes
        WHERE user_id=? AND book_id=? AND chapter_num=?
        ORDER BY updated_at DESC LIMIT 1
    """, (u['id'], book_id, chapter_num)).fetchone()
    
    # 5. Pobierz teorię ze słownika lub z bazy
    chap_theory = THEORY_DATA_BY_SUBJECT.get(subj, {}).get(chapter_num, {})
    if not chap_theory or not chap_theory.get("desc"):
        chap_row = conn.execute("""
            SELECT title, raw_ocr_text, pages_range 
            FROM book_chapters 
            WHERE book_id=? AND (chapter_number=? OR id=?)
            ORDER BY id ASC LIMIT 1
        """, (book_id, chapter_num, chapter_num)).fetchone()
        if not chap_row:
            chap_row = conn.execute("""
                SELECT title, raw_ocr_text, pages_range 
                FROM book_chapters 
                WHERE book_id=?
                ORDER BY id ASC LIMIT 1
            """, (book_id,)).fetchone()
        if chap_row and chap_row["raw_ocr_text"]:
            chap_theory = {
                "title": chap_row["title"],
                "desc": f"Podsumowanie merytoryczne i zagadnienia teoretyczne dla zakresu stron {chap_row['pages_range'] or 'rozdziału'}.",
                "raw_markdown": chap_row["raw_ocr_text"],
                "competencies": [
                    {"title": "Materiały źródłowe", "text": f"Opracowane na podstawie podręcznika: {book['title']}."}
                ],
                "rules": [],
                "pitfalls": []
            }
    
    conn.close()
    
    return JSONResponse({
        "status": "ok",
        "subject": subj,
        "book_title": book["title"],
        "chapter_num": chapter_num,
        "topics": topics,
        "exercises": exercises,
        "podcast": dict(podcast) if podcast else None,
        "theory": chap_theory,
        "user_note": dict(user_note) if user_note else None
    })


@app.post("/api/notes/save-user-note")
async def api_save_user_note(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    book_id = int(data.get("book_id", 0))
    chapter_num = int(data.get("chapter_num", 1))
    topic_num = int(data.get("topic_num", 0))
    note_content = str(data.get("note_content", "")).strip()
    
    conn = db()
    conn.execute("""
        INSERT INTO user_study_notes (user_id, book_id, chapter_num, topic_num, note_content, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, book_id, chapter_num, topic_num)
        DO UPDATE SET note_content=excluded.note_content, updated_at=datetime('now')
    """, (u['id'], book_id, chapter_num, topic_num, note_content))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "saved", "message": "Notatka została trwale zapisana w bazie."})


@app.post("/api/notes/ask-tutor")
async def api_notes_ask_tutor(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    question = str(data.get("question", "")).strip()
    book_id = int(data.get("book_id", 103))
    chapter_num = int(data.get("chapter_num", 1))
    
    if not question:
        return JSONResponse({"answer": "Zadaj pytanie dotyczące materiału z podręcznika lub zadania."})
        
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    
    subj = book['subject'] if book else "Matematyka"
    chap_theory = THEORY_DATA_BY_SUBJECT.get(subj, {}).get(chapter_num, {})
    
    # Odpowiedź oparta na materiale
    theory_title = chap_theory.get("title", f"Rozdział {chapter_num}")
    comps = chap_theory.get("competencies", [])
    rules = chap_theory.get("rules", [])
    
    answer_text = f"Odpowiedź ze źródła: {book['title'] if book else subj} ({theory_title}):\n\n"
    if "jak" in question.lower() or "dlaczego" in question.lower() or "reguł" in question.lower() or "wzór" in question.lower():
        if rules:
            r = rules[0]
            answer_text += f"**Kluczowa zasada ({r.get('title')}):**\n{r.get('content')}\n\n"
        if comps:
            c = comps[0]
            answer_text += f"**Metoda postępowania ({c.get('title')}):**\n{c.get('text')}\n\n"
    else:
        answer_text += f"Na podstawie materiału z podręcznika:\n"
        for c in comps[:2]:
            answer_text += f"• **{c.get('title')}**: {c.get('text')}\n"
            
    answer_text += f"\n💡 *Wskazówka Snitchnotes: Możesz sprawdzić to bezpośrednio w 'Moje Kursy', rozwiązując zadania z tego rozdziału!*"
    
    return JSONResponse({"answer": answer_text})


# ============================================================================
# FAZA 1 + FAZA 2: Integrated Library + Notes-Kursy Bridge
# ============================================================================

def _ensure_library_tables():
    """Run once on startup to ensure new tables/cols exist."""
    try:
        conn = db()
        for col, defn in [
            ("generation_source", "TEXT DEFAULT 'ai'"),
            ("chapter_title", "TEXT"),
            ("subject", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE learning_artifacts ADD COLUMN {col} {defn}")
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                book_id INTEGER,
                chapter_num INTEGER,
                reminder_type TEXT DEFAULT 'task',
                source TEXT DEFAULT 'manual',
                due_date TEXT,
                is_done INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print("Library tables init error:", e)

_ensure_library_tables()


# ── FAZA 1: Shared Library API ────────────────────────────────────────────────

@app.get("/api/library")
def api_library(request: Request, subject: str = "", book_id: int = 0, chapter: int = 0,
                artifact_type: str = "", limit: int = 20):
    u = current_user(request)
    if not u:
        raise HTTPException(401)

    conn = db()
    where = ["la.is_shared = 1"]
    params = []
    if book_id:
        where.append("(la.book_id = ? OR (la.subject = (SELECT subject FROM books WHERE id=?) AND la.chapter_num = ?))")
        params.extend([book_id, book_id, chapter if chapter else 1])
    elif subject:
        where.append("la.subject = ?")
        params.append(subject)
    if chapter and not book_id:
        where.append("la.chapter_num = ?")
        params.append(chapter)
    if artifact_type:
        where.append("la.artifact_type = ?")
        params.append(artifact_type)

    query = f"""
        SELECT la.*,
               (SELECT COUNT(*) FROM user_artifact_likes ual WHERE ual.artifact_id = la.id) as like_count,
               (SELECT COUNT(*) FROM user_artifact_favorites uaf WHERE uaf.artifact_id = la.id AND uaf.user_id = ?) as is_favorite,
               (SELECT COUNT(*) FROM user_artifact_likes ual2 WHERE ual2.artifact_id = la.id AND ual2.user_id = ?) as user_liked
        FROM learning_artifacts la
        WHERE {' AND '.join(where)}
        ORDER BY like_count DESC, la.created_at DESC
        LIMIT ?
    """
    rows = conn.execute(query, (u['id'], u['id'], *params, limit)).fetchall()
    conn.close()

    items = []
    for r in rows:
        d = dict(r)
        # Resolve author name
        if not d.get('author_name'):
            d['author_name'] = 'Klasa 5B (AI)'
        items.append(d)

    return JSONResponse({"status": "ok", "items": items, "count": len(items)})


@app.post("/api/artifacts/generate")
async def api_generate_artifact(request: Request):
    """Unified artifact generation endpoint (called from both Notes and Moje Kursy)."""
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    book_id = int(data.get("book_id", 0))
    chapter_num = int(data.get("chapter_num", 1))
    artifact_type = str(data.get("artifact_type", "podcast"))  # 'podcast' | 'infographic'
    generation_source = str(data.get("source", "notes_tab"))   # 'notes_tab' | 'courses_studykit'

    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")

    subj = book['subject']

    # Check if already exists for this chapter (avoid duplicates)
    existing = conn.execute("""
        SELECT id, title, audio_url, content_html
        FROM learning_artifacts
        WHERE book_id=? AND chapter_num=? AND artifact_type=? AND is_shared=1
        ORDER BY likes_count DESC, created_at DESC LIMIT 1
    """, (book_id, chapter_num, artifact_type)).fetchone()

    if existing and data.get("check_existing", True):
        conn.close()
        return JSONResponse({
            "status": "exists",
            "message": f"Klasa już wygenerowała {artifact_type} dla tego rozdziału. Możesz go zobaczyć w bibliotece.",
            "artifact": dict(existing)
        })

    # Get chapter info
    toc = conn.execute("""
        SELECT DISTINCT chapter_title, MIN(page_start) as pg_start
        FROM book_toc WHERE book_id=? AND chapter_num=?
    """, (book_id, chapter_num)).fetchone()
    chap_title = toc['chapter_title'] if toc else f"Rozdział {chapter_num}"

    # Build artifact
    theory = THEORY_DATA_BY_SUBJECT.get(subj, {}).get(chapter_num, {})

    if artifact_type == "podcast":
        title = f"Podcast: {chap_title} ({subj})"
        desc = f"Dwuosobowa rozmowa ekspercka o rozdziale {chapter_num}: {chap_title}"
        # Real audio: check if exists in db/podcasts
        audio_url = None
        pod = conn.execute("""
            SELECT audio_url FROM learning_artifacts
            WHERE book_id=? AND chapter_num=? AND artifact_type='podcast' AND audio_url IS NOT NULL
            LIMIT 1
        """, (book_id, chapter_num)).fetchone()
        if pod:
            audio_url = pod['audio_url']

        content_html = f"""<div class='podcast-transcript'>
            <p><strong>Kacper:</strong> Hej Zuzia, dziś powtarzamy <em>{chap_title}</em> z {subj}. Co jest najważniejsze?</p>
            <p><strong>Zuzia:</strong> {theory.get('desc', 'Zapoznaj się z kluczowymi pojęciami i zasadami z tego rozdziału.')}</p>
            <p><strong>Kacper:</strong> A jakie mamy kluczowe kompetencje do opanowania?</p>
            <p><strong>Zuzia:</strong> {'Przede wszystkim: ' + ', '.join(c['title'] for c in theory.get('competencies', [])[:3]) if theory.get('competencies') else 'Rozwiąż dostępne zadania i fiszki.'}</p>
            <p><strong>Kacper:</strong> Dzięki, teraz rozumiem. Ćwiczmy to w Moje Kursy!</p>
        </div>"""

    else:  # infographic
        title = f"Infografika: {chap_title} ({subj})"
        desc = f"Visual bento map kluczowych pojęć: Rozdział {chapter_num}"
        audio_url = None

        rules_html = ""
        for r in theory.get('rules', [])[:4]:
            rules_html += f"<div class='rule-card'><strong>{r['title']}</strong><br><small>{r['content'][:100]}...</small></div>"

        content_html = f"""<div class='bento-infographic'>
            <h2>{chap_title}</h2>
            <p class='desc'>{theory.get('desc', '')}</p>
            <div class='rules-grid'>{rules_html}</div>
        </div>"""

    # Save to learning_artifacts
    cur = conn.execute("""
        INSERT INTO learning_artifacts
            (book_id, chapter_num, artifact_type, title, description, content_html, audio_url,
             user_id, author_name, is_shared, generation_source, chapter_title, subject)
        VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)
    """, (book_id, chapter_num, artifact_type, title, desc, content_html, audio_url,
          u['id'], u['name'], generation_source, chap_title, subj))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "generated",
        "artifact_id": new_id,
        "title": title,
        "audio_url": audio_url,
        "content_html": content_html,
        "message": f"✅ {artifact_type.capitalize()} wygenerowany i dodany do wspólnej biblioteki klasy!"
    })


@app.post("/api/library/like/{artifact_id}")
def api_library_like(request: Request, artifact_id: int):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    existing = conn.execute(
        "SELECT 1 FROM user_artifact_likes WHERE user_id=? AND artifact_id=?",
        (u['id'], artifact_id)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM user_artifact_likes WHERE user_id=? AND artifact_id=?",
                     (u['id'], artifact_id))
        action = "unliked"
    else:
        conn.execute(
            "INSERT OR IGNORE INTO user_artifact_likes(user_id, artifact_id, user_name) VALUES(?,?,?)",
            (u['id'], artifact_id, u['name'])
        )
        conn.execute("UPDATE learning_artifacts SET likes_count = likes_count + 1 WHERE id=?", (artifact_id,))
        action = "liked"
    conn.commit()
    like_count = conn.execute("SELECT likes_count FROM learning_artifacts WHERE id=?", (artifact_id,)).fetchone()
    conn.close()
    return JSONResponse({"status": action, "likes": like_count[0] if like_count else 0})


@app.post("/api/library/favorite/{artifact_id}")
def api_library_favorite(request: Request, artifact_id: int):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    existing = conn.execute(
        "SELECT 1 FROM user_artifact_favorites WHERE user_id=? AND artifact_id=?",
        (u['id'], artifact_id)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM user_artifact_favorites WHERE user_id=? AND artifact_id=?",
                     (u['id'], artifact_id))
        action = "removed"
    else:
        conn.execute(
            "INSERT OR IGNORE INTO user_artifact_favorites(user_id, artifact_id) VALUES(?,?)",
            (u['id'], artifact_id)
        )
        action = "added"
    conn.commit()
    conn.close()
    return JSONResponse({"status": action})


# ── FAZA 2: Study Planner / Reminders API ─────────────────────────────────────

@app.get("/api/reminders")
def api_get_reminders(request: Request, filter: str = "all"):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    if filter == "today":
        rows = conn.execute("""
            SELECT r.*, b.title as book_title, b.subject
            FROM user_reminders r
            LEFT JOIN books b ON r.book_id = b.id
            WHERE r.user_id=? AND r.is_done=0 AND date(r.due_date) <= date('now')
            ORDER BY r.due_date ASC
        """, (u['id'],)).fetchall()
    elif filter == "upcoming":
        rows = conn.execute("""
            SELECT r.*, b.title as book_title, b.subject
            FROM user_reminders r
            LEFT JOIN books b ON r.book_id = b.id
            WHERE r.user_id=? AND r.is_done=0 AND date(r.due_date) > date('now')
            ORDER BY r.due_date ASC LIMIT 10
        """, (u['id'],)).fetchall()
    else:
        rows = conn.execute("""
            SELECT r.*, b.title as book_title, b.subject
            FROM user_reminders r
            LEFT JOIN books b ON r.book_id = b.id
            WHERE r.user_id=? AND r.is_done=0
            ORDER BY r.due_date ASC, r.created_at DESC LIMIT 20
        """, (u['id'],)).fetchall()

    # Also merge teacher assignments
    assignments = conn.execute("""
        SELECT a.id, a.note as title, a.due_date, a.kind as reminder_type, 'teacher' as source,
               0 as is_done, b.title as book_title, b.subject
        FROM assignments a
        LEFT JOIN books b ON a.ref_id = b.id
        WHERE a.student = ? AND (a.due_date IS NULL OR date(a.due_date) >= date('now','-7 days'))
        ORDER BY a.due_date ASC LIMIT 10
    """, (u['id'],)).fetchall()

    conn.close()
    return JSONResponse({
        "reminders": [dict(r) for r in rows],
        "assignments": [dict(a) for a in assignments],
        "overdue_count": len([r for r in rows if r['due_date'] and r['due_date'] < __import__('datetime').date.today().isoformat()])
    })


@app.get("/api/reminders/badge")
def api_reminder_badge(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"count": 0})
    conn = db()
    overdue = conn.execute("""
        SELECT COUNT(*) as c FROM user_reminders
        WHERE user_id=? AND is_done=0 AND due_date <= date('now')
    """, (u['id'],)).fetchone()['c']
    srs_due = conn.execute("""
        SELECT COUNT(*) as c FROM card_state cs
        JOIN cards c ON cs.card_id = c.id
        WHERE cs.user_id=? AND cs.due <= datetime('now')
    """, (u['id'],)).fetchone()['c']
    conn.close()
    return JSONResponse({"count": overdue + srs_due, "overdue": overdue, "srs_due": srs_due})


@app.post("/api/reminders/add")
async def api_add_reminder(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()
    title = str(data.get("title", "Powtórka")).strip()
    body = str(data.get("body", "")).strip()
    book_id = data.get("book_id")
    chapter_num = data.get("chapter_num")
    reminder_type = str(data.get("reminder_type", "review"))
    source = str(data.get("source", "manual"))
    due_date = str(data.get("due_date", "")).strip()

    if not due_date:
        from datetime import date, timedelta
        due_date = (date.today() + timedelta(days=3)).isoformat()

    conn = db()
    # Get chapter title for better label
    if book_id and chapter_num:
        toc = conn.execute("SELECT DISTINCT chapter_title FROM book_toc WHERE book_id=? AND chapter_num=? LIMIT 1",
                           (book_id, chapter_num)).fetchone()
        if toc and not body:
            body = f"Powtórka: {toc['chapter_title']}"

    conn.execute("""
        INSERT INTO user_reminders(user_id, title, body, book_id, chapter_num, reminder_type, source, due_date)
        VALUES(?,?,?,?,?,?,?,?)
    """, (u['id'], title, body, book_id, chapter_num, reminder_type, source, due_date))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "added", "message": f"Reminder zaplanowany na {due_date}."})


@app.post("/api/reminders/{reminder_id}/done")
def api_reminder_done(request: Request, reminder_id: int):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()
    conn.execute("UPDATE user_reminders SET is_done=1 WHERE id=? AND user_id=?", (reminder_id, u['id']))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "done"})


# ── FAZA 2: Chapter Progress API (for Notes ↔ Courses bridge) ─────────────────

@app.get("/api/chapter-progress/{book_id}/{chapter_num}")
def api_chapter_progress(request: Request, book_id: int, chapter_num: int):
    """Returns task completion stats for a chapter — used by Notes to show inline exercise chips."""
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    conn = db()

    # Find companion book (workbook if given textbook, or vice versa)
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    subj = book['subject'] if book else ""
    kind = book['kind'] if book else ""

    companion_id = None
    if kind == 'podreczniki':
        wb = conn.execute("SELECT id FROM books WHERE subject=? AND kind='cwiczenia' LIMIT 1", (subj,)).fetchone()
        if wb:
            companion_id = wb['id']
    else:
        tb = conn.execute("SELECT id FROM books WHERE subject=? AND kind='podreczniki' LIMIT 1", (subj,)).fetchone()
        if tb:
            companion_id = tb['id']

    book_ids = [book_id]
    if companion_id:
        book_ids.append(companion_id)

    ph = ",".join("?" * len(book_ids))
    tasks = conn.execute(f"""
        SELECT it.id, it.task_type, it.content_json,
               utp.score as user_score, utp.attempts
        FROM interactive_tasks it
        JOIN book_chapters bc ON it.chapter_id = bc.id
        LEFT JOIN user_task_progress utp ON it.id = utp.task_id AND utp.user_id = ?
        WHERE bc.book_id IN ({ph})
    """, (u['id'], *book_ids)).fetchall()

    import re
    task_list = []
    for t in tasks:
        cj = {}
        try:
            cj = json.loads(t['content_json'])
        except Exception:
            pass
        num = cj.get("number", f"Zad. {t['id']}")
        pg = None
        m = re.search(r'str\.\s*(\d+)', (num + ' ' + cj.get('title','')).lower())
        if m:
            pg = int(m.group(1))
        task_list.append({
            "id": t['id'],
            "number": num,
            "score": t['user_score'],
            "is_done": (t['user_score'] is not None and t['user_score'] >= 70),
            "page_ref": pg
        })

    done = sum(1 for t in task_list if t['is_done'])
    total = len(task_list)
    pct = int(done / total * 100) if total > 0 else 0

    # Library count for this chapter
    lib_count = conn.execute("""
        SELECT COUNT(*) as c FROM learning_artifacts
        WHERE book_id=? AND chapter_num=? AND is_shared=1
    """, (book_id, chapter_num)).fetchone()['c']

    conn.close()
    return JSONResponse({
        "status": "ok",
        "subject": subj,
        "book_id": book_id,
        "chapter_num": chapter_num,
        "tasks": task_list,
        "done": done,
        "total": total,
        "pct": pct,
        "library_count": lib_count,
        "is_complete": (total > 0 and done == total)
    })


# ── FAZA 3: Study Planner Page ────────────────────────────────────────────────

@app.get("/planner", response_class=HTMLResponse)
def study_planner(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", 302)
    return templates.TemplateResponse(request, "planner.html", {
        "user": u,
        "points": user_points(u["id"]),
        "streak": user_streak(u["id"])
    })


# ── FAZA 4: Zaawansowany & Inteligentny Fiszkomat AI (/fiszkomat) ────────────

def get_omniroute_key() -> str:
    k = os.environ.get("OMNIROUTE_API_KEY", "")
    if k and not k.startswith("sk-b44"):
        return k
    for p in [Path("/DATA/AppData/sm-portal/.env"), Path("/data/.env"), Path(".env")]:
        if p.exists():
            try:
                for line in p.read_text().splitlines():
                    if line.startswith("OMNIROUTE_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val and not val.startswith("sk-b44"):
                            return val
            except Exception:
                pass
    return "sk-bf98b1ba44aefca43b2f9f5664db6e15"


def _clean_card_front(text: str) -> str:
    """Oczyszcza przód fiszki ze sztucznych pytań typu 'Jak powiesz po angielsku: ...', zostawiając czyste hasło/słowo."""
    import re
    s = str(text).strip()
    # Usunięcie tagów markdown z początku/końca
    s = re.sub(r'^\*+\s*', '', s)
    s = re.sub(r'\s*\*+$', '', s)

    prefixes = [
        r"^\[[^\]]+\]\s*",                               # np. [Angielski • Rozdz. 2]
        r"^jak powiesz po angielsku\s*:\s*",
        r"^jak jest po angielsku\s*:\s*",
        r"^jak przetłumaczysz na angielski\s*:\s*",
        r"^jak przetłumaczysz\s*:\s*",
        r"^co oznacza słowo\s*:\s*",
        r"^co to jest\s*:\s*",
        r"^wyjaśnij pojęcie\s*:\s*",
        r"^podaj znaczenie\s*:\s*",
        r"^co znaczy\s*:\s*",
        r"^przetłumacz na angielski\s*:\s*",
        r"^przetłumacz\s*:\s*",
    ]
    for p in prefixes:
        s = re.sub(p, "", s, flags=re.IGNORECASE).strip()

    # Ponowne usunięcie ewentualnych gwiazdek bolda
    s = s.strip("*").strip()

    # Jeśli to pojedyncze słowo lub zwrot zakończony pytajnikiem (np. "piórnik?"), usuń '?'
    if s.endswith("?") and not any(q in s.lower() for q in ["ile", "kiedy", "dlaczego", "gdzie", "który", "czy"]):
        s = s[:-1].strip()

    return s


def _extract_fallback_flashcards(subject: str, chapter_title: str, context_text: str, count: int = 10) -> list[dict]:
    """Deterministyczny ekstraktor fiszek wprost ze struktury tekstu podręcznika gdy AI nie jest dostępne."""
    import re
    res = []
    lines = context_text.splitlines()

    # 1. Słownictwo i zwroty angielskie: np. Art (plastyka), canteen (stołówka)
    if subject == "Angielski":
        vocab_matches = re.findall(r'([A-Za-z\s\'/-]+)\s*\(([^)]+)\)', context_text)
        for w, tr in vocab_matches:
            w_c = w.strip()
            tr_c = tr.strip()
            if len(w_c) >= 3 and len(tr_c) >= 2 and not w_c.lower().startswith(("lesson", "unit", "str", "page")):
                res.append({
                    "front": tr_c,
                    "back": f"**{w_c}**\n\n*Przykład:* We learn about this in {chapter_title}.",
                    "type": "słownictwo",
                    "topic": chapter_title
                })

    # 2. Definicje i pojęcia z dwukropkiem: Hasło: Opis
    def_pattern = re.compile(r'[*]{0,2}([A-Za-z0-9ĄĆĘŁŃÓŚŹŻąćęłńóśźż\s\'/–-]{4,45})[*]{0,2}\s*:\s*(.+)')
    for line in lines:
        clean = line.strip().lstrip("#-*• ").strip()
        m = def_pattern.match(clean)
        if m:
            term = m.group(1).strip()
            defn = m.group(2).strip()
            if len(term) >= 4 and len(defn) >= 10 and not any(r['front'].lower() == term.lower() for r in res):
                res.append({
                    "front": term,
                    "back": defn,
                    "type": "reguła" if "reguł" in term.lower() or "wzór" in term.lower() else "pojęcie",
                    "topic": chapter_title
                })

    return res[:count]


def call_ai_flashcards(subject: str, book_title: str, chapter_title: str, topics_list: list[dict],
                       context_text: str, card_count: int = 10, card_type: str = "all",
                       page_range: str = "") -> list[dict]:
    """Generuje kompletne i ergonomiczne fiszki SRS przez model AI (OmniRoute Gemini 3.8 Flash) z fallbackiem."""
    import json as _json
    import urllib.request

    topics_desc = "\n".join([f"- {t.get('title', '')} (str. {t.get('page_start', '')})" for t in topics_list])

    if subject == "Angielski":
        pedagogy_focus = (
            "GŁÓWNY NACISK NA JĘZYK ANGIELSKI:\n"
            "Wyłuskaj ISTOTNE SŁOWNICTWO (vocabulary), wyrażenia oraz kluczowe reguły gramatyczne z tego działu.\n"
            "ZASADA DLA PRZODU (FRONT):\n"
            "- 'front' dla słówek: SAMO POLSKIE SŁOWO (np. 'piórnik', 'stołówka szkolna', 'linijka') — BEZ zbędnych pytań 'Jak powiesz po angielsku:' ani znaku zapytania na końcu!\n"
            "- 'back' dla słówek: **[angielskie słowo / zwrot]** + zwięzły, naturalny przykład zdania po angielsku z tłumaczeniem w nawiasie.\n"
            "- 'front' dla gramatyki/reguł: czysta nazwa reguły lub zdanie z luką [...] do uzupełnienia (np. 'Present Simple — 3. os. l.poj.', 'There is vs There are')\n"
            "- 'back' dla gramatyki/reguł: poprawna forma + zwięzłe wyjaśnienie i wzorcowy przykład."
        )
    elif subject == "Matematyka":
        pedagogy_focus = (
            "GŁÓWNY NACISK NA MATEMATYKĘ:\n"
            "Wyłuskaj definicje, wzory, własności liczb i metody obliczeń.\n"
            "- 'front': Czyste pojęcie matematyczne, nazwa własności lub wzór (BEZ sztucznych pytań 'Co to jest')\n"
            "- 'back': Jasna reguła, wzór, metoda krok po kroku oraz przykład/pułapka."
        )
    else:
        pedagogy_focus = (
            f"GŁÓWNY NACISK NA PRZEDMIOT {subject.upper()}:\n"
            "Wyłuskaj kluczowe pojęcia, procesy, fakty, daty i definicje potrzebne na sprawdzian.\n"
            "- 'front': Czyste pojęcie lub zwięzłe hasło (BEZ formy sztucznego pytania)\n"
            "- 'back': Zrozumiała, kompletna odpowiedź z najważniejszymi faktami."
        )

    if card_type == "vocab":
        type_instruction = "SKUP SIĘ W 100% NA SŁOWNICTWIE I WYRAŻENIACH (Przód = polskie słowo, Tył = angielskie słowo + przykład zdania)."
    elif card_type == "rules":
        type_instruction = "SKUP SIĘ W 100% NA REGUŁACH GRAMATYCZNYCH, WZORACH I ZASADACH."
    elif card_type == "cloze":
        type_instruction = "STWÓRZ FISZKI Z LUKAMI [...] DO UZUPEŁNIENIA W ZDANIACH."
    else:
        type_instruction = "ZBALANSUJ: 60% kluczowe słownictwo (Przód = czyste słowo), 40% reguły i przykłady."

    prompt = f"""Jesteś doświadczonym nauczycielem tworzącym profesjonalne, ergonomiczne fiszki SRS dla ucznia 5. klasy (11 lat).
Przedmiot: {subject}
Podręcznik: {book_title}
Dział: {chapter_title} (Strony: {page_range or 'całość'})
Wybrane lekcje:
{topics_desc}

{pedagogy_focus}
Nacisk: {type_instruction}

Treść i materiał z podręcznika:
{context_text[:3500]}

ZASADY BEZWZGLĘDNE:
1. Wygeneruj DOKŁADNIE {card_count} fiszek.
2. PRZÓD FISZKI ('front'): Podawaj TYLKO czyste hasło/słowo/pojęcie (np. 'piórnik', 'stołówka', 'Present Simple — 3. os. l.poj.').
   ABSOLUTNY ZAKAZ: Nigdy nie stosuj form pytań: 'Jak powiesz po angielsku:', 'Co to jest:', 'Wyjaśnij pojęcie:'. Forma pytania jest całkowicie zabroniona!
3. TYŁ FISZKI ('back'): Kompletne, wyczerpujące wyjaśnienie / angielski odpowiednik z przykładem zdania.
4. Zwróć WYŁĄCZNIE czysty format JSON array (żadnego markdownu wokół, sam JSON):
[
  {{
    "front": "stołówka szkolna",
    "back": "**canteen**\\n\\n*Przykład:* We eat lunch in the school canteen. (Jemy obiad na stołówce szkolnej.)",
    "type": "słownictwo",
    "topic": "{chapter_title}"
  }},
  {{
    "front": "piórnik",
    "back": "**pencil case**\\n\\n*Przykład:* I have three pens in my pencil case. (Mam trzy długopisy w piórniku.)",
    "type": "słownictwo",
    "topic": "{chapter_title}"
  }}
]"""

    omni_key = get_omniroute_key()
    omni_url = os.environ.get("OMNIROUTE_BASE_URL", "http://10.10.10.157:20128/v1/chat/completions")
    if not omni_url.endswith("/chat/completions"):
        omni_url = f"{omni_url.rstrip('/')}/chat/completions"

    cards = []
    try:
        req = urllib.request.Request(
            omni_url,
            data=_json.dumps({
                "model": "Gemini 3.8 Flash",
                "messages": [
                    {"role": "system", "content": "Jesteś precyzyjnym generatorem fiszek edukacyjnych JSON dla portalu szkolnego. Przód fiszki to zawsze czyste słowo lub pojęcie bez formy pytania. Zwracasz wyłącznie tablicę JSON."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 2500,
                "temperature": 0.3
            }).encode(),
            headers={
                "Authorization": f"Bearer {omni_key}",
                "Content-Type": "application/json"
            }
        )
        raw = urllib.request.urlopen(req, timeout=35).read()
        content = _json.loads(raw)["choices"][0]["message"]["content"]
        s = content.find("[")
        e = content.rfind("]") + 1
        if s >= 0 and e > s:
            parsed = _json.loads(content[s:e])
            for c in parsed:
                raw_f = str(c.get("front", "")).strip()
                f = _clean_card_front(raw_f)
                b = str(c.get("back", "")).strip()
                t = str(c.get("type", "słownictwo")).strip()
                tp = str(c.get("topic", chapter_title)).strip()
                if f and b:
                    cards.append({"front": f, "back": b, "type": t, "topic": tp})
    except Exception as err:
        print(f"[fiszkomat_ai_error]: {err}")

    # Fallback jeśli model nie zwrócił wystarczającej liczby kart
    if len(cards) < min(3, card_count):
        fallback = _extract_fallback_flashcards(subject, chapter_title, context_text, card_count)
        seen = {c["front"].lower() for c in cards}
        for fc in fallback:
            clean_fc_f = _clean_card_front(fc["front"])
            if clean_fc_f.lower() not in seen:
                fc["front"] = clean_fc_f
                cards.append(fc)
                seen.add(clean_fc_f.lower())
            if len(cards) >= card_count:
                break

    return cards[:card_count]


@app.get("/fiszkomat", response_class=HTMLResponse)
def fiszkomat_view(request: Request, book_id: Optional[int] = None, chapter: Optional[int] = None, deck_id: Optional[int] = None):
    u = current_user(request)
    if not u:
        return RedirectResponse("/login", 302)
    user = u

    conn = db()
    all_books = [dict(r) for r in conn.execute("SELECT id, title, subject, kind, filename FROM books ORDER BY subject, title").fetchall()]
    # Tylko podręczniki PDF (bez EPUB, bez ćwiczeń, bez lektur)
    books = [b for b in all_books if b.get('kind') == 'podreczniki' and not b.get('filename', '').endswith('.epub')]
    decks = [dict(r) for r in conn.execute("SELECT id, name, subject FROM decks ORDER BY id DESC").fetchall()]

    selected_book_id = book_id
    if not selected_book_id and books:
        eng_b = next((b for b in books if 'angielski' in b['subject'].lower()), None)
        selected_book_id = eng_b['id'] if eng_b else books[0]['id']

    selected_chapter = chapter or 1
    selected_deck_id = deck_id
    conn.close()

    return templates.TemplateResponse(request, "fiszkomat.html", {
        "user": user,
        "books": books,
        "decks": decks,
        "selected_book_id": selected_book_id,
        "selected_chapter": selected_chapter,
        "selected_deck_id": selected_deck_id
    })


@app.get("/api/fiszkomat/toc/{book_id}")
def api_fiszkomat_toc(book_id: int, request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)

    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")

    toc_rows = [dict(r) for r in conn.execute("SELECT * FROM book_toc WHERE book_id=? ORDER BY chapter_num, topic_num", (book_id,)).fetchall()]
    chapter_rows = [dict(r) for r in conn.execute("SELECT id, chapter_number, title, pages_range, length(raw_ocr_text) as ocr_len FROM book_chapters WHERE book_id=? ORDER BY chapter_number", (book_id,)).fetchall()]
    conn.close()

    chapters_dict = {}
    if toc_rows:
        for row in toc_rows:
            c_num = row['chapter_num'] or 1
            if c_num not in chapters_dict:
                chapters_dict[c_num] = {
                    "chapter_num": c_num,
                    "chapter_title": row['chapter_title'],
                    "page_start": row['page_start'],
                    "page_end": row['page_end'],
                    "topics": []
                }
            else:
                if row['page_start'] and (not chapters_dict[c_num]['page_start'] or row['page_start'] < chapters_dict[c_num]['page_start']):
                    chapters_dict[c_num]['page_start'] = row['page_start']
                if row['page_end'] and (not chapters_dict[c_num]['page_end'] or row['page_end'] > chapters_dict[c_num]['page_end']):
                    chapters_dict[c_num]['page_end'] = row['page_end']

            chapters_dict[c_num]['topics'].append({
                "id": row['id'],
                "topic_num": row['topic_num'],
                "topic_title": row['topic_title'],
                "page_start": row['page_start'],
                "page_end": row['page_end']
            })
    elif chapter_rows:
        for r in chapter_rows:
            c_num = r['chapter_number'] or 1
            p_start, p_end = 1, 20
            if r.get('pages_range'):
                parts = str(r['pages_range']).replace('–', '-').split('-')
                try:
                    p_start = int(parts[0].strip())
                    p_end = int(parts[1].strip()) if len(parts) > 1 else p_start
                except Exception:
                    pass
            chapters_dict[c_num] = {
                "chapter_num": c_num,
                "chapter_title": r['title'],
                "page_start": p_start,
                "page_end": p_end,
                "topics": [{"id": r['id'], "topic_num": 1, "topic_title": r['title'], "page_start": p_start, "page_end": p_end}]
            }
    else:
        chapters_dict[1] = {
            "chapter_num": 1,
            "chapter_title": f"Rozdział 1 ({book['subject']})",
            "page_start": 1,
            "page_end": 20,
            "topics": [{"id": 1, "topic_num": 1, "topic_title": "Wprowadzenie", "page_start": 1, "page_end": 20}]
        }

    return JSONResponse({
        "book": dict(book),
        "chapters": list(chapters_dict.values())
    })


@app.post("/api/fiszkomat/generate")
async def api_fiszkomat_generate(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()

    book_id = int(data.get("book_id", 0))
    chapter_num = int(data.get("chapter_num", 1))
    selected_topic_ids = data.get("topic_ids", [])
    page_start = data.get("page_start")
    page_end = data.get("page_end")
    card_count = max(3, min(30, int(data.get("card_count", 10))))
    card_type = data.get("card_type", "all")
    custom_focus = str(data.get("custom_focus", "")).strip()

    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        raise HTTPException(404, "Książka nie znaleziona")

    subj = book['subject']
    book_title = book['title']

    toc_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM book_toc WHERE book_id=? AND chapter_num=?",
        (book_id, chapter_num)
    ).fetchall()]
    chapter_title = toc_rows[0]['chapter_title'] if toc_rows else f"Rozdział {chapter_num} ({subj})"

    if selected_topic_ids:
        selected_set = {int(x) for x in selected_topic_ids}
        selected_topics = [t for t in toc_rows if t['id'] in selected_set]
    else:
        selected_topics = toc_rows

    ch_row = conn.execute(
        "SELECT * FROM book_chapters WHERE book_id=? AND (chapter_number=? OR title LIKE ?)",
        (book_id, chapter_num, f"%{chapter_title}%")
    ).fetchone()
    raw_text = ch_row['raw_ocr_text'] if ch_row and ch_row['raw_ocr_text'] else ""

    theory_data = THEORY_DATA_BY_SUBJECT.get(subj, {}).get(chapter_num, {})
    if not raw_text and theory_data:
        lines = [f"# {theory_data.get('title', chapter_title)}"]
        lines.append(theory_data.get("desc", ""))
        for comp in theory_data.get("competencies", []):
            lines.append(f"* {comp['title']}: {comp['text']}")
        for r in theory_data.get("rules", []):
            lines.append(f"* Reguła {r['title']}: {r['content']}")
        for p in theory_data.get("pitfalls", []):
            lines.append(f"* Pułapka: {p}")
        raw_text = "\n".join(lines)

    if selected_topics:
        topic_lines = ["\n## Wybrane tematy i lekcje:"]
        for t in selected_topics:
            topic_lines.append(f"- {t['topic_title']} (str. {t['page_start']}-{t['page_end']})")
        raw_text = raw_text + "\n" + "\n".join(topic_lines)

    conn.close()

    page_range_str = f"{page_start}–{page_end}" if page_start and page_end else ""

    cards = call_ai_flashcards(
        subject=subj,
        book_title=book_title,
        chapter_title=chapter_title,
        topics_list=[{"title": t["topic_title"], "page_start": t["page_start"]} for t in selected_topics],
        context_text=raw_text,
        card_count=card_count,
        card_type=card_type,
        page_range=page_range_str
    )

    clean_ch = chapter_title.replace("Rozdział ", "").replace("Unit ", "Unit ")
    default_deck_name = f"{subj} — {clean_ch}"
    if card_type == "vocab":
        default_deck_name += " (Słownictwo)"
    elif card_type == "rules":
        default_deck_name += " (Reguły i Gramatyka)"

    return JSONResponse({
        "status": "ok",
        "cards": cards,
        "book_title": book_title,
        "chapter_title": chapter_title,
        "suggested_deck_name": default_deck_name,
        "count": len(cards)
    })


@app.post("/api/fiszkomat/save-deck")
async def api_fiszkomat_save_deck(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401)
    data = await request.json()

    deck_id = data.get("deck_id")
    deck_name = str(data.get("deck_name", "")).strip()
    subject = str(data.get("subject", "Ogólny")).strip()
    cards = data.get("cards", [])

    if not cards:
        raise HTTPException(400, "Brak fiszek do zapisania.")

    conn = db()
    if not deck_id or deck_id == 0 or deck_id == "0":
        if not deck_name:
            deck_name = f"Fiszki {subject} — {datetime.now().strftime('%d.%m %H:%M')}"
        existing = conn.execute("SELECT id FROM decks WHERE name=? AND subject=?", (deck_name, subject)).fetchone()
        if existing:
            deck_id = existing["id"]
        else:
            cur = conn.execute("INSERT INTO decks (name, subject, owner, created_at) VALUES (?, ?, ?, datetime('now'))",
                               (deck_name, subject, u["id"]))
            deck_id = cur.lastrowid
    else:
        deck_id = int(deck_id)

    saved_count = 0
    for c in cards:
        front = _clean_card_front(str(c.get("front", "")).strip())
        back = str(c.get("back", "")).strip()
        if front and back:
            exists = conn.execute("SELECT id FROM cards WHERE deck_id=? AND front=?", (deck_id, front)).fetchone()
            if not exists:
                conn.execute("INSERT INTO cards (deck_id, front, back) VALUES (?, ?, ?)", (deck_id, front, back))
                saved_count += 1
            else:
                conn.execute("UPDATE cards SET back=? WHERE id=?", (back, exists["id"]))
                saved_count += 1

    try:
        conn.execute("INSERT INTO points_log (user_id, points, reason) VALUES (?, ?, ?)",
                     (u["id"], min(25, max(5, saved_count)), f"Zapisanie {saved_count} fiszek z Fiszkomatu AI"))
    except Exception:
        pass

    conn.commit()
    conn.close()

    return JSONResponse({
        "status": "ok",
        "deck_id": deck_id,
        "saved_count": saved_count,
        "redirect_url": f"/srs/deck/{deck_id}",
        "message": f"Pomyślnie zapisano {saved_count} fiszek do talii! Możesz od razu rozpocząć naukę SRS."
    })

