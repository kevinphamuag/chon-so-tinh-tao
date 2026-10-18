"""The owner's bought tickets, kept encrypted in data/tickets.enc, and result notifications.

Tickets arrive from the phone: an iOS Shortcut forwards each 9969 confirmation SMS to
this repo as a repository_dispatch event (client_payload.sms). Only game, draw and numbers
are kept; the file is AES-encrypted with a key derived from the GEMINI_API_KEY secret,
so the public repo never shows which tickets were bought.

Usage:
  python scripts/tickets.py add        # reads client_payload.sms of the repository_dispatch event
  python scripts/tickets.py list       # prints the stored tickets (for local debugging)
"""
import datetime as dt
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "data", "tickets.enc")
N = {"645": 45, "655": 55}
DAYS = {"645": {2, 4, 6}, "655": {1, 3, 5}}  # Python weekday(): Mon=0


def _passphrase():
    seed = os.environ.get("TICKETS_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not seed:
        return None
    return hashlib.sha256(("chon-so-tinh-tao/" + seed).encode()).hexdigest()


def _openssl(args, data, key):
    env = dict(os.environ, TK=key)
    return subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:TK"] + args,
                          input=data, capture_output=True, env=env, check=True).stdout


def load():
    key = _passphrase()
    if not key or not os.path.exists(STORE):
        return []
    try:
        return json.loads(_openssl(["-d", "-a", "-A"], open(STORE, "rb").read(), key))
    except Exception:
        print("Không giải mã được danh sách vé (khóa đã đổi?) - bắt đầu danh sách mới.")
        return []


def save(tickets):
    key = _passphrase()
    if not key:
        raise SystemExit("Thiếu khóa để mã hóa vé (GEMINI_API_KEY hoặc TICKETS_KEY).")
    blob = _openssl(["-e", "-a", "-A", "-salt"], json.dumps(tickets, separators=(",", ":")).encode(), key)
    open(STORE, "wb").write(blob + b"\n")


def parse_sms(text):
    """Extract tickets from 9969 messages: [{'g','ky','date','n'}]. Mirrors parseSMS in index.html."""
    text = str(text or "").replace("\r", "")
    text = re.sub(r"[ \t]*-[ \t]*\n[ \t]*|\n[ \t]*-[ \t]*", "-", text)
    parts = [p for p in re.split(r"(?=Yeu cau dat ve|\(VE\)|(?:^|\n)\s*(?:645|655)\s+[A-Z]\s+\d)", text, flags=re.I) if p.strip()]
    out, seen = [], set()
    for part in parts:
        flat = re.sub(r"\s+", " ", part)
        if re.search(r"SP\s*:?\s*(645|Mega)|Mega\s*6\s*/\s*45", flat, re.I):
            g = "645"
        elif re.search(r"SP\s*:?\s*(655|Power)|Power\s*6\s*/\s*55", flat, re.I):
            g = "655"
        else:
            m = re.search(r"(?:^|\s)(645|655)(?=\s|,|$)", flat)
            g = m.group(1) if m else None
        if not g:
            continue
        m = re.search(r"ky\s*QSMT\s*:?\s*0*(\d{1,6})", part, re.I) or re.search(r"(?:^|\s)(?:645|655)\s+[A-Z]\s+0*(\d{3,6})\b", part, re.I)
        ky = int(m.group(1)) if m else None
        m = re.search(r"ngay\s*MT\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", part, re.I)
        date = "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else None
        m = re.search(r"(?:bo\s*so|BSDT)\s*:?\s*([\d\-#\s]+)", part, re.I)
        groups = [[int(x) for x in re.findall(r"\d{1,2}", s)] for s in m.group(1).split("#")] if m else []
        if not groups:
            groups = [[int(x) for x in re.findall(r"\d{1,2}", s)] for s in re.findall(r"\bS\s+((?:\d{1,2}[\s,.\-]+){5,}\d{1,2})\b", part)]
        for raw in groups:
            nums = sorted(set(raw))
            if not (6 <= len(nums) <= 18) or len(nums) != len(raw) or not all(1 <= x <= N[g] for x in nums):
                continue
            k = (g, ky, date, tuple(nums))
            if k in seen:
                continue
            seen.add(k)
            out.append({"g": g, "ky": ky, "date": date, "n": nums})
    return out


def add_from_sms(text):
    found = [t for t in parse_sms(text) if t["ky"] or t["date"]]
    if not found:
        print("Tin nhắn không có vé nào đọc được.")
        return 0
    tickets = load()
    have = {(t["g"], t.get("ky"), t.get("date"), tuple(t["n"])) for t in tickets}
    added = 0
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for t in found:
        k = (t["g"], t["ky"], t["date"], tuple(t["n"]))
        if k not in have:
            tickets.append(dict(t, added=now))
            have.add(k)
            added += 1
    # keep only recent tickets
    cutoff = (dt.date.today() - dt.timedelta(days=60)).isoformat()
    tickets = [t for t in tickets if (t.get("date") or t["added"][:10]) >= cutoff]
    save(tickets)
    print("Đã lưu %d vé mới (tổng %d)." % (added, len(tickets)))
    return added


def prize_of(g, combo, draw):
    """(prize name, fixed value or None for jackpots) for one 6-number combination."""
    main = set(draw[2:8])
    bonus = draw[8] if g == "655" and len(draw) > 8 else None
    m = len(main & set(combo))
    if m == 6:
        return ("Jackpot 1" if g == "655" else "Jackpot"), None
    if m == 5 and g == "655" and bonus and bonus in combo:
        return "Jackpot 2", None
    fixed = {"645": {5: ("Giải nhất", 10_000_000), 4: ("Giải nhì", 300_000), 3: ("Giải ba", 30_000)},
             "655": {5: ("Giải nhất", 40_000_000), 4: ("Giải nhì", 500_000), 3: ("Giải ba", 50_000)}}[g]
    return fixed.get(m, (None, 0))


def money(v):
    return format(int(v), ",").replace(",", ".") + " đ"


def check(draws, meta, notify):
    """Notify the result of every stored ticket whose draw is now in `draws`. Returns True if tickets changed."""
    tickets = load()
    if not tickets:
        return False
    changed = False
    for t in tickets:
        if t.get("notified"):
            continue
        rows = draws.get(t["g"], [])
        d = next((r for r in rows if (t.get("ky") and r[0] == t["ky"]) or (not t.get("ky") and t.get("date") == r[1])), None)
        if not d:
            continue
        main = set(d[2:8])
        hit = sorted(set(t["n"]) & main)
        wins, total, jackpots = {}, 0, []
        for combo in itertools.combinations(t["n"], 6):
            name, value = prize_of(t["g"], combo, d)
            if name:
                wins[name] = wins.get(name, 0) + 1
                if value is None:
                    jackpots.append(name)
                else:
                    total += value
        game = "Mega 6/45" if t["g"] == "645" else "Power 6/55"
        nums = " ".join("%02d" % x for x in t["n"])
        drawn = " ".join("%02d" % x for x in d[2:8]) + (" + %02d" % d[8] if t["g"] == "655" and len(d) > 8 and d[8] else "")
        line = "Vé: %s\nKết quả: %s\nTrùng %d số%s" % (nums, drawn, len(hit), (": " + ", ".join("%02d" % x for x in hit)) if hit else "")
        if t["g"] == "655" and len(d) > 8 and d[8] in t["n"]:
            line += " (có số đặc biệt %02d)" % d[8]
        if wins:
            prize = ", ".join(("%d× " % c if c > 1 else "") + n for n, c in wins.items())
            title = "%s kỳ #%05d: TRÚNG %s!" % (game, d[0], prize)
            parts = ([money(total)] if total else []) + (["giải %s theo giá trị Vietlott công bố" % "/".join(sorted(set(jackpots)))] if jackpots else [])
            line += "\nTiền thưởng: " + " + ".join(parts) + " (trước thuế)"
            line += "\nNhớ kiểm tra và nhận thưởng trên app Vietlott SMS."
            tags = ["tada", "moneybag"]
        else:
            title = "%s kỳ #%05d: vé của bạn không trúng" % (game, d[0])
            tags = ["ticket"]
        if notify(title, line, tags):
            t["notified"] = True
            changed = True
    if changed:
        save(tickets)
    return changed


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "add":
        # read the SMS from the event file rather than an env var, so it never appears in public logs
        event = json.load(open(os.environ["GITHUB_EVENT_PATH"])) if os.environ.get("GITHUB_EVENT_PATH") else {}
        add_from_sms((event.get("client_payload") or {}).get("sms") or os.environ.get("TICKET_SMS", ""))
    elif cmd == "list":
        for t in load():
            print(t)
    else:
        print(__doc__)
