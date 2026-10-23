#!/usr/bin/env python3
"""
يجلب أحدث الكونفيجات من قناة تيليجرام العامة (عن طريق صفحة المعاينة t.me/s/...)
ويبني ملف اشتراك (Subscription) لبرنامج v2rayNG.

- يأخذ أحدث 50 كونفيج فقط (الأحدث أولاً)
- يحذف المكرر
- يعيد تسمية كل كونفيج إلى:  Sabah 1 🇳🇱 , Sabah 2 🇳🇱 ... مع الإبقاء على العلم الأصلي
- يكتب:  sub.txt (Base64 - هذا رابط الاشتراك)  و  sub_raw.txt (نص عادي)

لا يحتاج أي مكتبات خارجية.
"""
import base64
import html
import json
import re
import sys
import urllib.parse
import urllib.request

# ===================== الإعدادات =====================
CHANNEL = "TheFreeConfigs"   # اسم القناة (بدون @)
MAX_CONFIGS = 50             # عدد الكونفيجات المطلوب
NAME_PREFIX = "Sabah"        # الاسم الجديد
MAX_PAGES = 30               # أقصى عدد صفحات يرجع لها في تاريخ القناة
# =====================================================

PROTOCOLS = ["hysteria2", "hysteria", "wireguard", "vless", "vmess",
             "trojan", "tuic", "ssr", "hy2", "ss"]
# (?<![A-Za-z0-9]) حتى لا يلتقط "ss://" من داخل "vless://"
LINK_RE = re.compile(r'(?<![A-Za-z0-9])(?:%s)://[^\s<>"\'`]+' % "|".join(PROTOCOLS),
                     re.IGNORECASE)
FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
POST_RE = re.compile(r'data-post="%s/(\d+)"' % re.escape(CHANNEL), re.IGNORECASE)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def html_to_text(fragment):
    fragment = re.sub(r"<br\s*/?>|</(?:div|p|pre|code|blockquote)>", "\n",
                      fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment)


def parse_page(page_html):
    """يرجع قائمة [(رقم_الرسالة, [روابط...]), ...] بترتيب الصفحة (الأقدم ثم الأحدث)."""
    messages = []
    matches = list(POST_RE.finditer(page_html))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page_html)
        text = html_to_text(page_html[start:end])
        links = [l.rstrip(".,;)]}") for l in LINK_RE.findall(text)]
        messages.append((int(m.group(1)), links))
    return messages


def b64decode_any(s):
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8")


def get_flag(name):
    m = FLAG_RE.search(name or "")
    return m.group(0) if m else ""


def make_name(n, flag):
    return f"{NAME_PREFIX} {n} {flag}".strip()


def analyze(link):
    """يرجع (مفتاح_لمنع_التكرار, العلم) أو None إذا الرابط غير صالح."""
    scheme = link.split("://", 1)[0].lower()
    if scheme == "vmess":
        try:
            data = json.loads(b64decode_any(link.split("://", 1)[1].split("#")[0]))
        except Exception:
            return None
        flag = get_flag(data.get("ps", ""))
        key_data = {k: v for k, v in data.items() if k != "ps"}
        return "vmess:" + json.dumps(key_data, sort_keys=True), flag
    base, _, frag = link.partition("#")
    if len(base) < 12:
        return None
    return base, get_flag(urllib.parse.unquote(frag))


def rename(link, n, flag):
    scheme = link.split("://", 1)[0].lower()
    new_name = make_name(n, flag)
    if scheme == "vmess":
        data = json.loads(b64decode_any(link.split("://", 1)[1].split("#")[0]))
        data["ps"] = new_name
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return "vmess://" + base64.b64encode(raw).decode()
    base = link.split("#", 1)[0]
    return base + "#" + urllib.parse.quote(new_name)


def collect():
    seen, result = set(), []
    url = f"https://t.me/s/{CHANNEL}"
    for _ in range(MAX_PAGES):
        page = fetch(url)
        messages = parse_page(page)
        if not messages:
            break
        # الأحدث أولاً، مع الحفاظ على ترتيب الكونفيجات داخل نفس الرسالة
        for _msg_id, links in sorted(messages, key=lambda x: -x[0]):
            for link in links:
                info = analyze(link)
                if not info or info[0] in seen:
                    continue
                seen.add(info[0])
                result.append((link, info[1]))
                if len(result) >= MAX_CONFIGS:
                    return result
        oldest = min(m[0] for m in messages)
        if oldest <= 1:
            break
        url = f"https://t.me/s/{CHANNEL}?before={oldest}"
    return result


def main():
    try:
        configs = collect()
    except Exception as e:
        print("خطأ أثناء الجلب:", e)
        sys.exit(1)

    if not configs:
        # لا نمسح الملف القديم إذا فشل الجلب
        print("لم يتم العثور على أي كونفيج - لن يتم تعديل الملفات.")
        sys.exit(1)

    final = [rename(link, i, flag) for i, (link, flag) in enumerate(configs, 1)]
    raw = "\n".join(final) + "\n"

    with open("sub_raw.txt", "w", encoding="utf-8") as f:
        f.write(raw)
    with open("sub.txt", "w", encoding="utf-8") as f:
        f.write(base64.b64encode(raw.encode("utf-8")).decode())

    print(f"تم: {len(final)} كونفيج")
    for line in final[:5]:
        print("  ", urllib.parse.unquote(line)[:120])


if __name__ == "__main__":
    main()
