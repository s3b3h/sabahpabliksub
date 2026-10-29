#!/usr/bin/env python3
"""
يجلب أحدث الكونفيجات من عدة قنوات تيليجرام عامة (عن طريق صفحة المعاينة t.me/s/...)
ويبني ملف اشتراك (Subscription) لبرنامج v2rayNG.

المنابع (قابلة للتعديل من قسم الإعدادات):
  1) @TheFreeConfigs          -> آخر 50  -> Sabah 1 .. Sabah 50   (Free)
  2) @tootffarangi2           -> آخر 100 -> Sabah 51 .. Sabah 150 (Toot)
  3) @v2rei                   -> آخر 100 -> Sabah 151 .. Sabah 250 (V2rei)
  4) @proxy_mtproto_vpns_free -> آخر 100 -> Sabah 251 .. Sabah 350 (Proxy)
  5) @ettehad_vpn             -> آخر 100 -> Sabah 351 .. Sabah 450 (Etehad)
المجموع: 450 كونفيج

شكل الاسم:  Sabah 151 🇩🇪 V2rei   (رقم + علم الدولة + اختصار القناة)
- سيرفرات الشادوساكس (ss:// و ssr://) يتم تخطيها ولا تدخل في العدد.

الجديد في هذه النسخة:
- حذف مكرر أقوى: نفس السيرفر (نفس الـ UUID/الباسورد + نفس العنوان + نفس البورت)
  يعتبر مكرر حتى لو اختلفت كل الباراميترات الباقية (sni / path / fp / type ...)
  وعبر كل المنابع. وكذلك الكونفيجات القديمة المحتفظ بها لا تتكرر.
- خيار DEDUPE_CDN_VARIANTS لحذف نسخ نفس الكونفيج التي تختلف فقط في IP الكلاود فلير.
- قراءة الروابط من داخل href (الروابط المخفية خلف نص مثل "اضغط هنا").
- لا يعتمد على اسم القناة داخل الصفحة (إذا تغير اسم القناة يظل يشتغل).
- تجربة t.me ثم telegram.me إذا فشل الأول.
- سطر تشخيص لكل قناة في اللوج يوضح كم رسالة وكم رابط وكم مكرر ... إلخ.

لا يحتاج أي مكتبات خارجية.
"""

import base64
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ===================== الإعدادات =====================
NAME_PREFIX = "Sabah"          # الاسم الجديد
MAX_PAGES = 60                 # أقصى عدد صفحات يرجع لها في تاريخ كل قناة (افتراضي)

# كل منبع:
#   channel        : اسم القناة (بدون @)
#   count          : عدد الكونفيجات
#   start          : رقم أول كونفيج
#   tag            : اختصار قصير يظهر في آخر الاسم (حروف/أرقام إنجليزية فقط بدون مسافات)
#   fallback_emoji : إيموجي بديل إذا ما عُرف علم الدولة
#   max_pages      : (اختياري) أقصى عدد صفحات لهذه القناة
#   legacy_range   : (اختياري) أرقام هذه القناة في النسخة القديمة (قبل إضافة الاختصار)
#                    تُستخدم مرة وحدة فقط للاحتفاظ بالقديم إذا فشل الجلب
SOURCES = [
    {"channel": "TheFreeConfigs", "count": 50, "start": 1, "tag": "Free",
     "fallback_emoji": "🌐", "legacy_range": (1, 50)},
    {"channel": "tootffarangi2", "count": 100, "start": 51, "tag": "Toot",
     "fallback_emoji": "🍓", "legacy_range": (51, 200)},
    {"channel": "v2rei", "count": 100, "start": 151, "tag": "V2rei",
     "fallback_emoji": "⚡"},
    {"channel": "proxy_mtproto_vpns_free", "count": 100, "start": 251, "tag": "Proxy",
     "fallback_emoji": "🔰", "max_pages": 80},
    {"channel": "ettehad_vpn", "count": 100, "start": 351, "tag": "Etehad",
     "fallback_emoji": "🔥"},
]

# بروتوكولات يتم تخطيها تماماً (لا تدخل في الاشتراك ولا في العدد)
# ss = Shadowsocks ، ssr = ShadowsocksR
SKIP_PROTOCOLS = {"ss", "ssr"}

USE_GEOIP = True               # True = حط علم دولة السيرفر الحقيقية (حسب الـ IP)

# إذا الـ GeoIP قال إن السيرفر في هذه الدول وكان للكونفيج علم أصلي مختلف،
# نخلي العلم الأصلي (لأن هذه غالباً سيرفرات "تونل/ريلاي" والخروج الحقيقي برا)
RELAY_COUNTRIES = {"IR"}

SKIP_PRIVATE_HOSTS = True      # True = تخطي السيرفرات بعناوين داخلية/وهمية (127.0.0.1 ...)
WRITE_PER_CHANNEL = True       # True = اكتب اشتراك منفصل لكل قناة في مجلد subs/
PAGE_DELAY = 0.4               # ثواني بين كل صفحة وصفحة (حتى ما يحظرنا تيليجرام)

# False (الافتراضي) = المكرر هو نفس (UUID + العنوان + البورت).
# True = أيضاً نعتبر مكرر الكونفيجات اللي تختلف فقط في عنوان الدخول (مثل IPات كلاود فلير
#        المختلفة لنفس الدومين/المسار). هذا يقلل العدد كثير لكنه يحذف تنويعات الـ IP.
DEDUPE_CDN_VARIANTS = False

# مواقع المعاينة العامة لتيليجرام (يجرب بالترتيب لين يلقى واحد يرجع رسائل)
MIRRORS = ("t.me", "telegram.me")
# =====================================================

PROTOCOLS = ["hysteria2", "hysteria", "wireguard", "vless", "vmess",
             "trojan", "tuic", "ssr", "hy2", "ss"]

# (?<![A-Za-z0-9]) حتى لا يلتقط "ss://" من داخل "vless://"
LINK_RE = re.compile(r'(?<![A-Za-z0-9])(?:%s)://[^\s<>"\'`]+' % "|".join(PROTOCOLS),
                     re.IGNORECASE)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
MT_RE = re.compile(r'(?:tg://proxy|t\.me/proxy|tg://socks|t\.me/socks)\?', re.IGNORECASE)
FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
NUM_RE = re.compile(r"^%s\s+(\d+)\b" % re.escape(NAME_PREFIX))
TAGS = {s["tag"] for s in SOURCES}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# الـ host يدخل في المقارنة فقط مع هذه الأنواع (في tcp عادة يكون نص إعلاني)
HOST_MATTERS = {"ws", "xhttp", "httpupgrade", "h2", "http", "splithttp"}
# أنواع النقل اللي تمر عادة عبر CDN (تستخدم في DEDUPE_CDN_VARIANTS)
CDN_TRANSPORTS = {"ws", "xhttp", "httpupgrade", "splithttp", "h2"}

CDN_WORDS = ("cloudflare", "fastly", "akamai", "cloudfront", "gcore", "g-core",
             "cdn77", "bunny", "edgecast", "stackpath", "arvancloud", "imperva",
             "incapsula", "sucuri")


# ----------------------------------------------------------------- أدوات عامة
def fetch(url, data=None, timeout=30, retries=3):
    """جلب صفحة مع إعادة المحاولة (مفيد عند الحظر المؤقت 429 أو انقطاع الشبكة)."""
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data,
                                     headers={"User-Agent": UA,
                                              "Accept-Language": "en-US,en;q=0.9",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise
            time.sleep((10 if e.code == 429 else 3) * (attempt + 1))
        except Exception as e:  # timeout / شبكة
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


def flag_to_cc(flag):
    if not flag or len(flag) != 2:
        return ""
    return "".join(chr(ord(c) - 0x1F1E6 + 65) for c in flag)


def norm_host(h):
    return (h or "").strip().strip("[]").rstrip(".").lower()


def txt(x):
    """تحويل أي قيمة لنص (None -> فاضي)."""
    return "" if x is None else str(x).strip()


def is_ip(h):
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def norm_path(p):
    """يشيل الجزء الإعلاني بعد ? ويوحد الشرطة الأخيرة."""
    p = urllib.parse.unquote(p or "").split("?")[0].split("#")[0].strip()
    return p.rstrip("/") or "/"


def is_bad_host(host):
    """True إذا كان العنوان داخلي/وهمي ما يشتغل (127.0.0.1 ، 10.x ، 0.0.0.0 ، localhost ...)."""
    if not SKIP_PRIVATE_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("localhost", "example.com") or host.endswith((".local", ".localhost"))
    return not ip.is_global


# ----------------------------------------------------------- تحليل الكونفيج
def parse_link(link):
    """
    يرجع dict فيه:
      key  : مفتاح كشف التكرار الأساسي (نوع + UUID + عنوان + بورت)
      key2 : مفتاح أوسع (يدمج تنويعات IP الكلاود فلير) - يستخدم إذا DEDUPE_CDN_VARIANTS
      host : عنوان السيرفر
      name : الاسم الأصلي
    أو None إذا الرابط غير صالح.
    """
    scheme = link.split("://", 1)[0].lower()
    body = link.split("://", 1)[1]
    fam = "hysteria2" if scheme == "hy2" else scheme

    # ---------- vmess (Base64 JSON)
    if scheme == "vmess":
        try:
            d = json.loads(b64decode_any(body.split("#")[0]))
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        host = norm_host(txt(d.get("add")))
        port = txt(d.get("port"))
        uid = txt(d.get("id")).lower()
        if not host or not port or not uid:
            return None
        net = txt(d.get("net")).lower() or "tcp"
        tls = txt(d.get("tls")).lower()
        dom = norm_host(txt(d.get("sni"))) or norm_host(txt(d.get("host")))
        key = f"vmess|{uid}|{host}|{port}"
        key2 = key
        if tls == "tls" and net in CDN_TRANSPORTS and dom and not is_ip(dom):
            key2 = f"cdn|vmess|{uid}|{port}|{dom}|{net}|{norm_path(txt(d.get('path')))}"
        return {"key": key, "key2": key2, "host": host, "name": txt(d.get("ps"))}

    base, _, frag = link.partition("#")
    name = urllib.parse.unquote(frag)

    # ---------- shadowsocks
    if scheme == "ss":
        rest = base.split("://", 1)[1].split("?")[0].split("/")[0]
        if "@" not in rest:  # ss://BASE64(method:pass@host:port)
            try:
                rest = b64decode_any(urllib.parse.unquote(rest))
            except Exception:
                return None
        userinfo, _, hostport = rest.rpartition("@")
        userinfo = urllib.parse.unquote(userinfo)
        if ":" not in userinfo:  # userinfo بصيغة Base64
            try:
                userinfo = b64decode_any(userinfo)
            except Exception:
                return None
        try:
            u = urllib.parse.urlsplit("ss://x@" + hostport)
            host, port = norm_host(u.hostname), u.port
        except Exception:
            return None
        if not host or not port:
            return None
        key = f"ss|{userinfo}|{host}|{port}"
        return {"key": key, "key2": key, "host": host, "name": name}

    # ---------- vless / trojan / hysteria / tuic / wireguard (صيغة URL عادية)
    try:
        u = urllib.parse.urlsplit(base)
        host, port = norm_host(u.hostname), u.port
    except Exception:
        return None
    if not host:
        return None
    user = urllib.parse.unquote(u.username or "")
    if scheme in ("vless", "trojan") and not user:
        return None  # بدون UUID/باسورد = غير صالح
    # UUID ما يتأثر بحالة الحروف، أما باسورد التروجان فيتأثر
    user_n = user.lower() if scheme in ("vless", "tuic") else user

    q = {k.lower(): v for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)}
    transport = (q.get("type") or "tcp").lower()
    sec = (q.get("security") or "").lower()
    dom = norm_host(q.get("sni", "")) or norm_host(q.get("host", ""))

    key = f"{fam}|{user_n}|{host}|{port or ''}"
    key2 = key
    if sec == "tls" and transport in CDN_TRANSPORTS and dom and not is_ip(dom):
        key2 = f"cdn|{fam}|{user_n}|{port or ''}|{dom}|{transport}|{norm_path(q.get('path', ''))}"
    return {"key": key, "key2": key2, "host": host, "name": name}


def dedupe_key(info):
    return info["key2"] if DEDUPE_CDN_VARIANTS else info["key"]


def is_skipped(link):
    """True إذا كان بروتوكول الرابط من البروتوكولات المحذوفة (مثل الشادوساكس)."""
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
    """Sabah 151 🇩🇪 V2rei"""
    return " ".join(x for x in (NAME_PREFIX, str(num), flag, tag) if x)


# --------------------------------------------------------------- جلب القنوات
def parse_page(page_html, channel):
    """
    يرجع ([(رقم_الرسالة, [روابط...], عدد_روابط_MTProto), ...], اسم_القناة_الفعلي)
    ملاحظة: ما نعتمد على اسم القناة داخل الصفحة (ممكن يتغير أو يختلف في الحروف).
    """
    post_re = re.compile(r'data-post="([^"/]+)/(\d+)"', re.IGNORECASE)
    matches = list(post_re.finditer(page_html))
    messages, real = [], ""
    for i, m in enumerate(matches):
        if not real:
            real = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page_html)
        block = page_html[start:end]

        # 1) الروابط الظاهرة كنص
        links = [l.rstrip(".,;)]}") for l in LINK_RE.findall(html_to_text(block))]
        # 2) الروابط المخفية داخل href (مثل: <a href="vless://...">اضغط هنا</a>)
        for href in HREF_RE.findall(block):
            href = html.unescape(href).strip()
            if LINK_RE.match(href):
                links.append(href.rstrip(".,;)]}"))
        links = list(dict.fromkeys(links))  # حذف التكرار داخل نفس الرسالة

        n_mt = len(MT_RE.findall(html.unescape(block)))
        messages.append((int(m.group(2)), links, n_mt))
    return messages, real


def report(channel, st, got, limit):
    """سطر تشخيص في اللوج."""
    print(f"  [{channel}] صفحات={st['pages']} رسائل={st['msgs']} روابط={st['raw']} | "
          f"مكرر={st['dup']} ss={st['ss']} غير_صالح={st['bad']} داخلي={st['private']} | "
          f"MTProto={st['mtproto']} | مقبول={got}/{limit}")
    if got == 0:
        if st["raw"] == 0:
            msg = "لم يتم العثور على أي رابط v2ray في الرسائل"
            if st["mtproto"]:
                msg += " (القناة تنشر بروكسيات تيليجرام MTProto فقط وهذه لا تعمل في v2rayNG)"
            print(f"  [{channel}] تشخيص: {msg}")
        elif st["dup"] == st["raw"] - st["ss"] - st["bad"] - st["private"]:
            print(f"  [{channel}] تشخيص: كل الروابط مكررة من منابع سابقة")


def collect(source, seen):
    """يجمع أحدث الكونفيجات غير المكررة من قناة واحدة."""
    channel, limit = source["channel"], source["count"]
    max_pages = source.get("max_pages", MAX_PAGES)
    st = {"pages": 0, "msgs": 0, "raw": 0, "ss": 0, "bad": 0,
          "private": 0, "dup": 0, "mtproto": 0}
    result = []

    # الصفحة الأولى: نجرب المواقع بالترتيب لين يرجع واحد منها رسائل
    first_html, base, errors = None, None, []
    for host in MIRRORS:
        url0 = f"https://{host}/s/{channel}"
        try:
            page_html = fetch(url0)
        except Exception as e:
            errors.append(f"{host}: فشل الاتصال ({e})")
            continue
        msgs, real = parse_page(page_html, channel)
        if msgs:
            first_html, base = page_html, url0
            if real and real.lower() != channel.lower():
                print(f"  تنبيه: القناة @{channel} اسمها الحالي @{real} - حدّث SOURCES")
            break
        errors.append(f"{host}: الصفحة بدون رسائل (المعاينة معطلة أو القناة محذوفة/خاصة)")
    if base is None:
        for e in errors:
            print(f"  [{channel}] {e}")
        return result

    url, page_html, full = base, first_html, False
    for page in range(max_pages):
        if page:
            try:
                page_html = fetch(url)
            except Exception as e:
                print(f"  [{channel}] توقف عند الصفحة {page + 1} ({e})")
                break
        st["pages"] += 1
        messages, _real = parse_page(page_html, channel)
        if not messages:
            break

        # الأحدث أولاً، مع الحفاظ على ترتيب الكونفيجات داخل نفس الرسالة
        for _msg_id, links, n_mt in sorted(messages, key=lambda x: -x[0]):
            st["msgs"] += 1
            st["mtproto"] += n_mt
            for link in links:
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
                k = dedupe_key(info)
                if k in seen:
                    st["dup"] += 1
                    continue
                seen.add(k)
                info["link"] = link
                result.append(info)
                if len(result) >= limit:
                    full = True
                    break
            if full:
                break
        if full:
            break

        oldest = min(m[0] for m in messages)
        if oldest <= 1:
            break
        url = f"{base}?before={oldest}"
        time.sleep(PAGE_DELAY)

    report(channel, st, len(result), limit)
    return result


# ---------------------------------------------------------------- GeoIP
def resolve(host):
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
        return host  # هو IP أصلاً
    except OSError:
        pass
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except Exception:
        return None


def geoip(hosts):
    """يرجع {host: (country_code, is_cdn)}"""
    socket.setdefaulttimeout(5)
    hosts = sorted(set(hosts))
    with ThreadPoolExecutor(max_workers=32) as ex:
        ips = dict(zip(hosts, ex.map(resolve, hosts)))
    unique_ips = sorted({ip for ip in ips.values() if ip})

    info = {}
    for n, i in enumerate(range(0, len(unique_ips), 100)):  # ip-api يقبل 100 لكل طلب
        chunk = unique_ips[i:i + 100]
        if n:
            time.sleep(4.5)  # حد ip-api: 15 طلب batch بالدقيقة
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
    if g and g[0] and not g[1]:  # موجود وليس CDN
        cc = g[0].upper()
        if cc in RELAY_COUNTRIES and original and flag_to_cc(original) != cc:
            return original
        return cc_to_flag(cc) or original or fallback
    return original or fallback


# -------------------------------------------------- الاحتفاظ بالقديم عند الفشل
def load_previous():
    """يرجع [(رقم, اختصار_القناة أو "", العلم, رابط), ...] من sub_raw.txt القديم."""
    old = []
    try:
        with open("sub_raw.txt", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return old
    for line in lines:
        if is_skipped(line):
            continue
        info = parse_link(line)
        if not info:
            continue
        name = info["name"].strip()
        m = NUM_RE.match(name)
        if not m:
            continue
        words = name.split()
        tag = words[-1] if words and words[-1] in TAGS else ""
        old.append((int(m.group(1)), tag, get_flag(name), line))
    return old


def kept_for_source(src, previous, seen):
    """
    الكونفيجات القديمة الخاصة بهذا المنبع (حسب الاختصار، أو حسب الأرقام القديمة).
    تتجنب أي كونفيج مكرر مع اللي انجلب من المنابع الثانية (seen).
    """
    kept = [(flag, link) for num, tag, flag, link in sorted(previous) if tag == src["tag"]]
    if not kept and src.get("legacy_range"):
        a, b = src["legacy_range"]
        kept = [(flag, link) for num, tag, flag, link in sorted(previous)
                if not tag and a <= num <= b]

    out = []
    for flag, link in kept:
        if len(out) >= src["count"]:
            break
        info = parse_link(link)
        if not info:
            continue
        k = dedupe_key(info)
        if k in seen:
            continue
        try:
            out.append(rename(link, make_name(src["start"] + len(out),
                                              flag or src["fallback_emoji"], src["tag"])))
        except Exception:
            continue
        seen.add(k)
    return out


# ------------------------------------------------------------------ main
def write_sub(path, lines):
    raw = "\n".join(lines) + "\n" if lines else ""
    with open(path, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(raw.encode("utf-8")).decode())
    return raw


def main():
    seen = set()
    per_source = []
    any_new = False

    for src in SOURCES:
        t0 = time.time()
        try:
            cfgs = collect(src, seen)
            print(f"@{src['channel']}: {len(cfgs)}/{src['count']} كونفيج "
                  f"({time.time() - t0:.0f}s)")
        except Exception as e:
            print(f"@{src['channel']}: خطأ أثناء الجلب -> {e}")
            cfgs = None
        if cfgs:
            any_new = True
        per_source.append(cfgs)

    if not any_new:
        print("لم يتم العثور على أي كونفيج - لن يتم تعديل الملفات.")
        sys.exit(1)

    geo = {}
    if USE_GEOIP:
        hosts = [c["host"] for cfgs in per_source if cfgs for c in cfgs]
        try:
            geo = geoip(hosts)
            print(f"GeoIP: تم تحديد دولة {len(geo)} من {len(set(hosts))} سيرفر")
        except Exception as e:
            print("تحذير: GeoIP فشل بالكامل:", e)

    previous = None
    final = []
    by_tag = {}
    for src, cfgs in zip(SOURCES, per_source):
        lines = []
        if not cfgs:
            # المنبع فشل/فاضي -> نحتفظ بكونفيجاته القديمة (بدون المكرر)
            if previous is None:
                previous = load_previous()
            lines = kept_for_source(src, previous, seen)
            print(f"@{src['channel']}: الاحتفاظ بـ {len(lines)} كونفيج قديم")
        else:
            for i, cfg in enumerate(cfgs):
                flag = choose_flag(cfg, geo, src["fallback_emoji"])
                try:
                    lines.append(rename(cfg["link"],
                                        make_name(src["start"] + i, flag, src["tag"])))
                except Exception:
                    continue
        by_tag[src["tag"]] = lines
        final.extend(lines)

    raw = "\n".join(final) + "\n"
    with open("sub_raw.txt", "w", encoding="utf-8") as f:
        f.write(raw)
    write_sub("sub.txt", final)

    if WRITE_PER_CHANNEL:
        os.makedirs("subs", exist_ok=True)
        for tag, lines in by_tag.items():
            if lines:
                write_sub(os.path.join("subs", f"{tag}.txt"), lines)

    print(f"تم: {len(final)} كونفيج")
    for tag, lines in by_tag.items():
        if lines:
            print(f"  [{tag}] {len(lines)} ->", urllib.parse.unquote(lines[0])[-30:])


if __name__ == "__main__":
    main()
