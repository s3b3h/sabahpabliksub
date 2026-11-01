#!/usr/bin/env python3
"""
Sabah Public Sub - يجلب الكونفيجات من قنوات تيليجرام عامة، يحذف المكرر، يختبر كل
كونفيج اختبار حقيقي (اتصال + انستقرام + سرعة) عن طريق Xray، ويبني اشتراك v2rayNG / Happ.

الجديد في هذه النسخة:
  1) اختبار حقيقي لكل كونفيج عبر Xray-core (نفس محرك v2rayNG):
       - Delay حقيقي (طلب فعلي عبر السيرفر، مو مجرد بينج)
       - انستقرام: يفتح www.instagram.com و i.instagram.com عبر السيرفر
       - سرعة تحميل فعلية (يحمّل ملف ويقيس الميجابت)
     أي كونفيج ميت / بطيء / ما يفتح انستقرام ما يدخل الاشتراك.
  2) حذف مكرر أقوى:
       - نفس UUID/الباسورد + نفس IP (حتى لو البورت أو الدومين مختلف)
       - نفس IP + نفس البورت (نفس السيرفر حتى لو UUID مختلف)
       - نسخ نفس الكونفيج خلف كلاود فلير بـ IPات مختلفة (نفس UUID + نفس الدومين + نفس المسار)
       - بعد الاختبار: نفس IP الخروج الحقيقي = نفس السيرفر -> نخلي الأسرع فقط
  3) الترقيم حسب الأحدث: آخر كونفيج انحط في أي قناة = Sabah 1 ، اللي قبله = Sabah 2 ...
     (مو شرط ترتيب القنوات ورا بعض).
  4) العلم = دولة الخروج الحقيقية (من الاختبار نفسه) بدل تخمين GeoIP لعنوان الدخول.

شكل الاسم:  Sabah 1 🇩🇪 V2rei   (رقم + علم دولة الخروج + اختصار القناة)
لا يحتاج مكتبات بايثون خارجية. يحتاج ملف xray (يتم تنزيله في GitHub Actions) و curl.
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
MAX_PAGES = 60                 # أقصى عدد صفحات يرجع لها في تاريخ كل قناة

# كل منبع:
#   channel        : اسم القناة (بدون @)
#   count          : أقصى عدد كونفيجات *شغالة* ناخذها من هذه القناة
#   tag            : اختصار يظهر في آخر الاسم (إنجليزي بدون مسافات)
#   fallback_emoji : إيموجي بديل إذا ما عُرف علم الدولة
#   max_pages      : (اختياري) أقصى عدد صفحات لهذه القناة
SOURCES = [
    {"channel": "TheFreeConfigs", "count": 50, "tag": "Free", "fallback_emoji": "🌐"},
    {"channel": "tootffarangi2", "count": 100, "tag": "Toot", "fallback_emoji": "🍓"},
    {"channel": "v2rei", "count": 100, "tag": "V2rei", "fallback_emoji": "⚡"},
    {"channel": "proxy_mtproto_vpns_free", "count": 100, "tag": "Proxy",
     "fallback_emoji": "🔰", "max_pages": 80},
    {"channel": "ettehad_vpn", "count": 100, "tag": "Etehad", "fallback_emoji": "🔥"},
]

# كم كونفيج نجلب من كل قناة للاختبار = count × هذا الرقم
# (لأن كثير منها بيفشل في الاختبار، نجلب أكثر من المطلوب)
CANDIDATE_MULTIPLIER = 2
MAX_TOTAL = 0                  # حد أعلى للاشتراك كامل (0 = بدون حد)

SKIP_PROTOCOLS = {"ss", "ssr"}  # شادوساكس يتم تخطيه
SKIP_PRIVATE_HOSTS = True      # تخطي 127.0.0.1 و 10.x ... إلخ
WRITE_PER_CHANNEL = True       # اشتراك منفصل لكل قناة في subs/
PAGE_DELAY = 0.4               # ثواني بين الصفحات (حتى ما يحظرنا تيليجرام)
MIRRORS = ("t.me", "telegram.me")

# ---------- حذف المكرر
DEDUPE_CDN_VARIANTS = True     # نفس الكونفيج خلف كلاود فلير بـ IPات مختلفة = مكرر
DEDUPE_SAME_IP_PORT = True     # نفس IP + نفس البورت = نفس السيرفر (حتى لو UUID مختلف)
DEDUPE_BY_EXIT_IP = True       # بعد الاختبار: نفس IP الخروج = نفس السيرفر
MAX_PER_EXIT_IP = 1            # كم كونفيج نخلي لكل IP خروج (نختار الأسرع)

# ---------- الاختبار الحقيقي
ENABLE_TEST = True
XRAY_PATH = os.environ.get("XRAY_PATH", "./xray-bin/xray")
TEST_WORKERS = 32              # كم كونفيج يختبر بنفس الوقت
DELAY_URL = "https://www.gstatic.com/generate_204"
DELAY_TIMEOUT = 6
MAX_DELAY_MS = 3000            # أبطأ من كذا = يُحذف

REQUIRE_INSTAGRAM = True       # True = لازم يفتح انستقرام وإلا يُحذف
INSTAGRAM_URLS = ("https://www.instagram.com/", "https://i.instagram.com/")
INSTAGRAM_TIMEOUT = 12

# ملف تحميل لقياس السرعة (لو الأول فشل يجرب الثاني)
SPEED_URLS = ("https://speed.cloudflare.com/__down?bytes=5000000",
              "https://proof.ovh.net/files/10Mb.dat")
SPEED_TIMEOUT = 10             # ثواني لكل اختبار سرعة
MIN_SPEED_MBPS = 2.0           # أقل سرعة مقبولة (ميجابت/ثانية). 0 = بدون شرط سرعة

EXIT_INFO_URLS = ("https://www.cloudflare.com/cdn-cgi/trace", "https://ipinfo.io/json")

# كونفيجات vless/trojan بدون تشفير (security=none) لسيرفر عام:
# Xray من نسخة 26.7.11 يرفضها نهائياً (والبرامج المحدثة كذلك) + ما فيها تشفير.
# True = نتخطاها بدون اختبار. لو تبيها: خلها False وثبّت XRAY_VERSION على v26.6.27 في update.yml
SKIP_UNENCRYPTED = True
# =====================================================

PROTOCOLS = ["hysteria2", "hysteria", "wireguard", "vless", "vmess",
             "trojan", "tuic", "ssr", "hy2", "ss"]
LINK_RE = re.compile(r'(?<![A-Za-z0-9])(?:%s)://[^\s<>"\'`]+' % "|".join(PROTOCOLS),
                     re.IGNORECASE)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.IGNORECASE)
MT_RE = re.compile(r'(?:tg://proxy|t\.me/proxy|tg://socks|t\.me/socks)\?', re.IGNORECASE)
FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
TAGS = {s["tag"] for s in SOURCES}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

CDN_TRANSPORTS = {"ws", "xhttp", "httpupgrade", "splithttp", "h2", "grpc"}


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


def txt(x):
    return "" if x is None else str(x).strip()


def is_ip(h):
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def norm_path(p):
    p = urllib.parse.unquote(p or "").split("?")[0].split("#")[0].strip()
    return p.rstrip("/") or "/"


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
    يحلل الرابط ويرجع dict موحد:
      scheme, fam, user, host, port, net, sec, name, params (للـ Xray)
      cdn_key : مفتاح (UUID + دومين + مسار) لكونفيجات الـ CDN، أو "" إذا مو CDN
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
        host, port, user = norm_host(txt(d.get("add"))), txt(d.get("port")), txt(d.get("id")).lower()
        if not host or not port.isdigit() or not user:
            return None
        p = {"host": txt(d.get("host")), "path": txt(d.get("path")), "sni": txt(d.get("sni")),
             "fp": txt(d.get("fp")), "alpn": txt(d.get("alpn")), "header": txt(d.get("type")),
             "scy": txt(d.get("scy")), "service": txt(d.get("path")), "mode": txt(d.get("mode"))}
        net = txt(d.get("net")).lower() or "tcp"
        sec = txt(d.get("tls")).lower()
        name = txt(d.get("ps"))
    else:
        base, _, frag = link.partition("#")
        name = urllib.parse.unquote(frag)
        try:
            u = urllib.parse.urlsplit(base)
            host, port = norm_host(u.hostname), u.port
        except Exception:
            return None
        if not host or not port:
            return None
        port = str(port)
        user = urllib.parse.unquote(u.username or "")
        if u.password is not None and scheme in ("hy2", "hysteria2"):
            user += ":" + urllib.parse.unquote(u.password)
        if scheme in ("vless", "trojan", "hy2", "hysteria2") and not user:
            return None
        if scheme in ("vless", "tuic"):
            user = user.lower()
        q = {k.lower(): v for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)}
        p = {"host": q.get("host", ""), "path": q.get("path", ""), "sni": q.get("sni") or q.get("peer", ""),
             "fp": q.get("fp", ""), "alpn": q.get("alpn", ""), "header": q.get("headertype", ""),
             "pbk": q.get("pbk", ""), "sid": q.get("sid", ""), "spx": q.get("spx", ""),
             "pqv": q.get("pqv", ""), "flow": q.get("flow", ""),
             "encryption": q.get("encryption", "none"), "service": q.get("servicename", ""),
             "authority": q.get("authority", ""), "mode": q.get("mode", ""),
             "extra": q.get("extra", ""), "obfs": q.get("obfs", "")}
        net = (q.get("type") or "tcp").lower()
        sec = (q.get("security") or ("tls" if scheme in ("trojan", "hy2", "hysteria2") else "")).lower()

    dom = norm_host(p.get("sni")) or norm_host(p.get("host", "").split(",")[0])
    cdn_key = ""
    if net in CDN_TRANSPORTS and dom and not is_ip(dom):
        cdn_key = f"cdn|{fam}|{user}|{dom}|{net}|{norm_path(p.get('path') or p.get('service'))}"
    return {"scheme": scheme, "fam": fam, "user": user, "host": host, "port": port,
            "net": net, "sec": sec, "name": name, "p": p, "cdn_key": cdn_key}


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
def parse_date(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def parse_page(page_html):
    """يرجع ([(رقم_الرسالة, وقت_النشر, [روابط], عدد_MTProto), ...], اسم_القناة)"""
    post_re = re.compile(r'data-post="([^"/]+)/(\d+)"', re.IGNORECASE)
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


def collect(src_idx, source):
    """يجمع أحدث الكونفيجات (مرشحين للاختبار) من قناة واحدة."""
    channel = source["channel"]
    limit = source["count"] * CANDIDATE_MULTIPLIER
    max_pages = source.get("max_pages", MAX_PAGES)
    st = {"pages": 0, "msgs": 0, "raw": 0, "ss": 0, "bad": 0, "private": 0, "dup": 0, "mtproto": 0}
    result, seen = [], set()

    first_html, base, errors = None, None, []
    for host in MIRRORS:
        url0 = f"https://{host}/s/{channel}"
        try:
            page_html = fetch(url0)
        except Exception as e:
            errors.append(f"{host}: فشل الاتصال ({e})")
            continue
        msgs, real = parse_page(page_html)
        if msgs:
            first_html, base = page_html, url0
            if real and real.lower() != channel.lower():
                print(f"  تنبيه: القناة @{channel} اسمها الحالي @{real} - حدّث SOURCES")
            break
        errors.append(f"{host}: الصفحة بدون رسائل")
    if base is None:
        for e in errors:
            print(f"  [{channel}] {e}")
        return result

    url, page_html = base, first_html
    for page in range(max_pages):
        if page:
            try:
                page_html = fetch(url)
            except Exception as e:
                print(f"  [{channel}] توقف عند الصفحة {page + 1} ({e})")
                break
        st["pages"] += 1
        messages, _ = parse_page(page_html)
        if not messages:
            break
        for msg_id, date, links, n_mt in sorted(messages, key=lambda x: -x[0]):
            st["msgs"] += 1
            st["mtproto"] += n_mt
            for pos, link in enumerate(links):
                st["raw"] += 1
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
                    break
            if len(result) >= limit:
                break
        if len(result) >= limit:
            break
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
        with open("sub_raw.txt", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return out
    for pos, line in enumerate(lines):
        if is_skipped(line):
            continue
        info = parse_link(line)
        if not info or not info["name"].split() or info["name"].split()[-1] != tag:
            continue
        info.update(link=line, date=0.0, msg_id=0, pos=pos, src=src_idx)
        out.append(info)
    return out


# ------------------------------------------------------------ حذف المكرر
def resolve(host):
    if is_ip(host):
        return host
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except Exception:
        return None


def dedupe(cands):
    """يحذف المكرر عبر كل القنوات. يحتفظ بالأحدث."""
    socket.setdefaulttimeout(5)
    hosts = sorted({c["host"] for c in cands})
    with ThreadPoolExecutor(max_workers=32) as ex:
        ips = dict(zip(hosts, ex.map(resolve, hosts)))
    seen, out, dups = set(), [], 0
    for c in sorted(cands, key=sort_key):
        ip = ips.get(c["host"]) or c["host"]
        c["ip"] = ip
        keys = [f"{c['fam']}|{c['user']}|{ip}|{c['port']}"]
        if c["cdn_key"] and DEDUPE_CDN_VARIANTS:
            keys.append(c["cdn_key"])
        if not c["cdn_key"]:  # عنوان الدخول هو السيرفر نفسه (مو IP كلاود فلير مشترك)
            keys.append(f"uip|{c['user']}|{ip}")
            if DEDUPE_SAME_IP_PORT:
                keys.append(f"ipp|{ip}|{c['port']}")
        if any(k in seen for k in keys):
            dups += 1
            continue
        seen.update(keys)
        out.append(c)
    return out, dups


def sort_key(c):
    """الأحدث أولاً (عبر كل القنوات)، وداخل نفس الرسالة بنفس ترتيب الروابط."""
    return (-c["date"], c["src"], -c["msg_id"], c["pos"])


# ---------------------------------------------------- تحويل الرابط لإعداد Xray
def build_stream(c):
    p, net, sec = c["p"], c["net"], c["sec"]
    if net in ("tcp", "raw"):
        net = "raw"
    elif net in ("splithttp", "h2", "http"):
        net = "xhttp"
    if net not in ("raw", "ws", "httpupgrade", "xhttp", "grpc"):
        return None
    host = p.get("host", "")
    first_host = host.split(",")[0].strip()
    sni = p.get("sni") or first_host or ("" if is_ip(c["host"]) else c["host"])
    path = p.get("path") or "/"
    ss = {"network": net}
    if net == "raw":
        if p.get("header") == "http":
            ss["rawSettings"] = {"header": {"type": "http", "request": {
                "path": [path], "headers": {"Host": [h for h in host.split(",") if h] or [sni or c["host"]]}}}}
    elif net in ("ws", "httpupgrade"):
        ss[net + "Settings"] = {"path": path, "host": first_host or sni}
    elif net == "xhttp":
        x = {"path": path, "host": first_host or sni, "mode": p.get("mode") or "auto"}
        if p.get("extra"):
            try:
                x["extra"] = json.loads(p["extra"])
            except Exception:
                pass
        ss["xhttpSettings"] = x
    elif net == "grpc":
        ss["grpcSettings"] = {"serviceName": p.get("service", ""), "multiMode": p.get("mode") == "multi",
                              "authority": p.get("authority", "")}

    if sec == "tls":
        t = {"serverName": sni, "fingerprint": p.get("fp") or "chrome"}
        if p.get("alpn"):
            t["alpn"] = [a for a in p["alpn"].split(",") if a]
        ss["security"], ss["tlsSettings"] = "tls", t
    elif sec == "reality":
        if not p.get("pbk"):
            return None
        r = {"serverName": sni, "fingerprint": p.get("fp") or "chrome", "publicKey": p["pbk"],
             "shortId": p.get("sid", ""), "spiderX": p.get("spx", "")}
        if p.get("pqv"):
            r["mldsa65Verify"] = p["pqv"]
        ss["security"], ss["realitySettings"] = "reality", r
    elif sec not in ("", "none"):
        return None
    return ss


def build_outbound(c):
    port = int(c["port"])
    if c["scheme"] in ("hy2", "hysteria2"):
        p = c["p"]
        if p.get("obfs"):
            return None
        tls = {"serverName": p.get("sni") or ("" if is_ip(c["host"]) else c["host"]), "alpn": ["h3"]}
        return {"protocol": "hysteria", "settings": {"version": 2, "address": c["host"], "port": port},
                "streamSettings": {"network": "hysteria", "security": "tls", "tlsSettings": tls,
                                   "hysteriaSettings": {"version": 2, "auth": c["user"]}}}
    ss = build_stream(c)
    if ss is None:
        return None
    if c["scheme"] == "vless":
        flow = c["p"].get("flow", "") if ss["network"] == "raw" and c["sec"] in ("tls", "reality") else ""
        user = {"id": c["user"], "encryption": c["p"].get("encryption") or "none", "flow": flow}
        return {"protocol": "vless", "settings": {"vnext": [
            {"address": c["host"], "port": port, "users": [user]}]}, "streamSettings": ss}
    if c["scheme"] == "vmess":
        user = {"id": c["user"], "security": c["p"].get("scy") or "auto"}
        return {"protocol": "vmess", "settings": {"vnext": [
            {"address": c["host"], "port": port, "users": [user]}]}, "streamSettings": ss}
    if c["scheme"] == "trojan":
        return {"protocol": "trojan", "settings": {"servers": [
            {"address": c["host"], "port": port, "password": c["user"]}]}, "streamSettings": ss}
    return None


# ------------------------------------------------------------- الاختبار
_port_counter = itertools.count(20000)
_port_lock = threading.Lock()


def next_port():
    with _port_lock:
        return next(_port_counter)


def curl(port, url, timeout, out=os.devnull):
    cmd = ["curl", "-s", "-o", out, "-w", "%{http_code} %{time_total} %{speed_download} %{size_download}",
           "-m", str(timeout), "--connect-timeout", str(timeout),
           "-x", f"socks5h://127.0.0.1:{port}", "-A", UA, "-H", "Accept-Language: en-US", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        code, t, sp, size = r.stdout.split()
        return int(code), float(t), float(sp), int(float(size))
    except Exception:
        return 0, 0.0, 0.0, 0


def wait_port(port, proc, limit=4.0):
    end = time.time() + limit
    while time.time() < end:
        if proc.poll() is not None:
            return False
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

    # 3) الاختبار
    if ENABLE_TEST:
        test_all(cands)
        stats = {}
        for c in cands:
            r = c["test"]["reason"] or "ok"
            stats[r] = stats.get(r, 0) + 1
        print("نتيجة الاختبار:", stats)
        passed = [c for c in cands if c["test"]["ok"]]
    else:
        for c in cands:
            c["test"] = {"ok": True, "delay": 0, "speed": 0.0, "cc": "", "exit_ip": "", "ig": False}
        passed = cands

    # 4) حذف المكرر حسب IP الخروج (نخلي الأسرع)
    if ENABLE_TEST and DEDUPE_BY_EXIT_IP:
        per_ip, keep = {}, []
        for c in sorted(passed, key=lambda c: -c["test"]["speed"]):
            ip = c["test"]["exit_ip"]
            if ip:
                per_ip[ip] = per_ip.get(ip, 0) + 1
                if per_ip[ip] > MAX_PER_EXIT_IP:
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
        print(f"  [{tag}] مرشح={fetched.get(tag, 0)} شغال={len(lines)}")

    # ملخص في صفحة GitHub Actions
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## Sabah Sub: {len(final)} working configs\n\n")
            f.write("| # | Flag | Channel | Proto | Net | Sec | Delay ms | Mbps | Posted (UTC) |\n")
            f.write("|---|---|---|---|---|---|---|---|---|\n")
            for r in rows:
                f.write("| " + " | ".join(str(x) for x in r) + " |\n")


if __name__ == "__main__":
    main()
