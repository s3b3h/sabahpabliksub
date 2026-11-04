# Sabah Public Sub

اشتراك v2rayNG يتحدث تلقائياً من قنوات تيليجرام.

## المنابع (آخر 50 غير مكرر من كل قناة)

| القناة | الأرقام | الاختصار |
|---|---|---|
| @TheFreeConfigs | Sabah 1 → 50 | Free |
| @tootffarangi2 | Sabah 51 → 100 | Toot |
| @v2rei | Sabah 101 → 150 | V2rei |
| @proxy_mtproto_vpns_free | Sabah 151 → 200 | Proxy |
| @ettehad_vpn | Sabah 201 → 250 | Etehad |
| @DailyCfg | Sabah 251 → 300 | Daily |
| @v2ray_dalghak | Sabah 301 → 350 | Dalghak |

## المميزات

- بدون أي فلتر فحص اتصال: يضيف آخر 50 من كل قناة كما هي.
- حذف التكرار: نفس (UUID/باسورد + IP + بورت) يُحذف عبر كل القنوات، ويُجلب بدله كونفيج أقدم.
- علم دولة السيرفر عن طريق GeoIP.
- إذا فشلت قناة، تبقى كونفيجاتها القديمة.
- يتحدث كل ساعتين عبر GitHub Actions (`.github/workflows/update.yml`) + تشغيل يدوي + تشغيل بعد تعديل السكربت.

## رابط الاشتراك

```
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/sub.txt
```

## الإعدادات

في أعلى `scrape.py`: `SOURCES` (إضافة/حذف قنوات)، `PER_CHANNEL`، `USE_GEOIP`، `NAME_PREFIX`.
