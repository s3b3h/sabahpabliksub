#!/usr/bin/env python3
"""
يجلب أحدث الكونفيجات من عدة قنوات تيليجرام عامة (عن طريق صفحة المعاينة t.me/s/...)
ويبني ملف اشتراك (Subscription) لبرنامج v2rayNG.

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
    {"channel": "proxyfree", "count": PER_CHANNEL, "start": 151, "tag": "Proxy",
     "fallback_emoji": "🔰", "max_pages": 150},
    {"channel": "ettehad_vpn", "count": PER_CHANNEL, "start": 201, "tag": "Etehad",
     "fallback_emoji": "🔥"},
    {"channel": "DailyCfg", "count": PER_CHANNEL, "start": 251, "tag": "Daily",
     "fallback_emoji": "📅"},
    {"channel": "v2ray_dalghak", "count": PER_CHANNEL, "start": 301, "tag": "Dalghak",
     "fallback_emoji": "🤡"},
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
# روابط بروكسي تيليجرام (MTProto / SOCKS) - ليست كونفيجات v2ray، تُتخطى دائماً
PROXY_RE = re.compile(r'(?:tg://|https?://(?:www\.)?t\.me/)(?:proxy|socks)\?', re.IGNORECASE)
HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
STATS = {}   # إحصائيات كل قناة (للتشخيص)
FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
NUM_RE = re.compile(r"^%s\s+(\d+)\b" % re.escape(NAME_PREFIX))
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TAGS = {s["tag"] for s in SOURCES}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

CDN_WORDS = ("cloudflare", "fastly", "akamai", "cloudfront", "gcore", "g-core",
             "cdn77", "bunny", "edgecast", "stackpath", "arvancloud", "imperva",
             "incapsula", "sucuri")


# ----------------------------------------------------------------- أدوات عامة
def fetch(url, data=None, timeout=30, retries=3):
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


def flag_to_cc(flag):
    if not flag or len(flag) != 2:
        return ""
    return "".join(chr(ord(c) - 0x1F1E6 + 65) for c in flag)


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


# ----------------------------------------------------------- تحليل الكونفيج
def parse_link(link):
    """
    يرجع dict فيه: key (للتكرار) ، host ، name
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
        if not isinstance(d, dict):
            return None
        host = norm_host(str(d.get("add", "")))
        port = str(d.get("port", "")).strip()
        uid = str(d.get("id", "")).strip()
        if not host or not port or not uid:
            return None
        return {"key": make_key(uid, host, port), "host": host, "user": uid,
                "port": port, "name": str(d.get("ps", ""))}

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
            u = urllib.parse.urlsplit("ss://x@" + hostport)
            host, port = norm_host(u.hostname), u.port
        except Exception:
            return None
        if not host or not port:
            return None
        return {"key": make_key(userinfo, host, port), "host": host,
                "user": userinfo, "port": port, "name": name}

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

    return {"key": make_key(user, host, port or ""), "host": host,
            "user": user, "port": port or "", "name": name}


_DNS = {}


def is_ip(h):
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def resolve_many(hosts):
    """يحوّل الدومينات إلى IP (بالتوازي) ويخزنها، عشان نكشف التكرار حتى لو
    كان أحدهم بدومين والثاني بالـ IP نفسه."""
    todo = [h for h in set(hosts) if h and not is_ip(h) and h not in _DNS]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=16) as ex:
        for h, ip in zip(todo, ex.map(resolve, todo)):
            _DNS[h] = ip


def ip_key(info):
    """مفتاح التكرار بعد تحويل الدومين إلى IP (إن أمكن)."""
    h = info["host"]
    return make_key(info.get("user", ""), _DNS.get(h) or h, info.get("port", ""))


def is_dup(info, seen):
    return info["key"] in seen or ip_key(info) in seen


def mark_seen(info, seen):
    seen.add(info["key"])
    seen.add(ip_key(info))


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
    """يرجع [(رقم_الرسالة, [روابط الكونفيج], عدد_روابط_البروكسي)]"""
    post_re = re.compile(r'data-post="%s/(\d+)"' % re.escape(channel), re.IGNORECASE)
    messages = []
    matches = list(post_re.finditer(page_html))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page_html)
        segment = page_html[start:end]
        text = html_to_text(segment)
        links = [l.rstrip(".,;)]}") for l in LINK_RE.findall(text)]
        # الروابط المخفية خلف نص (مثل: [انسخ الكونفيج](vless://...))
        hrefs = [html.unescape(h) for h in HREF_RE.findall(segment)]
        for h in hrefs:
            if LINK_RE.match(h):
                h = h.rstrip(".,;)]}")
                if h not in links:
                    links.append(h)
        nproxy = len(PROXY_RE.findall(text)) + sum(1 for h in hrefs if PROXY_RE.match(h))
        messages.append((int(m.group(1)), links, nproxy))
    return messages


def collect(source, seen):
    """
    يجمع أحدث الكونفيجات غير المكررة من قناة واحدة.
    أي كونفيج مكرر (نفس UUID + IP + بورت) يُتخطى ويُكمل بالأقدم حتى يكتمل العدد.
    """
    channel, limit = source["channel"], source["count"]
    max_pages = source.get("max_pages", MAX_PAGES)
    result = []
    st = STATS[channel] = {"pages": 0, "posts": 0, "proxy": 0, "configs": 0, "note": ""}
    url = f"https://t.me/s/{channel}"

    for page in range(max_pages):
        try:
            page_html = fetch(url)
        except Exception as e:
            if result:
                print(f"  @{channel}: توقف عند الصفحة {page + 1} ({e})")
                break
            raise
        messages = parse_page(page_html, channel)
        st["pages"] += 1
        if not messages:
            if page == 0:
                st["note"] = "لا توجد منشورات في صفحة المعاينة (ربما المعاينة معطلة للقناة)"
            break
        st["posts"] += len(messages)
        st["proxy"] += sum(m[2] for m in messages)

        ordered = []
        for _msg_id, links, _np in sorted(messages, key=lambda x: -x[0]):
            for link in links:
                st["configs"] += 1
                if is_skipped(link):
                    continue
                info = parse_link(link)
                if info:
                    info["link"] = link
                    ordered.append(info)
        resolve_many([i["host"] for i in ordered])

        for info in ordered:
            if is_dup(info, seen) or is_bad_host(info["host"]):
                continue
            mark_seen(info, seen)
            result.append(info)
            if len(result) >= limit:
                return result

        oldest = min(m[0] for m in messages)
        if oldest <= 1:
            break
        url = f"https://t.me/s/{channel}?before={oldest}"
        time.sleep(PAGE_DELAY)
    return result


# ---------------------------------------------------------------- GeoIP
def resolve(host):
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
    hosts = sorted(set(hosts))
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


def kept_for_source(src, previous):
    kept = [(flag, link) for num, tag, flag, link in sorted(previous) if tag == src["tag"]]
    out = []
    for i, (flag, link) in enumerate(kept[:src["count"]]):
        try:
            out.append(rename(link, make_name(src["start"] + i,
                                              flag or src["fallback_emoji"], src["tag"])))
        except Exception:
            continue
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
        by_tag[src["tag"]] = lines
        final.extend(lines)

    # فحص نهائي: يحذف أي مكرر تبقى (مثلاً بين كونفيجات قديمة محتفظ بها وجديدة)
    all_infos = {t: [(l, parse_link(l)) for l in ls] for t, ls in by_tag.items()}
    resolve_many([i["host"] for ls in all_infos.values() for _, i in ls if i])
    final_seen = set()
    final = []
    for tag, pairs in all_infos.items():
        kept = []
        for line, info in pairs:
            if info and is_dup(info, final_seen):
                print(f"  حذف مكرر نهائي [{tag}]: {info['host']}:{info['port']}")
                continue
            if info:
                mark_seen(info, final_seen)
            kept.append(line)
        by_tag[tag] = kept
        final.extend(kept)

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
    summary = [f"### المجموع: {len(final)} كونفيج", "", "| القناة | العدد | صفحات / منشورات / بروكسي / كونفيج |", "|---|---|---|"]
    for src in SOURCES:
        n = len(by_tag.get(src["tag"], []))
        print(f"  [{src['tag']}] {n}" + ("   <-- ناقص!" if n < src["count"] else ""))
        st = STATS.get(src["channel"], {})
        if n < src["count"] and st:
            print(f"      تشخيص: صفحات={st['pages']} منشورات={st['posts']} "
                  f"روابط بروكسي={st['proxy']} روابط كونفيج={st['configs']} {st['note']}")
        summary.append(f"| @{src['channel']} | {n} / {src['count']} | "
                       f"{st.get('pages', 0)} / {st.get('posts', 0)} / {st.get('proxy', 0)} / {st.get('configs', 0)} |")
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write("\n".join(summary) + "\n")


if __name__ == "__main__":
    main()
