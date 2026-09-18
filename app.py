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
        {"label":"🌲 Lektura: Baśniobór (PDF)","url":"/read/101"},
        {"label":"📗 Podręcznik 'Między Nami 5' (Flipbook GWO)","url":"https://drive.google.com/drive/u/0/my-drive"},
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
@app.get("/subjects/{sid}", response_class=HTMLResponse)
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
        "angielski_podrecznik.pdf": 120,
        "biologia_podrecznik.pdf": 54,
        "historia_podrecznik.pdf": 32,
    }
    
    podreczniki = []
    cwiczenia = []
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
        if "ćwicz" in tl or "cwicz" in tl or "workbook" in tl or "skan" in tl or bd.get("kind") == "workbook":
            cwiczenia.append(bd)
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
    user = require_role(current_user(request), "admin", "teacher")
    conn = db()
    conn.execute("INSERT INTO cards(deck_id,front,back) VALUES(?,?,?)", (deck_id, front, back))
    conn.commit(); conn.close()
    return RedirectResponse(f"/srs/deck/{deck_id}", 302)


@app.get("/srs/deck/{deck_id}", response_class=HTMLResponse)
def deck_view(request: Request, deck_id: int):
    user = require(current_user(request))
    conn = db()
    deck = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    cards = conn.execute("SELECT id, front FROM cards WHERE deck_id=?", (deck_id,)).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "deck.html", {"user": user, "deck": deck, "cards": cards})



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
def edit_card(card_id: int, front: str = Form(...), back: str = Form(...), ):
    with get_db() as db:
        db.execute("UPDATE cards SET front=?, back=? WHERE id=?", (front, back, card_id))
        db.commit()
    # It redirects or returns JSON depending on how client handles it.
    return {"status": "ok"}

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
    'angielski_podrecznik.pdf': 4,
    'polski_podrecznik.pdf': 396,
    'angielski_cwiczenia.pdf': 105,
    'biologia_podrecznik.pdf': 54,
    'historia_podrecznik.pdf': 32,
    'matematyka_cwiczenia.pdf': 100,
    'matematyka_podrecznik.pdf': 268
}

def get_book_total_pages(filename: str) -> int:
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
    raw_books = conn.execute("SELECT * FROM books").fetchall()
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
    books = [dict(r) for r in conn.execute("SELECT id, filename, title FROM books").fetchall()]
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
        books = conn.execute(f"SELECT id, title, subject FROM books WHERE id IN ({placeholders}) ORDER BY title DESC", tuple(books_with_data)).fetchall()
        for b in books:
            b_dict = dict(b)
            subj = b_dict['subject']
            if subj not in bundled_courses:
                bundled_courses[subj] = {
                    "textbook": None, 
                    "workbook": None, 
                    "total_tasks": 0, 
                    "done_tasks": 0, 
                    "progress_pct": 0
                }
                
            task_cnt = conn.execute("SELECT COUNT(*) as c FROM interactive_tasks WHERE chapter_id IN (SELECT id FROM book_chapters WHERE book_id=?)", (b['id'],)).fetchone()['c']
            done_cnt = conn.execute("SELECT COUNT(DISTINCT utp.task_id) as c FROM user_task_progress utp JOIN interactive_tasks it ON utp.task_id=it.id JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=? AND utp.user_id=? AND utp.score>=70", (b['id'], u['id'])).fetchone()['c']
            
            b_dict['tasks_cnt'] = task_cnt
            b_dict['done_cnt'] = done_cnt
            b_dict['pct'] = int((done_cnt / task_cnt) * 100) if task_cnt > 0 else 0
            
            if "Ćwiczenia" in b_dict['title'] or "cwiczenia" in b_dict['title'].lower():
                bundled_courses[subj]["workbook"] = b_dict
            else:
                bundled_courses[subj]["textbook"] = b_dict
                
            bundled_courses[subj]["total_tasks"] += task_cnt
            bundled_courses[subj]["done_tasks"] += done_cnt
            
        for subj, d in bundled_courses.items():
            if d["total_tasks"] > 0:
                d["progress_pct"] = int((d["done_tasks"] / d["total_tasks"]) * 100)
            
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
    
    # 1. Pobieramy powiązane karty Anki (SRS) z talii pasującej do przedmiotu/książki
    b_subj = book['subject']
    deck = conn.execute("SELECT id, name FROM decks WHERE subject=? AND (name LIKE ? OR name LIKE ?) LIMIT 1",
                        (b_subj, f"%{b_subj}%", "%Unit%")).fetchone()
    if not deck:
        deck = conn.execute("SELECT id, name FROM decks WHERE subject=? LIMIT 1", (b_subj,)).fetchone()
    
    target_deck_id = deck['id'] if deck else None
    cards = []
    if target_deck_id:
        cards = [dict(r) for r in conn.execute("SELECT id, front, back FROM cards WHERE deck_id=? LIMIT 12", (target_deck_id,)).fetchall()]
    
    # 2. Pobieramy zadania interaktywne przypisane do chapterów tej konkretnej książki
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
            td['chapter_num'] = 1
            if page_num in (3, 4):
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Zapis i porównywanie liczb"
            elif page_num in (5, 6):
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Działania pamięciowe i sprytne liczenie"
            elif page_num in (11, 12):
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Liczby wielocyfrowe i oś liczbowa"
            else:
                td['chapter_name'] = "1. Liczby naturalne"
                td['topic_name'] = "Ćwiczenia z podręcznika"

            tasks.append(td)
        except Exception as e:
            pass

    # Sortowanie zadań według logicznej kolejności stron i numerów ćwiczeń
    tasks.sort(key=lambda x: (x.get('page_ref') or 999, x.get('exercise_num') or 999, x.get('id') or 0))

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

    # 8. Pobieramy listę rozdziałów (np. z book_toc)
    chaps = conn.execute("""
        SELECT DISTINCT chapter_num, chapter_title 
        FROM book_toc 
        WHERE book_id=? 
        ORDER BY chapter_num ASC
    """, (book_id,)).fetchall()
    if not chaps and b_subj == 'Matematyka':
        chaps = conn.execute("""
            SELECT DISTINCT chapter_num, chapter_title 
            FROM book_toc 
            WHERE book_id=103 
            ORDER BY chapter_num ASC
        """).fetchall()
    chapters_data = [dict(c) for c in chaps]
    if not chapters_data:
        chapters_data = [
            {"chapter_num": 1, "chapter_title": "1. Liczby naturalne i działania"},
            {"chapter_num": 2, "chapter_title": "2. Własności liczb naturalnych"},
            {"chapter_num": 3, "chapter_title": "3. Ułamki zwykłe"},
            {"chapter_num": 4, "chapter_title": "4. Figury na płaszczyźnie"},
            {"chapter_num": 5, "chapter_title": "5. Ułamki dziesiętne"},
            {"chapter_num": 6, "chapter_title": "6. Pola figur"},
            {"chapter_num": 7, "chapter_title": "7. Liczby całkowite"},
            {"chapter_num": 8, "chapter_title": "8. Objętość figur"}
        ]

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

    # 10. Pobieramy współdzielone materiały (infografiki i podcasty klasy 5B z ulubionymi na początku i polubieniami)
    artifacts = [dict(r) for r in conn.execute("""
        SELECT la.*,
               EXISTS(SELECT 1 FROM user_artifact_favorites uaf WHERE uaf.artifact_id = la.id AND uaf.user_id = ?) as is_favorite,
               EXISTS(SELECT 1 FROM user_artifact_likes ual WHERE ual.artifact_id = la.id AND ual.user_id = ?) as is_liked,
               (SELECT GROUP_CONCAT(ual.user_name, ', ') FROM user_artifact_likes ual WHERE ual.artifact_id = la.id) as liked_by_names
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
               EXISTS(SELECT 1 FROM user_artifact_likes ual WHERE ual.artifact_id = la.id AND ual.user_id = ?) as is_liked,
               (SELECT GROUP_CONCAT(ual.user_name, ', ') FROM user_artifact_likes ual WHERE ual.artifact_id = la.id) as liked_by_names
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

@app.post("/api/artifacts/{art_id}/toggle-like")
def api_toggle_artifact_like(request: Request, art_id: int):
    u = current_user(request)
    if not u: raise HTTPException(401)
    u_dict = dict(u)
    user_name = u_dict.get("name") or u_dict.get("login") or "Uczeń 5B"
    conn = db()
    liked = conn.execute("SELECT 1 FROM user_artifact_likes WHERE user_id=? AND artifact_id=?", (u['id'], art_id)).fetchone()
    if liked:
        conn.execute("DELETE FROM user_artifact_likes WHERE user_id=? AND artifact_id=?", (u['id'], art_id))
        is_liked = 0
    else:
        conn.execute("INSERT OR IGNORE INTO user_artifact_likes (user_id, artifact_id, user_name) VALUES (?, ?, ?)", (u['id'], art_id, user_name))
        is_liked = 1
    cnt = conn.execute("SELECT COUNT(*) FROM user_artifact_likes WHERE artifact_id=?", (art_id,)).fetchone()[0]
    conn.execute("UPDATE learning_artifacts SET likes_count=? WHERE id=?", (cnt, art_id))
    likers = conn.execute("SELECT GROUP_CONCAT(user_name, ', ') FROM user_artifact_likes WHERE artifact_id=?", (art_id,)).fetchone()[0] or ''
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
    cur.execute("""
        INSERT INTO learning_artifacts (book_id, chapter_num, artifact_type, title, description, content_html, audio_url, duration, user_id, author_name, is_shared)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (book_id, chapter_num, artifact_type, title, description, content_html, audio_url, duration, u["id"], author_name, is_shared))
    art_id = cur.lastrowid
    conn.commit()
    created = conn.execute("""
        SELECT la.*, 0 as is_favorite, 0 as is_liked, '' as liked_by_names
        FROM learning_artifacts la WHERE la.id=?
    """, (art_id,)).fetchone()
    conn.close()
    return {"status": "ok", "artifact": dict(created) if created else {}}


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
    if numeric_task_id:
        existing = conn.execute("SELECT id, score, attempts FROM user_task_progress WHERE user_id=? AND task_id=?", (u["id"], numeric_task_id)).fetchone()
        if existing:
            conn.execute("UPDATE user_task_progress SET score=MAX(score, ?), attempts=attempts+1, last_attempt=datetime('now') WHERE id=?", (score, existing["id"]))
        else:
            conn.execute("INSERT INTO user_task_progress (user_id, task_id, score, attempts) VALUES (?, ?, ?, 1)", (u["id"], numeric_task_id, score))
        
        # Zapisz punkty w points_log jeśli zaliczone (>= 70%)
        if score >= 70:
            try:
                conn.execute("INSERT INTO points_log (user_id, points, reason) VALUES (?, ?, ?)",
                             (u["id"], 5, f"Rozwiązanie zadania #{numeric_task_id} ({score}%)"))
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
    conn.close()
    return JSONResponse({"status": "ok", "score": score, "task_id": numeric_task_id, "overall_pct": overall_pct})


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

