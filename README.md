# رابط اشتراك v2rayNG تلقائي من قناة TheFreeConfigs

يجلب أحدث **50** كونفيج من قناة https://t.me/TheFreeConfigs كل ساعة عبر GitHub Actions (مجاني)،
ويحذف القديم والمكرر، ويغيّر الأسماء إلى `Sabah 1 🇳🇱` … `Sabah 50 🇳🇱` مع الإبقاء على العلم الأصلي.

## الملفات
| الملف | الوظيفة |
|---|---|
| `scrape.py` | سكربت الجلب (بايثون، بدون مكتبات خارجية) |
| `.github/workflows/update.yml` | يشغّل السكربت كل ساعة ويرفع النتيجة |
| `sub.txt` | **رابط الاشتراك** (Base64) – يُنشأ تلقائياً |
| `sub_raw.txt` | نفس الكونفيجات كنص عادي – يُنشأ تلقائياً |

## طريقة التركيب على GitHub
1. أنشئ مستودع جديد **Public** (مثلاً `my-sub`).
2. ارفع الملفين `scrape.py` و `.github/workflows/update.yml` بنفس المسارات.
3. من **Settings → Actions → General → Workflow permissions** اختر **Read and write permissions** ثم Save.
4. من تبويب **Actions** → `Update V2Ray Subscription` → **Run workflow** (أول تشغيل يدوي).
5. رابط الاشتراك لـ v2rayNG:
   ```
   https://raw.githubusercontent.com/USERNAME/my-sub/main/sub.txt
   ```
6. في v2rayNG: القائمة ← Subscription group setting ← + ← الصق الرابط، ثم «Update subscription».

## الإعدادات
في أعلى `scrape.py`: `CHANNEL` ، `MAX_CONFIGS` ، `NAME_PREFIX`.

## ملاحظات
- يعتمد على صفحة المعاينة العامة `t.me/s/TheFreeConfigs` (لا يحتاج بوت أو توكن).
- إذا فشل الجلب لا يُمسح الملف القديم.
- جدولة GitHub قد تتأخر بضع دقائق، وقد يوقف GitHub الجدولة بعد 60 يوم بدون نشاط في المستودع (تفعيلها بضغطة من تبويب Actions).
