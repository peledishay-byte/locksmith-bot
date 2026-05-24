# Locksmith Bot

בוט שיחה לדף פייסבוק של עסק לוקסמית' באוהיו. עונה ללקוחות אוטומטית, וכשהוא לא בטוח - מעביר לקבוצת טלגרם של החברה. תשובות שכותב צוות החברה ב-Reply בטלגרם נשלחות חזרה ללקוח בפייסבוק.

---

## איך זה עובד

```
   לקוח בפייסבוק
        │ הודעה
        ▼
  Webhook /webhook/facebook  ──►  Claude (עם system prompt של לוקסמית')
        │                                │
        │ אם Claude בטוח:                │ אם לא בטוח/שאלה על מחיר/דחוף:
        │ שולח תשובה חזרה ללקוח         │ קורא ל-tool escalate_to_team
        │                                ▼
        │                        sendToCompanyGroup()
        │                                │
        │                                ▼
        │                       קבוצת הטלגרם של החברה
        │                                │
        │                                │ איש צוות עושה Reply להודעה
        │                                ▼
        │                  Webhook /webhook/telegram
        │                                │
        └◄────── שליחת התשובה חזרה ללקוח בפייסבוק ─┘
```

הקסם: צוות החברה משתמש בפיצ'ר ה-**Reply** הרגיל של טלגרם. הבוט מזהה לפי `reply_to_message_id` לאיזה לקוח התשובה שייכת.

---

## מבנה הפרויקט

```
locksmith-bot/
├── package.json
├── railway.json              ← תצורת Railway
├── .env.example              ← העתק ל-.env ומלא ערכים
├── src/
│   ├── index.js              ← Express server
│   ├── facebook.js           ← Webhook + Send API של Messenger
│   ├── telegram.js           ← Webhook + Bot API של טלגרם
│   ├── claude.js             ← agentic loop עם Anthropic SDK
│   ├── knowledge.js          ← system prompt + הגדרות tools
│   ├── aks-client.js         ← API client של American Key Supply
│   ├── sheets.js             ← קריאה/כתיבה ל-Google Sheets
│   └── db.js                 ← SQLite (שיחות, הודעות, escalations)
└── scripts/
    ├── register-telegram-webhook.js  ← הרצה חד-פעמית אחרי deploy
    └── sync-aks-to-sheets.js         ← סנכרון קטלוג המפתחות → Google Sheet
```

---

## הקמה - שלב אחר שלב

### שלב 1: יצירת בוט טלגרם (~5 דק')

1. בטלגרם, פתח שיחה עם **@BotFather**.
2. שלח `/newbot`. תן לו שם (למשל "Buckeye Locksmith Bot") וכינוי (`@buckeye_locksmith_bot`).
3. שמור את ה-**Bot Token** שתקבל - זה ה-`TELEGRAM_BOT_TOKEN`.
4. שלח ל-BotFather `/setprivacy` ובחר **Disable** עבור הבוט שלך - זה מאפשר לו לראות את כל ההודעות בקבוצה (כולל replies).
5. צור קבוצת טלגרם לחברה ("Buckeye Locksmith - Dispatch") והוסף את הבוט שיצרת **כאדמין**.
6. כדי לקבל את ה-Chat ID של הקבוצה: שלח הודעה כלשהי בקבוצה, ואז גש מהדפדפן ל:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
   חפש `"chat":{"id":-1001234567890,...}`. זה ה-`TELEGRAM_GROUP_CHAT_ID` (כולל המינוס).

### שלב 2: יצירת Meta App + Page Access Token (~20 דק')

1. גש ל-[developers.facebook.com](https://developers.facebook.com/) והירשם כמפתח.
2. **My Apps → Create App → Business → Next**. תן שם ("Buckeye Locksmith Bot").
3. ב-Dashboard של ה-App: **Add Product → Messenger → Set Up**.
4. בעמוד הגדרות Messenger:
   - **Generate Access Token** → בחר את דף הפייסבוק של העסק → תן לאפליקציה הרשאות `pages_messaging`. שמור את ה-Token שיופיע - זה ה-`FB_PAGE_ACCESS_TOKEN`.
5. **Settings → Basic → App Secret → Show**. שמור - זה ה-`FB_APP_SECRET`.
6. אל תגדיר את ה-webhook עדיין - נחזור לזה בשלב 5 (אחרי deploy).

> **שים לב:** עד שתעבור App Review של Meta, הבוט יוכל לשוחח רק עם משתמשים שהם Admin/Developer/Tester של ה-App. זה מספיק לבדיקות. ל-production תצטרך Advanced Access ל-`pages_messaging` (אפשר להגיש בקשה דרך App Review).

### שלב 3: מפתח Anthropic API (~5 דק')

1. גש ל-[console.anthropic.com](https://console.anthropic.com/), הירשם.
2. **Plans & Billing** - הוסף 5-10$ קרדיט.
3. **API Keys → Create Key**. שמור - זה ה-`ANTHROPIC_API_KEY`.

### שלב 4: Deploy ל-Railway (~10 דק')

1. דחוף את התיקייה הזו ל-GitHub (כ-private repo).
2. גש ל-[railway.app](https://railway.app/), הירשם עם GitHub.
3. **New Project → Deploy from GitHub repo** → בחר את ה-repo.
4. לאחר ה-deploy הראשון, ב-Tab **Settings → Networking → Generate Domain**. שמור את ה-URL שתקבל (למשל `https://locksmith-bot-production.up.railway.app`) - זה ה-`PUBLIC_BASE_URL`.
5. ב-Tab **Variables**, הוסף את כל המשתנים מ-`.env.example`:
   ```
   ANTHROPIC_API_KEY=...
   FB_PAGE_ACCESS_TOKEN=...
   FB_VERIFY_TOKEN=<בחר מחרוזת אקראית כלשהי>
   FB_APP_SECRET=...
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_GROUP_CHAT_ID=...
   TELEGRAM_WEBHOOK_SECRET=<בחר מחרוזת אקראית כלשהי>
   PUBLIC_BASE_URL=https://...
   BUSINESS_NAME=Buckeye Locksmith
   BUSINESS_AREA=Ohio (Columbus, Cleveland, Cincinnati, ...)
   BUSINESS_HOURS=24/7 emergency, office Mon-Fri 8am-6pm ET
   ```
6. Railway יבצע redeploy אוטומטית אחרי הוספת המשתנים.
7. וודא שהשרת רץ ע"י ביקור ב-`https://your-app.up.railway.app/` - אמור להחזיר `{"ok":true,"name":"locksmith-bot"}`.

> **לגבי ה-DB:** SQLite נשמר בקובץ מקומי על ה-container של Railway. ל-MVP זה בסדר. כשתרצה persistent volume אמיתי, צרף ב-Railway **Volume** ל-`/app/data` והגדר `DB_PATH=/app/data/bot.db` במשתנים.

### שלב 5: חיבור ה-webhook של פייסבוק

1. חזור ל-Meta App Dashboard → **Messenger → Settings → Webhooks → Add Callback URL**.
2. **Callback URL:** `https://your-app.up.railway.app/webhook/facebook`
3. **Verify Token:** הערך שבחרת ל-`FB_VERIFY_TOKEN`.
4. לחץ **Verify and Save** - אם הכל תקין, יאמת תוך שנייה.
5. ב-**Webhook Fields** סמן ✅ `messages` ו-`messaging_postbacks`.
6. ב-**Add or Remove Pages → Add Subscriptions** וודא שהדף שלך מופיע ועם ✅ ל-`messages`.

### שלב 6: חיבור ה-webhook של טלגרם

הרץ פעם אחת מקומית (`.env` שלך אמור להיות מלא):
```bash
npm install
npm run register-telegram-webhook
```
התשובה אמורה להיות `{"ok":true,"result":true,"description":"Webhook was set"}`.

### שלב 7: בדיקה מקצה לקצה

1. שלח הודעה לדף הפייסבוק שלך ("Hi, do you do car lockouts in Columbus?") - הבוט צריך לענות תוך כמה שניות.
2. שאל שאלה שמחייבת escalation ("How much for opening a 2024 Tesla in Cleveland?") - אמור להופיע פוסט בקבוצת הטלגרם, והלקוח אמור לקבל הודעה מרגיעה.
3. בקבוצת הטלגרם, עשה **Reply** להודעת ה-escalation וכתוב משהו - הטקסט אמור להישלח חזרה ללקוח בפייסבוק.

---

## קטלוג המפתחות (Google Sheets ↔ American Key Supply)

מעבר לבסיס הידע הסטטי ב-`knowledge.js`, הבוט שולף **מחירים ותמונות מפתחות** מטבלת Google Sheets שמתעדכנת מהקטלוג של American Key Supply. כשלקוח שואל "כמה עולה מפתח ל-2018 Honda Civic", הבוט קורא ל-tool בשם `lookup_key_for_vehicle`, מקבל סוג מפתח + תמונה + מחיר, ומצטט ושולח את התמונה לאישור.

### שלב 8: הקמת Google Cloud + Service Account (~15 דק')

1. גש ל-[console.cloud.google.com](https://console.cloud.google.com/), הירשם (חינם).
2. **Create Project** → תן שם ("locksmith-bot") → Create.
3. בתפריט החיפוש הקלד "Google Sheets API" → **Enable**.
4. **APIs & Services → Credentials → Create Credentials → Service Account**:
   - Name: `locksmith-bot-sa`
   - Role: דלג (לא צריך).
   - Done.
5. הקלק על ה-Service Account שיצרת → טאב **Keys → Add Key → Create new key → JSON**. הקובץ ירד.
6. פתח את הקובץ ב-Notepad - **כל התוכן** של ה-JSON ייכנס למשתנה `GOOGLE_SERVICE_ACCOUNT_JSON` ב-Railway (כשורה אחת).
7. שמור גם את הכתובת `client_email` מהקובץ - היא נראית כמו `locksmith-bot-sa@your-project.iam.gserviceaccount.com`.

### שלב 9: הקמת ה-Google Sheet

1. גש ל-[sheets.google.com](https://sheets.google.com/) ויצור גיליון חדש בשם "Locksmith Keys Catalog".
2. הקלק על **Share** ולחץ הדבקה של ה-`client_email` של ה-Service Account מהשלב הקודם, עם הרשאת **Editor**.
3. העתק מ-URL את ה-ID של הגיליון (החלק הארוך בין `/d/` ל-`/edit`) - זה ה-`GOOGLE_SHEET_ID`.
4. אל תוסיף עמודות ידנית - הסקריפט יבנה אותן בעצמו.

### שלב 10: הוספת אישורי American Key Supply ל-`.env`

תלוי איך AKS נתנו לך גישה, מלא אחד משלושת הסטים:

- אם נתנו לך **email + password** של חשבון API (נפוץ):
  ```
  AKS_AUTH_MODE=login
  AKS_EMAIL=...
  AKS_PASSWORD=...
  ```
- אם נתנו לך **Bearer token**:
  ```
  AKS_AUTH_MODE=bearer
  AKS_API_TOKEN=...
  ```
- אם נתנו לך **API key + שם header**:
  ```
  AKS_AUTH_MODE=header
  AKS_API_KEY_HEADER=X-API-Key
  AKS_API_KEY=...
  ```

אם לא בטוח - נסה את הראשון. אם תקבל שגיאת אימות מהסקריפט, תכתוב לי איזו שגיאה ונכוון.

### שלב 11: הרצת הסנכרון הראשון

```bash
npm run sync-keys
```

הסקריפט ימשוך את כל הקטלוג, יפשט (כל זוג רכב/מפתח שורה נפרדת), ויכתוב לגיליון. שורה ראשונה היא header. עמודת **Markup %** מוגדרת ל-60% כברירת מחדל - תשנה ערכים פר-שורה אם תרצה תמחור לפי דגם. עמודת **Customer Price (USD)** היא מה שהבוט מצטט ללקוח.

> **חשוב:** אם תערוך ידנית את העמודה Markup % או Customer Price, **אל תריץ עוד פעם** `sync-keys` - זה ידרוס. במקום, צור עוד גיליון/תהליך לעריכה. אפשרות פשוטה: להריץ סנכרון לגיליון `Keys_Raw`, ולעבוד עם VLOOKUP בגיליון נפרד שעורכים. אם תרצה - תגיד ואני אבנה לך את זה.

### שלב 12: סנכרון אוטומטי (אופציונלי)

ב-Railway → **Plugins → Cron**. הוסף job שמריץ פעם ביום (`0 6 * * *`) את `npm run sync-keys`. ככה הקטלוג תמיד עדכני בלי שתצטרך לזכור.

---

## איפה לערוך מה

| רוצה לשנות | תערוך |
|---|---|
| שירותים, אזור, שעות, סגנון תשובות | `src/knowledge.js` |
| מתי הבוט מסלים | `src/knowledge.js` (סעיף "What you MUST escalate") |
| פורמט הודעת ה-escalation בטלגרם | `src/facebook.js` (פונקציית `handleMessagingEvent`) |
| מודל Claude (sonnet/haiku/opus) | משתנה הסביבה `ANTHROPIC_MODEL` |
| כמה הודעות זיכרון לבוט | `src/facebook.js` (`getRecentMessages(..., 20)`) |

---

## עלויות צפויות

- **Railway:** ~5$/חודש (Hobby plan).
- **Anthropic API:** עם Sonnet 4.5, ~0.003$ לבקשה. 100 שיחות/יום של 5 הודעות = ~45$/חודש. אם זה יקר מדי, שנה ל-`claude-haiku-4-5` במשתנה `ANTHROPIC_MODEL` - יחתוך את העלות פי 5.
- **Facebook + Telegram:** חינם.

---

## שיפורים שכדאי להוסיף בעתיד

- Dashboard פנימי לראות את כל השיחות.
- שליחת תמונות (מפתחות, נעילות) - כרגע הבוט מתעלם מהן.
- תור עבודות (מספר שיחות במקביל יוצרות הרבה עומס על Claude).
- WhatsApp Business דרך אותו backend.
- App Review של Meta כדי לעבוד עם הציבור הרחב (חובה ל-production).
