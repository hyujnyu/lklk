# Storage Helper — APK Builder (GitHub Actions)

مجلد جاهز للرفع على GitHub، يبني الـ APK تلقائياً بالسحابة. **ما يحتاج تثبت شي على جهازك.**

## المحتويات

```
.
├── main.py                          ← كود العقدة (لا تغير اسمه)
├── buildozer.spec                   ← إعدادات البناء
└── .github/workflows/build.yml      ← سكربت البناء التلقائي
```

## الخطوات (5 دقائق إعداد، 30-40 دقيقة بناء)

1. **أنشئ حساب GitHub** على https://github.com (لو ما عندك).

2. **New repository**:
   - اسم: أي شي، مثلاً `storage-helper`
   - اختر **Private** (لا تخلقه Public - تطبيقك خاص).
   - لا تختر README ولا .gitignore.
   - اضغط **Create repository**.

3. **ارفع الملفات** (أسهل طريقة - من المتصفح):
   - فك ضغط المجلد هذا.
   - في صفحة الـ repo الفارغة، اضغط **uploading an existing file**.
   - اسحب `main.py` و `buildozer.spec` فقط (الجذر).
   - ثم افتح مجلد `.github/workflows` واسحب `build.yml`... لكن GitHub ما يقبل رفع مجلدات فاضية.
   - **الطريقة الصحيحة**: ارفع `main.py` و `buildozer.spec` أولاً، بعدين:
     - اضغط **Add file → Create new file**.
     - اكتب بالاسم: `.github/workflows/build.yml` (هيك تنشئ المجلدات تلقائياً).
     - انسخ محتوى `build.yml` من ملفك وضعه هناك.
     - Commit changes.

4. **شغّل البناء**:
   - اضغط تبويب **Actions** بالأعلى.
   - سترى workflow باسم "Build Storage Helper APK".
   - انتظر 30-40 دقيقة (أول مرة تنزل Android SDK/NDK).
   - لما يصير بعلامة ✓ أخضر، البناء نجح.

5. **نزّل الـ APK**:
   - ادخل على الـ run الأخضر.
   - انزل لأسفل قسم **Artifacts**.
   - اضغط **storage-helper-apk** → ينزل ملف ZIP فيه الـ APK.
   - فكه → ثبته على الهاتف.

## لو فشل البناء

- اضغط على الـ run الحمراء.
- افتح خطوة "Build debug APK" واقرأ آخر 20 سطر.
- الأسباب الشائعة:
  - خطأ Python syntax في `main.py` → اصلحه وارفع نسخة جديدة.
  - نقص مكتبة في `requirements` بـ `buildozer.spec` → أضفها.

## معلومات تقنية

- Runner: Ubuntu LTS على سيرفرات GitHub.
- Java 17 (Temurin).
- Python 3.11.
- Buildozer + Cython 0.29.36.
- الناتج: `bin/storagehelper-1.0.0-debug.apk`.
