#!/usr/bin/env python3
"""
يجلب أحدث الكونفيجات من عدة قنوات تيليجرام عامة (عن طريق صفحة المعاينة t.me/s/...)
ويبني ملف اشتراك (Subscription) لبرنامج v2rayNG.

المنابع (قابلة للتعديل من قسم الإعدادات):
  1) @TheFreeConfigs  -> آخر 50 كونفيج غير مكرر  -> Sabah 1   .. Sabah 50
  2) @tootffarangi2   -> آخر 100 كونفيج غير مكرر -> Sabah 51  .. Sabah 150

المميزات:
- حذف المكرر "الذكي": نفس السيرفر حتى لو اختلف ترتيب الباراميترات أو الاسم أو
  باراميترات شكلية (fp / sid / spx / host الإعلاني ...) يعتبر مكرر.
  والحذف يكون عبر كل المنابع (كونفيج موجود في المنبع 1 لا يتكرر في المنبع 2).
- علم الدولة: يحاول معرفة دولة السيرفر عن طريق IP (GeoIP) ويضع علمها.
  إذا ما قدر (CDN مثل Cloudflare أو فشل البحث) يستخدم العلم الأصلي من اسم
  الكونفيج، وإذا ما فيه علم يستخدم إيموجي بديل.
- إذا فشل أحد المنابع، يحتفظ بكونفيجاته القديمة بدل ما يمسحها.
- يكتب:  sub.txt (Base64 - هذا رابط الاشتراك)  و  sub_raw.txt (نص عادي)

لا يحتاج أي مكتبات خارجية.
"""
import base64
import html
import json
import re
import socket
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ===================== الإعدادات =====================
NAME_PREFIX = "Sabah"        # الاسم الجديد
MAX_PAGES = 40               # أقصى عدد صفحات يرجع لها في تاريخ كل قناة

# كل منبع: اسم القناة (بدون @) ، عدد الكونفيجات ، رقم أول كونفيج ، الإيموجي البديل
SOURCES = [
    {"channel": "TheFreeConfigs", "count": 50,  "start": 1,  "fallback_emoji": "🌐"},
    {"channel": "tootffarangi2",  "count": 100, "start": 51, "fallback_emoji": "🍓"},
]

USE_GEOIP = True             # True = حط علم دولة السيرفر الحقيقية (حسب الـ IP)
# إذا الـ GeoIP قال إن السيرفر في هذه الدول وكان للكونفيج علم أصلي مختلف،
# نخلي العلم الأصلي (لأن هذه غالباً سيرفرات "تونل/ريلاي" والخروج الحقيقي برا)
RELAY_COUNTRIES = {"IR"}
# =====================================================

PROTOCOLS = ["hysteria2", "hysteria", "wireguard", "vless", "vmess",
             "trojan", "tuic", "ssr", "hy2", "ss"]
# (?<![A-Za-z0-9]) حتى لا يلتقط "ss://" من داخل "vless://"
LINK_RE = re.compile(r'(?<![A-Za-z0-9])(?:%s)://[^\s<>"\'`]+' % "|".join(PROTOCOLS),
                     re.IGNORECASE)
FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
NUM_RE = re.compile(r"^%s\s+(\d+)\b" % re.escape(NAME_PREFIX))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# باراميترات تؤثر على "هوية" السيرفر (الباقي شكلي ولا يدخل في كشف التكرار)
KEY_PARAMS = ("type", "security", "sni", "pbk", "path", "servicename")
# الـ host يدخل في المقارنة فقط مع هذه الأنواع (في tcp عادة يكون نص إعلاني)
HOST_MATTERS = {"ws", "xhttp", "httpupgrade", "h2", "http", "splithttp"}

CDN_WORDS = ("cloudflare", "fastly", "akamai", "cloudfront", "gcore", "g-core",
             "cdn77", "bunny", "edgecast", "stackpath", "arvancloud", "imperva",
             "incapsula", "sucuri")


# ----------------------------------------------------------------- أدوات عامة
def fetch(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": UA,
                                          "Accept-Language": "en-US,en;q=0.9",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


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


# ----------------------------------------------------------- تحليل الكونفيج
def parse_link(link):
    """
    يرجع dict فيه:
      key   : مفتاح لكشف التكرار
      host  : عنوان السيرفر
      name  : الاسم الأصلي
    أو None إذا الرابط غير صالح.
    """
    scheme = link.split("://", 1)[0].lower()
    body = link.split("://", 1)[1]

    # ---------- vmess (Base64 JSON)
    if scheme == "vmess":
        try:
            d = json.loads(b64decode_any(body.split("#")[0]))
        except Exception:
            return None
        host = norm_host(str(d.get("add", "")))
        if not host:
            return None
        net = str(d.get("net", "")).lower()
        parts = [host, str(d.get("port", "")), str(d.get("id", "")).lower(), net,
                 str(d.get("tls", "")).lower(), norm_host(str(d.get("sni", ""))),
                 str(d.get("path", ""))]
        if net in HOST_MATTERS:
            parts.append(norm_host(str(d.get("host", ""))))
        return {"key": "vmess|" + "|".join(parts), "host": host,
                "name": str(d.get("ps", ""))}

    base, _, frag = link.partition("#")
    name = urllib.parse.unquote(frag)

    # ---------- shadowsocks
    if scheme == "ss":
        rest = base.split("://", 1)[1].split("?")[0].split("/")[0]
        if "@" not in rest:                       # ss://BASE64(method:pass@host:port)
            try:
                rest = b64decode_any(urllib.parse.unquote(rest))
            except Exception:
                return None
        userinfo, _, hostport = rest.rpartition("@")
        userinfo = urllib.parse.unquote(userinfo)
        if ":" not in userinfo:                   # userinfo بصيغة Base64
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
        return {"key": f"ss|{userinfo}|{host}|{port}", "host": host, "name": name}

    # ---------- vless / trojan / hysteria / tuic / ... (صيغة URL عادية)
    try:
        u = urllib.parse.urlsplit(base)
        host, port = norm_host(u.hostname), u.port
    except Exception:
        return None
    if not host:
        return None
    user = urllib.parse.unquote(u.username or "")
    q = {k.lower(): v for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)}
    parts = [scheme, user, host, str(port or "")]
    for p in KEY_PARAMS:
        val = q.get(p, "")
        parts.append(norm_host(val) if p == "sni" else val)
    if q.get("type", "tcp").lower() in HOST_MATTERS or q.get("headertype") == "http":
        parts.append(norm_host(q.get("host", "")))
    return {"key": "|".join(parts), "host": host, "name": name}


def rename(link, new_name):
    scheme = link.split("://", 1)[0].lower()
    if scheme == "vmess":
        data = json.loads(b64decode_any(link.split("://", 1)[1].split("#")[0]))
        data["ps"] = new_name
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return "vmess://" + base64.b64encode(raw).decode()
    return link.split("#", 1)[0] + "#" + urllib.parse.quote(new_name)


# --------------------------------------------------------------- جلب القنوات
def parse_page(page_html, channel):
    """يرجع [(رقم_الرسالة, [روابط...]), ...]"""
    post_re = re.compile(r'data-post="%s/(\d+)"' % re.escape(channel), re.IGNORECASE)
    messages = []
    matches = list(post_re.finditer(page_html))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page_html)
        text = html_to_text(page_html[start:end])
        links = [l.rstrip(".,;)]}") for l in LINK_RE.findall(text)]
        messages.append((int(m.group(1)), links))
    return messages


def collect(source, seen):
    """يجمع أحدث الكونفيجات غير المكررة من قناة واحدة."""
    channel, limit = source["channel"], source["count"]
    result = []
    url = f"https://t.me/s/{channel}"
    for _ in range(MAX_PAGES):
        messages = parse_page(fetch(url), channel)
        if not messages:
            break
        # الأحدث أولاً، مع الحفاظ على ترتيب الكونفيجات داخل نفس الرسالة
        for _msg_id, links in sorted(messages, key=lambda x: -x[0]):
            for link in links:
                info = parse_link(link)
                if not info or info["key"] in seen:
                    continue
                seen.add(info["key"])
                info["link"] = link
                result.append(info)
                if len(result) >= limit:
                    return result
        oldest = min(m[0] for m in messages)
        if oldest <= 1:
            break
        url = f"https://t.me/s/{channel}?before={oldest}"
    return result


# ---------------------------------------------------------------- GeoIP
def resolve(host):
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
        return host                                   # هو IP أصلاً
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
    for i in range(0, len(unique_ips), 100):           # ip-api يقبل 100 لكل طلب
        chunk = unique_ips[i:i + 100]
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
    if g and g[0] and not g[1]:                        # موجود وليس CDN
        cc = g[0].upper()
        if cc in RELAY_COUNTRIES and original and flag_to_cc(original) != cc:
            return original
        return cc_to_flag(cc) or original or fallback
    return original or fallback


# -------------------------------------------------- الاحتفاظ بالقديم عند الفشل
def load_previous():
    """يرجع {رقم: رابط} من sub_raw.txt القديم."""
    old = {}
    try:
        with open("sub_raw.txt", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return old
    for line in lines:
        info = parse_link(line)
        if not info:
            continue
        m = NUM_RE.match(info["name"].strip())
        if m:
            old[int(m.group(1))] = line
    return old


# ------------------------------------------------------------------ main
def main():
    seen = set()
    per_source = []
    any_new = False

    for src in SOURCES:
        try:
            cfgs = collect(src, seen)
            print(f"@{src['channel']}: {len(cfgs)} كونفيج")
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
    for src, cfgs in zip(SOURCES, per_source):
        if not cfgs:
            # المنبع فشل/فاضي -> نحتفظ بكونفيجاته القديمة
            if previous is None:
                previous = load_previous()
            rng = range(src["start"], src["start"] + src["count"])
            kept = [previous[n] for n in rng if n in previous]
            print(f"@{src['channel']}: الاحتفاظ بـ {len(kept)} كونفيج قديم")
            final.extend(kept)
            continue
        for i, cfg in enumerate(cfgs):
            flag = choose_flag(cfg, geo, src["fallback_emoji"])
            new_name = f"{NAME_PREFIX} {src['start'] + i} {flag}".strip()
            final.append(rename(cfg["link"], new_name))

    raw = "\n".join(final) + "\n"
    with open("sub_raw.txt", "w", encoding="utf-8") as f:
        f.write(raw)
    with open("sub.txt", "w", encoding="utf-8") as f:
        f.write(base64.b64encode(raw.encode("utf-8")).decode())

    print(f"تم: {len(final)} كونفيج")
    for line in final[:3] + final[50:53]:
        print("  ", urllib.parse.unquote(line)[-40:])


if __name__ == "__main__":
    main()
