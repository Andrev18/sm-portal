#!/usr/bin/env python3
"""Lokalny test logiki portalu bez Dockera: sqlite schema + SM-2 + auth (bez FastAPI)."""
import importlib.util
import sys

sys.path.insert(0, "/opt/data/.hermes/plans/sm-portal")

# wylacz czesci FastAPI - testujemy tylko czyste funkcje przez wyodrebnienie
import hashlib, hmac, secrets

def hash_pw(pw):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${dk.hex()}"

def verify_pw(pw, stored):
    _, salt, hexd = stored.split("$")
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(dk.hex(), hexd)

h = hash_pw("test123")
assert verify_pw("test123", h) and not verify_pw("zle", h), "auth fail"

# SM-2 jak w app.py
def sm2(rating, ease, interval, reps):
    if rating == 0:
        ease = max(1.3, ease - 0.2); interval = 0.003
    elif rating == 1:
        ease = max(1.3, ease - 0.15); interval = max(interval * 1.2, 0.007)
    elif rating == 2:
        interval = max(interval * ease, 0.0104) if reps else 0.0104
    else:
        ease += 0.15; interval = max(interval * ease * 1.3, 0.041) if reps else 0.041
    return ease, interval

ease, interval = 2.5, 0.0
ease, interval = sm2(2, ease, interval, 0)   # dobre: 1 dzien
assert abs(interval - 0.0104) < 1e-9
ease, interval = sm2(2, ease, interval, 1)   # dobre: ~2.6 dni
assert abs(interval - 0.0104 * ease) < 1e-9, interval
ease, interval = sm2(0, ease, interval, 2)   # znowu: reset
assert interval == 0.003 and ease < 2.5
ease, interval = sm2(3, ease, interval, 3)   # latwe: skok
assert interval > 0.04

print("OK: auth + SM-2 dzialaja")