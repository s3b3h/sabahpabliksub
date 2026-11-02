#!/usr/bin/env python3
"""
Sabah Public Sub - يجلب الكونفيجات من قنوات تيليجرام عامة، يحذف المكرر، يختبر كل
كونفيج اختبار حقيقي (اتصال + انستقرام + سرعة) عن طريق Xray، ويبني اشتراك v2rayNG / Happ.

- من كل قناة: آخر 50 كونفيج (بدون أي فلتر فحص اتصال/إنترنت).
- حذف المكرر: إذا تطابق (UUID/الباسورد + عنوان السيرفر IP + البورت) يُحذف الكونفيج،
  ويُجلب بدله كونفيج أقدم من نفس القناة حتى يكتمل العدد 50. ويشمل الحذف كل القنوات.
- سيرفرات الشادوساكس (ss:// و ssr://) يتم تخطيها.
- علم الدولة عن طريق GeoIP، واختصار القناة في آخر الاسم.
- إذا فشلت قناة، يحتفظ بكونفيجاتها القديمة.

يكتب:
    sub.txt      (Base64 - رابط الاشتراك الكامل)
    sub_raw.txt  (نص عادي)
    subs/<Tag>.txt  (اشتراك منفصل لكل قناة - Base64)

لا يحتاج أي مكتبات خارجية.
"""

import base64
import html
import ipaddress
import itertools
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# ===================== الإعدادات =====================
NAME_PREFIX = "Sabah"
MAX_PAGES = 60          # أقصى عدد صفحات يرجع لها في تاريخ كل قناة
PER_CHANNEL = 50        # عدد الكونفيجات من كل قناة

# كل منبع:
#   channel : اسم القناة (بدون @)
#   count   : عدد الكونفيجات
#   start   : رقم أول كونفيج
#   tag     : اختصار قصير يظهر في آخر الاسم (حروف/أرقام إنجليزية فقط)
#   fallback_emoji : إيموجي بديل إذا ما عُرف علم الدولة
# لإضافة قناة جديدة: انسخ سطر وغيّر channel و tag و start (start = آخر رقم + 50)
SOURCES = [
    {"channel": "TheFreeConfigs", "count": PER_CHANNEL, "start": 1,   "tag": "Free",
     "fallback_emoji": "🌐"},
    {"channel": "tootffarangi2", "count": PER_CHANNEL, "start": 51,  "tag": "Toot",
     "fallback_emoji": "🍓"},
    {"channel": "v2rei", "count": PER_CHANNEL, "start": 101, "tag": "V2rei",
     "fallback_emoji": "⚡"},
    {"channel": "proxy_mtproto_vpns_free", "count": PER_CHANNEL, "start": 151, "tag": "Proxy",
     "fallback_emoji": "🔰", "max_pages": 80},
    {"channel": "ettehad_vpn", "count": PER_CHANNEL, "start": 201, "tag": "Etehad",
     "fallback_emoji": "🔥"},
    {"channel": "DailyCfg", "count": PER_CHANNEL, "start": 251, "tag": "Daily",
     "fallback_emoji": "📅"},
]

# بروتوكولات يتم تخطيها تماماً
SKIP_PROTOCOLS = {"ss", "ssr"}

USE_GEOIP = True
RELAY_COUNTRIES = {"IR"}
SKIP_PRIVATE_HOSTS = True   # تخطي العناوين الوهمية (127.0.0.1 ، 10.x ...). خليها False لإلغائه
WRITE_PER_CHANNEL = True
PAGE_DELAY = 0.4
# =====================================================

PROTOCOLS = ["hysteria2", "hysteria", "wireguard", "vless", "vmess",
             "trojan", "tuic", "ssr", "hy2", "ss"]

LINK_RE = re.compile(r'(?<![A-Za-z0-9])(?:%s)://[^\s<>"\'`]+' % "|".join(PROTOCOLS),
                     re.IGNORECASE)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.IGNORECASE)
MT_RE = re.compile(r'(?:tg://proxy|t\.me/proxy|tg://socks|t\.me/socks)\?', re.IGNORECASE)
FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
NUM_RE = re.compile(r"^%s\s+(\d+)\b" % re.escape(NAME_PREFIX))
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TAGS = {s["tag"] for s in SOURCES}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

CDN_WORDS = ("cloudflare", "fastly", "akamai", "cloudfront", "gcore", "g-core",
             "cdn77", "bunny", "edgecast", "stackpath", "arvancloud", "imperva",
             "incapsula", "sucuri")


# ----------------------------------------------------------------- أدوات عامة
def fetch(url, data=None, timeout=30, retries=3):
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise
            time.sleep((10 if e.code == 429 else 3) * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def html_to_text(fragment):
    fragment = re.sub(r"<br\s*/?>|</(?:div|p|pre|code|blockquote)>", "\n",
                      fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment)


def b64decode_any(s):
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8")


def get_flag(name):
    m = FLAG_RE.search(name or "")
    return m.group(0) if m else ""


def cc_to_flag(cc):
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc)


def norm_host(h):
    return (h or "").strip().strip("[]").rstrip(".").lower()


def norm_user(u):
    """UUID يتحول لحروف صغيرة. الباسوردات تبقى كما هي."""
    u = (u or "").strip()
    return u.lower() if UUID_RE.match(u) else u


def make_key(user, host, port):
    """مفتاح كشف التكرار: (UUID/باسورد) + (IP/دومين) + البورت."""
    return f"{norm_user(user)}|{norm_host(host)}|{str(port).strip()}"


def is_bad_host(host):
    if not SKIP_PRIVATE_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("localhost", "example.com") or host.endswith((".local", ".localhost"))
    return not ip.is_global


def is_skipped(link):
    return link.split("://", 1)[0].lower() in SKIP_PROTOCOLS


# ----------------------------------------------------------- تحليل الكونفيج
def parse_link(link):
    """
    يرجع dict فيه: key (للتكرار) ، host ، name
    أو None إذا الرابط غير صالح.
    """
    try:
        scheme = link.split("://", 1)[0].lower()
        body = link.split("://", 1)[1]
    except IndexError:
        return None
    fam = "hysteria2" if scheme in ("hy2", "hysteria2") else scheme

    if scheme == "vmess":
        try:
            d = json.loads(b64decode_any(body.split("#")[0]))
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        host = norm_host(str(d.get("add", "")))
        port = str(d.get("port", "")).strip()
        uid = str(d.get("id", "")).strip()
        if not host or not port or not uid:
            return None
        return {"key": make_key(uid, host, port), "host": host,
                "name": str(d.get("ps", ""))}

    base, _, frag = link.partition("#")
    name = urllib.parse.unquote(frag)

    # ---------- shadowsocks
    if scheme == "ss":
        rest = base.split("://", 1)[1].split("?")[0].split("/")[0]
        if "@" not in rest:
            try:
                rest = b64decode_any(urllib.parse.unquote(rest))
            except Exception:
                return None
        userinfo, _, hostport = rest.rpartition("@")
        userinfo = urllib.parse.unquote(userinfo)
        if ":" not in userinfo:
            try:
                userinfo = b64decode_any(userinfo)
            except Exception:
                return None
        try:
            u = urllib.parse.urlsplit(base)
            host, port = norm_host(u.hostname), u.port
        except Exception:
            return None
        if not host or not port:
            return None
        return {"key": make_key(userinfo, host, port), "host": host, "name": name}

    # ---------- vless / trojan / hysteria / tuic / ... (صيغة URL عادية)
    try:
        u = urllib.parse.urlsplit(base)
        host, port = norm_host(u.hostname), u.port
    except Exception:
        return None
    if not host:
        return None

    user = urllib.parse.unquote(u.username or "")
    if u.password:                       # مثل tuic: uuid:password
        user += ":" + urllib.parse.unquote(u.password)
    if scheme in ("vless", "trojan") and not user:
        return None
    q = {k.lower(): v for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)}
    if not user:                         # بعض أنواع hysteria تضع الباسورد في الباراميترات
        user = q.get("auth", "") or q.get("password", "")

    return {"key": make_key(user, host, port or ""), "host": host, "name": name}


def is_skipped(link):
    return link.split("://", 1)[0].lower() in SKIP_PROTOCOLS


def rename(link, new_name):
    scheme = link.split("://", 1)[0].lower()
    if scheme == "vmess":
        data = json.loads(b64decode_any(link.split("://", 1)[1].split("#")[0]))
        data["ps"] = new_name
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return "vmess://" + base64.b64encode(raw).decode()
    return link.split("#", 1)[0] + "#" + urllib.parse.quote(new_name)


def make_name(num, flag, tag):
    return " ".join(x for x in (NAME_PREFIX, str(num), flag, tag) if x)


# --------------------------------------------------------------- جلب القنوات
def parse_page(page_html, channel):
    post_re = re.compile(r'data-post="%s/(\d+)"' % re.escape(channel), re.IGNORECASE)
    messages = []
    matches = list(post_re.finditer(page_html))
    messages, real = [], ""
    for i, m in enumerate(matches):
        real = real or m.group(1)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page_html)
        block = page_html[m.end():end]
        links = [l.rstrip(".,;)]}") for l in LINK_RE.findall(html_to_text(block))]
        for href in HREF_RE.findall(block):
            href = html.unescape(href).strip()
            if LINK_RE.match(href):
                links.append(href.rstrip(".,;)]}"))
        links = list(dict.fromkeys(links))
        t = TIME_RE.search(block)
        messages.append((int(m.group(2)), parse_date(t.group(1)) if t else 0.0,
                         links, len(MT_RE.findall(html.unescape(block)))))
    return messages, real


def collect(source, seen):
    """
    يجمع أحدث الكونفيجات غير المكررة من قناة واحدة.
    أي كونفيج مكرر (نفس UUID + IP + بورت) يُتخطى ويُكمل بالأقدم حتى يكتمل العدد.
    """
    channel, limit = source["channel"], source["count"]
    max_pages = source.get("max_pages", MAX_PAGES)
    result = []
    url = f"https://t.me/s/{channel}"

    for page in range(max_pages):
        try:
            page_html = fetch(url0)
        except Exception as e:
            if result:
                print(f"  @{channel}: توقف عند الصفحة {page + 1} ({e})")
                break
        st["pages"] += 1
        messages, _ = parse_page(page_html)
        if not messages:
            break

        for _msg_id, links in sorted(messages, key=lambda x: -x[0]):
            for link in links:
                if is_skipped(link):
                    st["ss"] += 1
                    continue
                info = parse_link(link)
                if not info:
                    st["bad"] += 1
                    continue
                if is_bad_host(info["host"]):
                    st["private"] += 1
                    continue
                k = f"{info['fam']}|{info['user']}|{info['host']}|{info['port']}"
                if k in seen:
                    st["dup"] += 1
                    continue
                seen.add(k)
                info.update(link=link, date=date, msg_id=msg_id, pos=pos, src=src_idx)
                result.append(info)
                if len(result) >= limit:
                    return result

        oldest = min(m[0] for m in messages)
        if oldest <= 1:
            break
        url = f"{base}?before={oldest}"
        time.sleep(PAGE_DELAY)

    print(f"  [{channel}] صفحات={st['pages']} رسائل={st['msgs']} روابط={st['raw']} | "
          f"مكرر={st['dup']} ss={st['ss']} غير_صالح={st['bad']} داخلي={st['private']} | "
          f"MTProto={st['mtproto']} | مرشح={len(result)}")
    return result


def load_previous(src_idx, tag):
    """الكونفيجات القديمة لقناة معينة من sub_raw.txt (تُستخدم إذا فشل جلب القناة)."""
    out = []
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
        return host
    except OSError:
        pass
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except Exception:
        return None


def geoip(hosts):
    socket.setdefaulttimeout(5)
    hosts = sorted({c["host"] for c in cands})
    with ThreadPoolExecutor(max_workers=32) as ex:
        ips = dict(zip(hosts, ex.map(resolve, hosts)))
    unique_ips = sorted({ip for ip in ips.values() if ip})

    info = {}
    for n, i in enumerate(range(0, len(unique_ips), 100)):
        chunk = unique_ips[i:i + 100]
        if n:
            time.sleep(4.5)
        try:
            res = json.loads(fetch(
                "http://ip-api.com/batch?fields=status,countryCode,isp,org,as,query",
                data=json.dumps(chunk).encode(), timeout=20))
        except Exception as e:
            print("  تحذير: فشل GeoIP:", e)
            continue
        for r in res:
            if r.get("status") != "success":
                continue
            blob = " ".join(str(r.get(k, "")) for k in ("isp", "org", "as")).lower()
            info[r["query"]] = (r.get("countryCode", ""),
                                any(w in blob for w in CDN_WORDS))
    return {h: info.get(ip) for h, ip in ips.items() if ip and ip in info}


def choose_flag(cfg, geo, fallback):
    original = get_flag(cfg["name"])
    g = geo.get(cfg["host"])
    if g and g[0] and not g[1]:
        cc = g[0].upper()
        if cc in RELAY_COUNTRIES and original and flag_to_cc(original) != cc:
            return original
        return cc_to_flag(cc) or original or fallback
    return original or fallback


# -------------------------------------------------- الاحتفاظ بالقديم عند الفشل
def load_previous():
    """يرجع [(رقم, اختصار_القناة, العلم, رابط), ...] من sub_raw.txt القديم."""
    old = []
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        code, t, sp, size = r.stdout.split()
        return int(code), float(t), float(sp), int(float(size))
    except Exception:
        return 0, 0.0, 0.0, 0


def kept_for_source(src, previous):
    kept = [(flag, link) for num, tag, flag, link in sorted(previous) if tag == src["tag"]]
    out = []
    for i, (flag, link) in enumerate(kept[:src["count"]]):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def test_one(c, workdir):
    """يرجع dict: ok, reason, delay, speed, cc, exit_ip"""
    res = {"ok": False, "reason": "", "delay": 0, "speed": 0.0, "cc": "", "exit_ip": "", "ig": False}
    if (SKIP_UNENCRYPTED and c["scheme"] in ("vless", "trojan") and c["sec"] in ("", "none")
            and c["p"].get("encryption", "none") in ("", "none")):
        res["reason"] = "no_tls"
        return res
    ob = build_outbound(c)
    if ob is None:
        res["reason"] = "unsupported"
        return res
    port = next_port()
    cfg = {"log": {"loglevel": "none"},
           "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                         "settings": {"udp": False}}],
           "outbounds": [ob]}
    cfg_path = os.path.join(workdir, f"{port}.json")
    body_path = os.path.join(workdir, f"{port}.html")
    with open(cfg_path, "w") as f:
        json.dump(cfg, f)
    proc = subprocess.Popen([XRAY_PATH, "run", "-c", cfg_path],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(port, proc):
            res["reason"] = "invalid"
            return res
        # 1) Delay حقيقي (محاولتين لأن السيرفرات المجانية متذبذبة)
        delay = 0
        for _ in range(2):
            code, t, _, _ = curl(port, DELAY_URL, DELAY_TIMEOUT)
            if code in (200, 204):
                delay = int(t * 1000)
                break
        if not delay:
            res["reason"] = "dead"
            return res
        res["delay"] = delay
        if delay > MAX_DELAY_MS:
            res["reason"] = "high_delay"
            return res
        # 2) انستقرام
        code, _, _, size = curl(port, INSTAGRAM_URLS[0], INSTAGRAM_TIMEOUT, out=body_path)
        ok_web = False
        if 200 <= code < 400:
            try:
                with open(body_path, "rb") as f:
                    ok_web = code >= 300 or b"instagram" in f.read().lower()
            except OSError:
                pass
        ok_api = ok_web and all(curl(port, u, INSTAGRAM_TIMEOUT)[0] > 0 for u in INSTAGRAM_URLS[1:])
        res["ig"] = ok_web and ok_api
        if REQUIRE_INSTAGRAM and not res["ig"]:
            res["reason"] = "no_instagram"
            return res
        # 3) السرعة
        for url in SPEED_URLS:
            code, _, sp, size = curl(port, url, SPEED_TIMEOUT)
            if size > 100_000:
                res["speed"] = round(sp * 8 / 1_000_000, 2)
                break
        if MIN_SPEED_MBPS and res["speed"] < MIN_SPEED_MBPS:
            res["reason"] = "slow"
            return res
        # 4) دولة و IP الخروج الحقيقي
        for url in EXIT_INFO_URLS:
            code, _, _, _ = curl(port, url, 8, out=body_path)
            if code != 200:
                continue
            try:
                with open(body_path, encoding="utf-8", errors="replace") as f:
                    data = f.read()
            except OSError:
                continue
            if "cdn-cgi/trace" in url:
                kv = dict(l.split("=", 1) for l in data.splitlines() if "=" in l)
                res["exit_ip"], res["cc"] = kv.get("ip", ""), kv.get("loc", "")
            else:
                try:
                    j = json.loads(data)
                    res["exit_ip"], res["cc"] = j.get("ip", ""), j.get("country", "")
                except Exception:
                    pass
            if res["exit_ip"]:
                break
        if res["cc"] in ("XX", "T1"):
            res["cc"] = ""
        res["ok"] = True
        return res
    finally:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        for pth in (cfg_path, body_path):
            try:
                os.remove(pth)
            except OSError:
                pass


def test_all(cands):
    workdir = tempfile.mkdtemp(prefix="xtest_")
    done = [0]
    lock = threading.Lock()
    t0 = time.time()

    def run(c):
        try:
            c["test"] = test_one(c, workdir)
        except Exception as e:
            c["test"] = {"ok": False, "reason": f"error:{e}", "delay": 0, "speed": 0.0,
                         "cc": "", "exit_ip": "", "ig": False}
        with lock:
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == len(cands):
                ok = sum(1 for x in cands if x.get("test", {}).get("ok"))
                print(f"  اختبار: {done[0]}/{len(cands)}  ناجح={ok}  ({time.time() - t0:.0f}s)")

    with ThreadPoolExecutor(max_workers=TEST_WORKERS) as ex:
        list(ex.map(run, cands))


# ------------------------------------------------------------------ main
def write_sub(path, lines):
    raw = "\n".join(lines) + "\n" if lines else ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(raw.encode("utf-8")).decode())


def check_xray():
    try:
        r = subprocess.run([XRAY_PATH, "version"], capture_output=True, text=True, timeout=10)
        print("Xray:", r.stdout.splitlines()[0])
        return True
    except Exception as e:
        print(f"خطأ: ما لقيت xray في {XRAY_PATH} ({e})")
        return False


def main():
    if ENABLE_TEST and not check_xray():
        sys.exit(1)

    # 1) الجلب
    cands, fetched = [], {}
    for i, src in enumerate(SOURCES):
        t0 = time.time()
        try:
            got = collect(i, src)
        except Exception as e:
            print(f"@{src['channel']}: خطأ أثناء الجلب -> {e}")
            got = []
        if not got:
            got = load_previous(i, src["tag"])
            print(f"@{src['channel']}: فشل الجلب، إعادة اختبار {len(got)} كونفيج قديم")
        else:
            print(f"@{src['channel']}: {len(got)} مرشح ({time.time() - t0:.0f}s)")
        fetched[src["tag"]] = len(got)
        cands.extend(got)
    if not cands:
        print("لم يتم العثور على أي كونفيج - لن يتم تعديل الملفات.")
        sys.exit(1)

    # 2) حذف المكرر عبر كل القنوات
    cands, dups = dedupe(cands)
    print(f"بعد حذف المكرر: {len(cands)} (محذوف {dups} مكرر)")

    previous = None
    final = []
    by_tag = {}
    for src, cfgs in zip(SOURCES, per_source):
        lines = []
        if not cfgs:
            if previous is None:
                previous = load_previous()
            lines = kept_for_source(src, previous)
            print(f"@{src['channel']}: الاحتفاظ بـ {len(lines)} كونفيج قديم")
        else:
            for i, cfg in enumerate(cfgs):
                flag = choose_flag(cfg, geo, src["fallback_emoji"])
                try:
                    lines.append(rename(cfg["link"],
                                        make_name(src["start"] + i, flag, src["tag"])))
                except Exception:
                    continue
            keep.append(c)
        print(f"حذف مكرر IP الخروج: {len(passed) - len(keep)}")
        passed = keep

    # 5) الترتيب: الأحدث = رقم 1 ، مع حد لكل قناة
    passed.sort(key=sort_key)
    per_src, final_cfgs = {}, []
    for c in passed:
        n = per_src.get(c["src"], 0)
        if n >= SOURCES[c["src"]]["count"]:
            continue
        per_src[c["src"]] = n + 1
        final_cfgs.append(c)
    if MAX_TOTAL:
        final_cfgs = final_cfgs[:MAX_TOTAL]
    if not final_cfgs:
        print("ولا كونفيج نجح في الاختبار - لن يتم تعديل الملفات.")
        sys.exit(1)

    # 6) التسمية والكتابة
    final, by_tag, rows = [], {s["tag"]: [] for s in SOURCES}, []
    for num, c in enumerate(final_cfgs, 1):
        src = SOURCES[c["src"]]
        flag = cc_to_flag(c["test"]["cc"]) or get_flag(c["name"]) or src["fallback_emoji"]
        try:
            line = rename(c["link"], make_name(num, flag, src["tag"]))
        except Exception:
            continue
        final.append(line)
        by_tag[src["tag"]].append(line)
        rows.append((num, flag, src["tag"], c["scheme"], c["net"], c["sec"] or "none",
                     c["test"]["delay"], c["test"]["speed"],
                     datetime.fromtimestamp(c["date"], timezone.utc).strftime("%m-%d %H:%M") if c["date"] else "-"))

    with open("sub_raw.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(final) + "\n")
    write_sub("sub.txt", final)
    if WRITE_PER_CHANNEL:
        os.makedirs("subs", exist_ok=True)
        for tag, lines in by_tag.items():
            if lines:
                write_sub(os.path.join("subs", f"{tag}.txt"), lines)

    print(f"تم: {len(final)} كونفيج شغال")
    for tag, lines in by_tag.items():
        if lines:
            print(f"  [{tag}] {len(lines)}")


if __name__ == "__main__":
    main()
