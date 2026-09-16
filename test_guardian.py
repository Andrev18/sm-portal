import subprocess
import requests
import time

PORTAL_URL = "http://10.10.10.157:8000"

def get_session_cookie():
    try:
        r = requests.post(f"{PORTAL_URL}/pin", data={"pin": "0000"}, allow_redirects=False)
        return r.cookies.get_dict().get("session")
    except Exception as e:
        print(f"FAILED (PIN login failed): {e}")
        return None

def test_routes():
    print("--- ROZPOCZĘCIE TESTÓW ZABEZPIECZAJĄCYCH (GUARDIAN) ---")
    session = get_session_cookie()
    if not session:
        print("[!] Błąd Krytyczny: Nie można zalogować się jako ADMIN.")
        return False
    
    cookies = {"session": session}
    
    routes_to_test = [
        "/",
        "/courses",
        "/courses/play/108",
        "/admin/ocr",
        "/admin/ocr/status",
        "/srs",
        "/subjects",
        "/subject/3",
        "/fiszkomat/chat"
    ]
    
    all_passed = True
    for route in routes_to_test:
        try:
            res = requests.get(f"{PORTAL_URL}{route}", cookies=cookies, timeout=5)
            if res.status_code == 200:
                print(f"[OK] {route} -> 200")
            elif res.status_code == 500:
                print(f"[CRITICAL FAIL] {route} -> 500 Internal Server Error")
                all_passed = False
            elif res.status_code == 404:
                print(f"[FAIL] {route} -> 404 Not Found")
                all_passed = False
            elif res.status_code == 422:
                print(f"[FAIL] {route} -> 422 Unprocessable Entity (Field Missing)")
                all_passed = False
            else:
                print(f"[WARN] {route} -> HTTP {res.status_code}")
                # Some APIs return 401 if they intentionally block despite session, but our routes should be 200.
                if res.status_code > 399:
                    all_passed = False
        except Exception as e:
            print(f"[FAIL] {route} -> Connection Error: {e}")
            all_passed = False

    return all_passed

if __name__ == "__main__":
    test_routes()
