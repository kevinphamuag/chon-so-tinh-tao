"""Fetch new Vietlott Mega 6/45 and Power 6/55 results into data/*.json.

Runs in GitHub Actions. Sources, in order:
  1. vietvudanh/vietlott-data on GitHub (structured, trusted)
  2. Result pages (vietlott.vn, minhngoc, xskt, minhchinh, xsmn...) read by Gemini
  3. Gemini with Google Search
A draw is accepted when a trusted source has it, or when two different
sources agree on the date and all numbers. Needs GEMINI_API_KEY for 2 and 3.

Usage: python scripts/update.py [--probe]
  --probe  fetch every source page and report reachability, even with nothing missing
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAWS = os.path.join(ROOT, "data", "draws.json")
META = os.path.join(ROOT, "data", "meta.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/"
KEY = os.environ.get("GEMINI_API_KEY", "").strip()

GAMES = {
    "645": {"name": "Mega 6/45", "N": 45, "weekdays": {2, 4, 6}, "bonus": False,
            "jsonl": "https://raw.githubusercontent.com/vietvudanh/vietlott-data/master/data/power645.jsonl",
            "pages": ["https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-645",
                      "https://www.minhngoc.net.vn/ket-qua-xo-so/dien-toan-vietlott/mega-6x45.html",
                      "https://xskt.com.vn/xsmega645",
                      "https://www.minhchinh.com/truc-tiep-xo-so-tu-chon-mega-645.html",
                      "https://xsmn.mobi/xs-mega-645.html",
                      "https://www.ketquadientoan.com/"]},
    "655": {"name": "Power 6/55", "N": 55, "weekdays": {1, 3, 5}, "bonus": True,
            "jsonl": "https://raw.githubusercontent.com/vietvudanh/vietlott-data/master/data/power655.jsonl",
            "pages": ["https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-655",
                      "https://www.minhngoc.net.vn/ket-qua-xo-so/dien-toan-vietlott/power-6x55.html",
                      "https://xskt.com.vn/xspower",
                      "https://www.minhchinh.com/truc-tiep-xo-so-tu-chon-power-655.html",
                      "https://xsmn.mobi/xs-power.html",
                      "https://www.ketquadientoan.com/"]},
}
TRUSTED = {"vietvudanh", "vietlott.vn"}
REPORT = []  # rows for the run summary


def log(*a):
    print(*a, flush=True)


def vn_now():
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=7)


def fetch(url, data=None, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.5", "Accept": "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # network error, timeout
        return 0, str(e)


def page_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table|section)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in raw.split("\n")]
    return "\n".join(l for l in lines if l)


def is_blocked(status, raw):
    return status != 200 or "challenges.cloudflare.com" in raw or "cf-mitigated" in raw or "Just a moment" in raw


# ---------- Gemini ----------
_model = None


def gemini_model():
    global _model
    if _model:
        return _model
    _model = os.environ.get("GEMINI_MODEL", "").strip()
    if _model:
        return _model
    st, body = fetch(GEMINI + "models?pageSize=200", headers={"x-goog-api-key": KEY})
    if st != 200:
        raise RuntimeError("Gemini models list failed: %s %s" % (st, body[:200]))
    names = [m["name"].split("/", 1)[1] for m in json.loads(body).get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]

    def score(n):
        m = re.match(r"^gemini-(\d+(?:\.\d+)?)-flash(?:-latest)?$", n)
        return float(m.group(1)) if m else -1

    ranked = sorted([n for n in names if score(n) >= 0], key=score, reverse=True)
    bad = re.compile(r"image|tts|audio|live|embedding|exp|preview|lite")
    _model = (ranked or [n for n in names if "flash" in n and not bad.search(n)] or names)[0]
    log("Gemini model:", _model)
    return _model


def gemini(prompt, search=False):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0}}
    if search:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
    st, out = fetch(GEMINI + "models/%s:generateContent" % gemini_model(), data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "x-goog-api-key": KEY}, timeout=120)
    if st != 200:
        raise RuntimeError("Gemini %s: %s" % (st, out[:300]))
    cand = (json.loads(out).get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
    m = re.search(r"\[.*\]|\{.*\}", text, re.S)
    if not m:
        return []
    try:
        val = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return val if isinstance(val, list) else [val] if isinstance(val, dict) else []


EXTRACT = """Dưới đây là văn bản lấy từ trang web {url}.
Liệt kê TẤT CẢ các kỳ quay xổ số {name} xuất hiện trong văn bản mà có ghi rõ đủ các số trúng thưởng.
Trả về một mảng JSON, mỗi phần tử có dạng:
{{"id": số kỳ quay (số nguyên, ví dụ 1570), "date": "YYYY-MM-DD", "numbers": [6 số chính], "bonus": {bonus}, "jackpot": giá trị giải Jackpot{j1} của kỳ đó tính bằng đồng (số nguyên) nếu văn bản có ghi, nếu không thì null}}
Chỉ chép đúng những gì văn bản ghi, không suy đoán. Nếu không có kỳ nào, trả về [].

VĂN BẢN:
\"\"\"
{text}
\"\"\""""

SEARCH = """Tìm trên Google kết quả xổ số Vietlott {name} kỳ quay #{id:05d} (dự kiến ngày {date}).
Trả lời CHỈ bằng một đối tượng JSON, không thêm chữ nào khác:
{{"id": {id}, "date": "YYYY-MM-DD", "numbers": [6 số chính], "bonus": {bonus}, "jackpot": giá trị Jackpot{j1} của kỳ đó tính bằng đồng hoặc null}}
Nếu không tìm thấy kết quả chính xác của đúng kỳ này, trả lời: null"""


def norm(g, item, source):
    """Validate one extracted draw; return (id, date, nums, bonus, jackpot) or None."""
    try:
        i = int(str(item.get("id")).lstrip("#"))
        d = dt.date.fromisoformat(str(item.get("date"))[:10])
        nums = sorted(int(x) for x in item.get("numbers") or [])
        b = item.get("bonus")
        b = int(b) if b not in (None, "", "null") else None
        j = item.get("jackpot")
        j = int(float(str(j).replace(".", "").replace(",", ""))) if j not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None
    G = GAMES[g]
    if len(nums) != 6 or len(set(nums)) != 6 or not all(1 <= x <= G["N"] for x in nums):
        return None
    if d.weekday() not in G["weekdays"]:
        return None
    if G["bonus"] and (b is None or not 1 <= b <= G["N"] or b in nums):
        return None
    if not G["bonus"]:
        b = None
    if j is not None and not (1e9 <= j <= 1e13):
        j = None
    return (i, d.isoformat(), tuple(nums), b, j, source)


def expected_dates(g, last_date):
    """Scheduled draw dates after last_date whose results should exist by now."""
    now = vn_now()
    d, out = dt.date.fromisoformat(last_date) + dt.timedelta(days=1), []
    while d <= now.date():
        if d.weekday() in GAMES[g]["weekdays"] and (d < now.date() or now.hour * 60 + now.minute >= 18 * 60 + 45):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def main():
    probe = "--probe" in sys.argv
    draws = json.load(open(DRAWS))
    meta = json.load(open(META)) if os.path.exists(META) else {}
    changed = False

    for g, G in GAMES.items():
        rows = draws[g]
        last_id, last_date = rows[-1][0], rows[-1][1]
        exp = expected_dates(g, last_date)
        log("\n== %s: có đến kỳ #%d (%s); đang thiếu %d kỳ: %s" % (G["name"], last_id, last_date, len(exp), exp))
        if not exp and not probe:
            continue
        cands = {}  # id -> list of normalized candidates

        def add(item, source):
            n = norm(g, item, source)
            if n and n[0] > last_id:
                cands.setdefault(n[0], []).append(n)
                return n
            return None

        # 1) structured GitHub dataset
        st, body = fetch(G["jsonl"])
        got = 0
        if st == 200:
            for line in body.splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                res = r.get("result") or []
                item = {"id": r.get("id"), "date": r.get("date"), "numbers": res[:6], "bonus": res[6] if len(res) > 6 else None}
                if add(item, "vietvudanh"):
                    got += 1
        REPORT.append((G["name"], "github vietvudanh", st, len(body), "", got))

        # 2) result pages read by Gemini
        need = lambda: [i for i in range(last_id + 1, last_id + 1 + len(exp)) if not any(c[5] in TRUSTED for c in cands.get(i, []))]
        if need() or probe:
            for url in G["pages"]:
                st, raw = fetch(url)
                blocked = is_blocked(st, raw)
                text = "" if blocked else page_text(raw)
                note = "bị chặn" if blocked else ""
                got = 0
                if not blocked and KEY and (need() or probe):
                    try:
                        items = gemini(EXTRACT.format(url=url, name=G["name"], text=text[:60000],
                                                      bonus="số đặc biệt (số nguyên)" if G["bonus"] else "null",
                                                      j1=" 1" if G["bonus"] else ""))
                        host = urllib.parse.urlparse(url).netloc.replace("www.", "")
                        got = sum(1 for it in items if isinstance(it, dict) and add(it, host))
                    except Exception as e:
                        note = "lỗi Gemini: %s" % str(e)[:80]
                elif not blocked and not KEY:
                    note = "chưa có GEMINI_API_KEY"
                REPORT.append((G["name"], url, st, len(raw), note, got))

        # 3) Gemini + Google Search for draws still unconfirmed
        if KEY:
            for k, i in enumerate(range(last_id + 1, last_id + 1 + len(exp))):
                groups = {}
                for c in cands.get(i, []):
                    groups.setdefault(c[1:4], set()).add(c[5])
                if any(c[5] in TRUSTED for c in cands.get(i, [])) or any(len(s) >= 2 for s in groups.values()):
                    continue
                try:
                    items = gemini(SEARCH.format(name=G["name"], id=i, date=exp[k],
                                                 bonus="số đặc biệt (số nguyên)" if G["bonus"] else "null",
                                                 j1=" 1" if G["bonus"] else ""), search=True)
                    got = sum(1 for it in items if isinstance(it, dict) and add(it, "google-search"))
                    REPORT.append((G["name"], "Gemini + Google Search #%d" % i, 200, 0, "", got))
                except Exception as e:
                    REPORT.append((G["name"], "Gemini + Google Search #%d" % i, 0, 0, str(e)[:80], 0))

        # accept consecutive draws only
        nxt, newest = last_id + 1, None
        while nxt in cands:
            cs = cands[nxt]
            trusted = [c for c in cs if c[5] in TRUSTED]
            groups = {}
            for c in cs:
                groups.setdefault(c[1:4], set()).add(c[5])
            pick = trusted[0][1:4] if trusted else next((k for k, s in groups.items() if len(s) >= 2), None)
            if not pick:
                log("  Kỳ #%d: chưa đủ nguồn khớp nhau -> bỏ qua lần này. Ứng viên: %s" % (nxt, [(c[5], c[1], c[2], c[3]) for c in cs]))
                break
            date, nums, b = pick
            if date <= rows[-1][1]:
                log("  Kỳ #%d: ngày %s không sau kỳ trước -> bỏ qua" % (nxt, date))
                break
            row = [nxt, date] + list(nums) + ([b] if G["bonus"] else [])
            rows.append(row)
            changed = True
            newest = nxt
            log("  + Thêm kỳ #%d %s %s%s (nguồn: %s)" % (nxt, date, list(nums), " + %d" % b if b else "",
                                                    ", ".join(sorted(groups.get(pick, set())))))
            nxt += 1

        # jackpot of the newest draw we know about
        jid = newest or rows[-1][0]
        vals = [c[4] for c in cands.get(jid, []) if c[4] and c[1:4] == tuple([rows[-1][1], tuple(rows[-1][2:8]), rows[-1][8] if G["bonus"] else None])]
        if vals:
            best = max(set(vals), key=vals.count)
            if meta.get("jackpot" + g) != best:
                meta["jackpot" + g] = best
                meta["jackpotAt"] = rows[-1][1]
                changed = True
                log("  Jackpot %s: %s đ" % (G["name"], format(best, ",")))

    if changed:
        meta["updatedAt"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta.pop("notes", None)
        json.dump(draws, open(DRAWS, "w"), separators=(",", ":"))
        json.dump(meta, open(META, "w"), ensure_ascii=False, indent=1)

    lines = ["| Trò chơi | Nguồn | HTTP | Byte | Ghi chú | Số kỳ đọc được |", "|---|---|---|---|---|---|"]
    lines += ["| %s | %s | %s | %s | %s | %s |" % r for r in REPORT]
    lines.append("\n" + ("Đã cập nhật dữ liệu." if changed else "Không có gì mới."))
    summary = "\n".join(lines)
    log("\n" + summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(summary + "\n")


if __name__ == "__main__":
    main()
