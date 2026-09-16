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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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
            headers={"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"},
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
            headers={"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"},
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
    sub = conn.execute("SELECT * FROM subjects WHERE id=?", (sid,)).fetchone()
    if not sub:
        raise HTTPException(404)
        
    books_raw = conn.execute("SELECT * FROM books WHERE subject=? ORDER BY title", (sub["name"],)).fetchall()
    decks = conn.execute("SELECT * FROM decks WHERE subject=? ORDER BY name", (sub["name"],)).fetchall()
    conn.close()
    
    import json as _json
    links = _json.loads(sub["links"] or "[]")
    
    # Dodatkowa kategoryzacja e-książek per przedmiot
    podreczniki = []
    cwiczenia = []
    for b in books_raw:
        tl = b["title"].lower()
        if "ćwicz" in tl or "cwicz" in tl or "workbook" in tl or "skan" in tl:
            cwiczenia.append(b)
        else:
            podreczniki.append(b)
            
    return templates.TemplateResponse(request, "subject.html", {
        "user": user, "sub": sub, "decks": decks, "links": links,
        "podreczniki": podreczniki, "cwiczenia": cwiczenia
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

    return templates.TemplateResponse(request, "library.html", {"user": user, "library_data": library_data})


@app.post("/library/upload")
def upload_book(request: Request, title: str = Form(""), subject: str = Form(""), grade: str = Form(""), file: UploadFile = File(...)):
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
    conn.execute("INSERT INTO books(title,subject,grade,filename,filetype,uploaded_by) VALUES(?,?,?,?,?,?)",
                 (title.strip() or file.filename, subject, grade, safe, ext[1:], user["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse("/library", 302)


@app.get("/read/{book_id}", response_class=HTMLResponse)
def reader(request: Request, book_id: int):
    user = require(current_user(request))
    conn = db()
    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    prog = conn.execute("SELECT page FROM reading_progress WHERE user_id=? AND book_id=?", (user["id"], book_id)).fetchone()
    conn.close()
    if not book:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "reader.html", {"user": user, "book": book, "page": prog["page"] if prog else 0})


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
def book_file(filename: str):
    path = (BOOKS / filename).resolve()
    if not str(path).startswith(str(BOOKS.resolve())) or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=filename)


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
    
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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
    ha_token = os.environ.get("HA_MCP_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI0NDRkZTNkYWI5Zjk0ZWQ1YThhZmQ1ZDEwODIxOWY4ZSIsImlhdCI6MTc4MjI4MzE3NSwiZXhwIjoyMDk3NjQzMTc1fQ.oQkkJw8q8Fe9ZtPovAU3HIUkZqUfBThpj74LK_3xJNE")
    if not ha_token:
        # Fallback reading from /opt/data/profiles/edu/.env or home-ha/.env
        for env_f in ["/opt/data/profiles/edu/.env", "/opt/data/profiles/home-ha/.env"]:
            if os.path.exists(env_f):
                with open(env_f, "r") as fp:
                    for line in fp:
                        if line.startswith("HA_MCP_TOKEN="):
                            ha_token = line.strip().split("=", 1)[1]
                            break
            if ha_token:
                break

    import urllib.request
    import json

    def get_ha_state(entity_id):
        if not ha_token:
            return {}
        try:
            req = urllib.request.Request(
                f"http://10.10.10.123:8123/api/states/{entity_id}",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
            )
            res = urllib.request.urlopen(req, timeout=4)
            return json.loads(res.read().decode("utf-8"))
        except Exception:
            return {}

    # Wyciągamy sensory - Plan
    plan_entity = get_ha_state("sensor.vultron_plan_stanislaw_mikos_next")
    if not plan_entity.get("attributes", {}).get("lekcje"):
        plan_entity = get_ha_state("sensor.vultron_plan_stanislaw_mikos_curr")

    # Dodatkowe wyciąganie nauczycieli i sal aby powiązać z naszymi przedmiotami
    zaciagniete_lekcje = plan_entity.get("attributes", {}).get("lekcje", [])
    
    # Przechwytujemy wszystkich nauczycieli per przedmiot i updatujemy bazę (Magia!)
    if zaciagniete_lekcje:
        import sqlite3
        with sqlite3.connect('/data/portal.db') as sync_conn:
            cur = sync_conn.cursor()
            try:
                # Kolumna teacher
                cur.execute("ALTER TABLE subjects ADD COLUMN teacher TEXT")
            except Exception:
                pass
            for lekcja in zaciagniete_lekcje:
                if "p" in lekcja and "n" in lekcja:
                    pz = lekcja["p"].strip()
                    n = lekcja["n"].strip()
                    if n and pz:
                        cur.execute("UPDATE subjects SET teacher=? WHERE name=?", (n, pz))
            sync_conn.commit()

    freq_entity = get_ha_state("sensor.vultron_frekwencja_stanislaw_mikos")
    oceny_entity = get_ha_state("sensor.vultron_oceny_stanislaw_mikos_p1")
    
    # ======= MAGIA APPLE-OCEN (Synchronizacja avg/prop) ========
    oceny_data = oceny_entity.get("attributes", {}).get("oceny", [])
    if oceny_data:
        import sqlite3
        with sqlite3.connect('/data/portal.db') as sync_conn:
            cur = sync_conn.cursor()
            try:
                cur.execute("ALTER TABLE subjects ADD COLUMN avg TEXT")
            except Exception: pass
            try:
                cur.execute("ALTER TABLE subjects ADD COLUMN prop TEXT")
            except Exception: pass
                
            for ocena in oceny_data:
                pz = ocena.get("przedmiot", "").strip()
                srednia = ocena.get("srednia")
                proponowana = ocena.get("proponowana", "-")
                if pz:
                    srednia_str = f"{float(srednia):.2f}" if srednia else "-"
                    cur.execute("UPDATE subjects SET avg=?, prop=? WHERE name=?", (srednia_str, proponowana, pz))
            sync_conn.commit()
    # ==========================================================

    terminarz_entity = get_ha_state("sensor.vultron_terminarz_stanislaw_mikos")
    numerek_entity = get_ha_state("sensor.vultron_szczesliwy_numerek_stanislaw_mikos")
    freq_entity = get_ha_state("sensor.vultron_freq_stanislaw_mikos")

    return {
        "student": "Stanisław Mikos",
        "klasa": "5B",
        "szkola": "SP13 w Gliwicach",
        "numerek": numerek_entity.get("attributes", {}).get("numer", 0),
        "plan": plan_entity.get("attributes", {}).get("lekcje", []),
        "dni_wolne": plan_entity.get("attributes", {}).get("dni_wolne", []),
        "oceny": oceny_entity.get("attributes", {}).get("lista_przedmiotow", []),
        "srednia_proponowana": oceny_entity.get("attributes", {}).get("srednia_proponowanych"),
        "srednia_okresowa": oceny_entity.get("attributes", {}).get("srednia_okresowych"),
        "terminarz": terminarz_entity.get("attributes", {}).get("lista", []),
        "frekwencja": freq_entity.get("attributes", {}).get("wpisy", [])
    }


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



@app.get("/admin/ocr", response_class=HTMLResponse)
def admin_ocr_panel(request: Request):
    user = require(current_user(request))
    if user['role'] != 'admin':
        raise HTTPException(403, "Not authorized")
    conn = db()
    books = [dict(r) for r in conn.execute("SELECT * FROM books").fetchall()]
    conn.close()
    return templates.TemplateResponse(request, "dashboard_ocr.html", {"user": user, "books": books}, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})

@app.get("/admin/ocr/status")
def admin_ocr_status(request: Request):
    user = require(current_user(request))
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized"})
    
    conn = db()
    chapters = conn.execute("SELECT book_id, pages_range, status FROM book_chapters").fetchall()
    conn.close()
    
    stats = {}
    for c in chapters:
        bd = c['book_id']
        if bd not in stats:
            stats[bd] = []
        stats[bd].append({"pages": c["pages_range"], "status": c["status"]})
        
    return JSONResponse(stats)

@app.post("/admin/ocr/trigger")
async def admin_ocr_trigger(request: Request):
    user = require(current_user(request))
    if user['role'] != 'admin':
        return JSONResponse({"msg": "Odmowa."})
    
    data = await request.json()
    bd = data.get("book_id")
    pg = data.get("pages_range", "1-10")
    subj = data.get("subject", "Nieznany")
    
    conn = db()
    b = conn.execute("SELECT * FROM books WHERE id=?", (bd,)).fetchone()
    conn.execute("INSERT INTO book_chapters (book_id, pages_range, title, status) VALUES (?, ?, ?, 'pending')", (bd, pg, "Automatyczna porcja"))
    conn.commit()
    conn.close()
    
    if not b:
        return JSONResponse({"msg": "Nie znaleziono id powiazanej księgi do parsowania."})
        
    try:
        return JSONResponse({"msg": "Polecenie wysłano na szynę."})
    except Exception as e:
         return JSONResponse({"msg": f"Błąd N8N: {e}"})


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
    
    # 2. Pobieramy zadania interaktywne przypisane do chapterów tej książki
    raw_tasks = conn.execute("""
        SELECT it.* FROM interactive_tasks it 
        JOIN book_chapters bc ON it.chapter_id = bc.id 
        WHERE bc.book_id = ?
        ORDER BY it.id ASC
    """, (book_id,)).fetchall()
    
    # Jeśli dla tego book_id nie ma jeszcze zadań, ale mamy powiązaną książkę z tego samego przedmiotu z zadaniami (np. podręcznik vs zeszyt ćwiczeń)
    if not raw_tasks:
        raw_tasks = conn.execute("""
            SELECT it.* FROM interactive_tasks it 
            JOIN book_chapters bc ON it.chapter_id = bc.id 
            JOIN books b ON bc.book_id = b.id
            WHERE b.subject = ?
            ORDER BY it.id ASC
        """, (b_subj,)).fetchall()

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
            tasks.append(td)
        except Exception as e:
            pass

    # 3. Pobieramy wyekstrahowany tekst OCR z book_chapters
    chap = conn.execute("SELECT raw_ocr_text FROM book_chapters WHERE book_id=? AND status='completed' LIMIT 1", (book_id,)).fetchone()
    raw_ocr_text = chap['raw_ocr_text'] if chap and chap['raw_ocr_text'] else "Tekst OCR dla tego podręcznika jest obecnie przetwarzany w kolejce N8N."

    conn.close()
    return templates.TemplateResponse(request, "course_play.html", {
        "user": u, 
        "book": dict(book),
        "target_deck_id": target_deck_id,
        "cards": cards,
        "tasks": tasks,
        "raw_ocr_text": raw_ocr_text
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
    task_id = data.get("task_id")
    score = data.get("score", 0) # 0 - 100%
    
    conn = db()
    existing = conn.execute("SELECT id, attempts FROM user_task_progress WHERE user_id=? AND task_id=?", (u["id"], task_id)).fetchone()
    if existing:
        conn.execute("UPDATE user_task_progress SET score=MAX(score, ?), attempts=attempts+1, last_attempt=datetime('now') WHERE id=?", (score, existing["id"]))
    else:
        conn.execute("INSERT INTO user_task_progress (user_id, task_id, score, attempts) VALUES (?, ?, ?, 1)", (u["id"], task_id, score))
    
    # Oblicz globalny postęp dla tej książki/przedmiotu
    # Pobierz chapter_id -> book_id
    b_row = conn.execute("SELECT bc.book_id FROM interactive_tasks it JOIN book_chapters bc ON it.chapter_id=bc.id WHERE it.id=?", (task_id,)).fetchone()
    book_id = b_row[0] if b_row else None
    
    overall_pct = 0
    if book_id:
        total_tasks = conn.execute("SELECT COUNT(*) FROM interactive_tasks it JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=?", (book_id,)).fetchone()[0]
        completed_tasks = conn.execute("SELECT COUNT(DISTINCT task_id) FROM user_task_progress utp JOIN interactive_tasks it ON utp.task_id=it.id JOIN book_chapters bc ON it.chapter_id=bc.id WHERE bc.book_id=? AND utp.user_id=? AND utp.score>=70", (book_id, u["id"])).fetchone()[0]
        if total_tasks > 0:
            overall_pct = int((completed_tasks / total_tasks) * 100)
            
    conn.commit()
    conn.close()
    return JSONResponse({"status": "ok", "score": score, "overall_pct": overall_pct})
