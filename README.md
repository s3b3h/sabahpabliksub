# Sabah Public Sub

اشتراك v2rayNG يتحدث تلقائياً من قنوات تيليجرام.

## المنابع
| القناة | العدد | الأرقام | الاختصار في الاسم | إذا ما عُرف العلم |
|---|---|---|---|---|
| @TheFreeConfigs | آخر 50 غير مكرر | Sabah 1 → 50 | `Free` | 🌐 |
| @tootffarangi2 | آخر 100 غير مكرر | Sabah 51 → 150 | `Toot` | 🍓 |
| @v2rei | آخر 100 غير مكرر | Sabah 151 → 250 | `V2rei` | ⚡ |
| @proxy_mtproto_vpns_free | آخر 100 غير مكرر | Sabah 251 → 350 | `Proxy` | 🔰 |
| @ettehad_vpn | آخر 100 غير مكرر | Sabah 351 → 450 | `Etehad` | 🔥 |

المجموع: حتى 450 كونفيج.

شكل الاسم: `Sabah 151 🇩🇪 V2rei` (رقم + علم الدولة + اختصار القناة).

## المميزات
- تخطي سيرفرات الشادوساكس (`ss://` و `ssr://`) — ما تدخل في الاشتراك ولا في العدد (`SKIP_PROTOCOLS`).
- حذف التكرار الذكي (نفس السيرفر حتى لو اختلف ترتيب الباراميترات/الاسم/fp/sid/spx) وعبر كل المنابع.
- علم دولة السيرفر الحقيقية عن طريق GeoIP (ip-api.com)، وإذا كان السيرفر خلف CDN يستخدم العلم الأصلي أو الإيموجي البديل.
- اختصار القناة في آخر الاسم لمعرفة مصدر كل كونفيج.
- تخطي العناوين الداخلية/الوهمية (127.0.0.1 ، 10.x ، 0.0.0.0 ...) والروابط الناقصة (`SKIP_PRIVATE_HOSTS`).
- إعادة المحاولة عند فشل الاتصال أو الحظر المؤقت من تيليجرام.
- إذا فشل منبع، تبقى كونفيجاته القديمة.
- اشتراك منفصل لكل قناة في مجلد `subs/` (`WRITE_PER_CHANNEL`).
- يتحدث كل 30 دقيقة عبر GitHub Actions (`.github/workflows/update.yml`) + تشغيل يدوي + تشغيل تلقائي بعد تعديل السكربت.

## رابط الاشتراك (الكل)
```
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/sub.txt
```

## روابط منفصلة لكل قناة
```
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/subs/Free.txt
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/subs/Toot.txt
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/subs/V2rei.txt
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/subs/Proxy.txt
https://raw.githubusercontent.com/s3b3h/sabahpabliksub/main/subs/Etehad.txt
```
(تظهر بعد أول تشغيل للـ Action)

## الإعدادات
في أعلى `scrape.py`: `SOURCES` (إضافة/حذف قنوات، العدد، الاختصار `tag`)، `SKIP_PROTOCOLS`، `USE_GEOIP`، `NAME_PREFIX`، `SKIP_PRIVATE_HOSTS`، `WRITE_PER_CHANNEL`.
