import feedparser
import requests
import time
import calendar
import html
import json
import re
import os
import logging
from pathlib import Path
from urllib.parse import quote
from deep_translator import GoogleTranslator

# ---------- הגדרות ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE")
CHAT_ID = os.environ.get("CHAT_ID", "PUT_YOUR_CHANNEL_CHAT_ID_HERE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def google_news_feed(query: str, hl: str, gl: str) -> str:
    """בונה URL של Google News RSS Search עבור שאילתה נתונה, בשפה/מדינה נתונה."""
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={gl}:{hl}"


# שאילתות חיפוש לזיהוי שיח על רשתות בוטים/חשבונות מתואמים בעד/נגד ישראל
BOT_ACTIVITY_QUERIES = [
    ('("bot network" OR "troll farm" OR "coordinated inauthentic behavior" OR "fake accounts") '
     'AND (Israel OR Gaza OR antisemitism)', "en", "US"),
    ('("rete di bot" OR "account falsi" OR "comportamento coordinato non autentico") AND Israele',
     "it", "IT"),
    ('("Bot-Netzwerk" OR "gefälschte Konten" OR "koordiniertes unechtes Verhalten") AND Israel',
     "de", "DE"),
    ('("δίκτυο bot" OR "ψεύτικοι λογαριασμοί") AND Ισραήλ', "el", "GR"),
]
# חיסכון בעלויות: רק אנגלית וגרמנית פעילות. להחזרת שפות נוספות - להוסיף "it", "el".
ENABLED_BOT_QUERY_LANGS = {"en", "de"}
BOT_ACTIVITY_FEEDS = [
    google_news_feed(q, hl, gl) for q, hl, gl in BOT_ACTIVITY_QUERIES if hl in ENABLED_BOT_QUERY_LANGS
]

RSS_FEEDS = [
    # איטליה
    "https://www.ansa.it/sito/ansait_rss.xml",                  # ANSA
    "https://xml2.corriereobjects.it/rss/homepage.xml",         # Corriere della Sera
    "https://www.repubblica.it/rss/politica/rss2.0.xml",        # La Repubblica - פוליטיקה
    "https://www.repubblica.it/rss/cronaca/rss2.0.xml",         # La Repubblica - חדשות/פלילים
    "https://www.lastampa.it/rss/copertina.xml",                # La Stampa
    "https://moked.it/international/feed/",                     # UCEI - קהילת יהדות איטליה
    # גרמניה
    "https://www.tagesschau.de/xml/rss2/",                      # Tagesschau (ARD)
    "https://www.spiegel.de/schlagzeilen/index.rss",            # Der Spiegel
    "https://www.welt.de/feeds/latest.rss",                     # Die Welt
    "https://www.faz.net/rss/aktuell/politik/",                 # FAZ - פוליטיקה (נבדק: כרגע מחזיר ריק, יש אבחון בלוג)
    "https://www.deutschlandfunk.de/politikportal-100.rss",     # Deutschlandfunk - פוליטיקה (רדיו ציבורי)
    # (Zentralrat der Juden הוסר - השרת חוסם/לא זמין באופן עקבי)
    # יוון - בשפה המקומית (יוונית)
    "https://www.tanea.gr/feed/",                                # TA NEA
    # קפריסין - בשפה המקומית (יוונית)
    "https://politis.com.cy/feed/",                              # Politis
] + BOT_ACTIVITY_FEEDS

# מילות מפתח fallback - למקרה שקריאת ה-AI נכשלת
FALLBACK_KEYWORDS = [
    # איטלקית
    "israele", "israeliano", "ebrei", "ebraico", "antisemitismo", "gaza", "hamas",
    "immigrazione", "migranti", "migrante", "sbarchi", "clandestini", "rimpatri",
    "islam", "islamico", "moschea", "musulmani", "sharia", "imam", "islamizzazione",
    "propalestinese", "pro-palestina", "boicottaggio israele", "antisionismo",
    # גרמנית
    "israel", "israelisch", "juden", "jüdisch", "antisemitismus",
    "einwanderung", "migranten", "migration", "asylbewerber", "abschiebung", "flüchtlinge",
    "islamisch", "moschee", "muslime", "scharia", "islamisierung",
    "propalästinensisch", "pro-palästina", "israel-boykott", "antizionismus",
    # יוונית
    "ισραηλ", "ισραηλινος", "εβραιοι", "αντισημιτισμος", "χαμας",
    "μεταναστευση", "μεταναστες", "προσφυγες", "απελασεις",
    "ισλαμ", "ισλαμικος", "μουσουλμανοι", "τζαμι", "σαρια",
    "υπερ παλαιστινης", "παλαιστινιακο", "αντισιωνισμος",
    # פעילות בוטים
    "bot network", "troll farm", "coordinated inauthentic behavior", "fake accounts",
    "bot-netzwerk", "gefälschte konten", "rete di bot", "account falsi",
    "δικτυο bot", "ψευτικοι λογαριασμοι",
]

# אם מחובר דיסק קבוע (Volume) ב-Railway, הוא מסופק במשתנה RAILWAY_VOLUME_MOUNT_PATH והקובץ נשמר עליו
# ולכן שורד פריסות. בלי דיסק - הקובץ נשמר בתיקייה הרגילה ונמחק בכל פריסה.
VOLUME_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
SEEN_LINKS_FILE = Path(VOLUME_PATH or ".") / "seen_links.json"
POLL_INTERVAL_SECONDS = 120
MAX_SEEN_LINKS = 5000
IMPORTANCE_THRESHOLD = "low"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

IMPORTANCE_RANK = {"low": 1, "medium": 2, "high": 3}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("italy_alerts_bot")


def load_seen_links() -> dict:
    """מחזיר מילון מסודר (לפי סדר ההוספה) - מפתחות הם קישורים. המסודר חשוב כדי שבחיתוך יימחקו הישנים."""
    if SEEN_LINKS_FILE.exists():
        try:
            with open(SEEN_LINKS_FILE, "r", encoding="utf-8") as f:
                return dict.fromkeys(json.load(f), True)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"לא הצלחתי לטעון {SEEN_LINKS_FILE.name} ({e}), מתחיל מרשימה ריקה")
    return {}


def save_seen_links(seen_links: dict):
    # חיתוך: מוחקים את הקישורים הישנים ביותר (הראשונים במילון)
    while len(seen_links) > MAX_SEEN_LINKS:
        del seen_links[next(iter(seen_links))]
    try:
        SEEN_LINKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_LINKS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(seen_links), f, ensure_ascii=False)
        os.replace(tmp, SEEN_LINKS_FILE)  # החלפה אטומית: קובץ לא יישאר חצי-כתוב אם התהליך נעצר באמצע
    except OSError as e:
        logger.error(f"נכשל בשמירת {SEEN_LINKS_FILE.name}: {e}")


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def fallback_is_important(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(word in text for word in FALLBACK_KEYWORDS)


LEVEL_EMOJI = {"high": "🔴", "medium": "🟠", "low": "🟡"}

SOURCE_INFO = {
    "ansa.it": {"flag": "🇮🇹", "country": "איטליה", "source_name": "ANSA"},
    "corriere": {"flag": "🇮🇹", "country": "איטליה", "source_name": "Corriere della Sera"},
    "repubblica.it": {"flag": "🇮🇹", "country": "איטליה", "source_name": "La Repubblica"},
    "lastampa.it": {"flag": "🇮🇹", "country": "איטליה", "source_name": "La Stampa"},
    "moked.it": {"flag": "🕎", "country": "קהילה יהודית - איטליה", "source_name": "UCEI (Moked)"},
    "tagesschau.de": {"flag": "🇩🇪", "country": "גרמניה", "source_name": "Tagesschau"},
    "spiegel.de": {"flag": "🇩🇪", "country": "גרמניה", "source_name": "Der Spiegel"},
    "welt.de": {"flag": "🇩🇪", "country": "גרמניה", "source_name": "Die Welt"},
    "faz.net": {"flag": "🇩🇪", "country": "גרמניה", "source_name": "FAZ"},
    "deutschlandfunk.de": {"flag": "🇩🇪", "country": "גרמניה", "source_name": "Deutschlandfunk"},
    "zentralratderjuden.de": {"flag": "🕎", "country": "קהילה יהודית - גרמניה", "source_name": "Zentralrat der Juden"},
    "tanea.gr": {"flag": "🇬🇷", "country": "יוון", "source_name": "TA NEA"},
    "politis.com.cy": {"flag": "🇨🇾", "country": "קפריסין", "source_name": "Politis"},
    "news.google.com": {"flag": "🔍", "country": "חיפוש רשת", "source_name": "Google News search"},
}


def get_source_info(link: str) -> dict:
    for domain, info in SOURCE_INFO.items():
        if domain in link:
            return info
    return {"flag": "🌍", "country": "לא ידוע", "source_name": "מקור לא מזוהה"}


def extract_individual_results(raw_text: str) -> list:
    """
    מנסה לחלץ אובייקטי JSON תקינים בודדים מתוך מערך שבור בכללותו.
    זה קורה כשל-Claude יש שגיאת escape במקום אחד, וכל שאר המערך היה תקין.
    """
    results = []
    for match in re.finditer(r'\{\s*"link"\s*:.*?\}(?=\s*[,\]])', raw_text, re.DOTALL):
        try:
            results.append(json.loads(match.group(0)))
        except json.JSONDecodeError:
            continue  # הפריט הספציפי הזה שבור - מדלגים רק עליו, לא על כל השאר
    return results


CLAUDE_COOLDOWN_SECONDS = 600  # 10 דקות - כמה זמן "לנוח" מ-Claude אחרי שגילינו בעיית קרדיט/מפתח
_claude_unavailable_until = 0.0


def classify_with_claude(entries: list) -> dict:
    global _claude_unavailable_until

    if not ANTHROPIC_API_KEY or not entries:
        return {}

    # מעגל ניתוק: אם כבר גילינו שאין קרדיט/מפתח לא תקין, לא ממשיכים לדפוק על אותה דלת
    # בכל באצ' - זה רק מציף לוגים ומבזבז זמן. ממתינים קצת ומנסים שוב מאוחר יותר.
    if time.time() < _claude_unavailable_until:
        return {}

    items_for_prompt = [
        {
            "link": e["link"],
            "title": e["title"],
            "summary": strip_html(e["summary"])[:300],
            "source_country": get_source_info(e["link"])["country"],
        }
        for e in entries
    ]

    system_prompt = (
        "אתה מסנן חדשות ממוקד-נושא עבור צרכן ישראלי שעוקב אחרי איטליה, גרמניה, יוון וקפריסין. "
        "הכתבות עשויות להיות באיטלקית, בגרמנית, ביוונית או באנגלית. "
        "לכל כתבה מצורף שדה 'source_country' שמציין מאיפה היא הגיעה - זה רמז חשוב.\n\n"
        "בדוק אם הכתבה שייכת לאחד מהנושאים הבאים בלבד:\n"
        "1. הקשר ישיר לישראל: ישראל, ישראלים, יהודים, קהילות יהודיות, אנטישמיות, "
        "פשעי שנאה נגד יהודים, יחסי המדינה עם ישראל.\n"
        "2. הגירה במדינה: מדיניות הגירה, גלי הגירה, מהגרים/פליטים, ויכוחים ציבוריים על הגירה.\n"
        "3. אסלאם והתחזקותו במדינה: גידול אוכלוסייה מוסלמית, מסגדים, אסלאם פוליטי, רדיקליזציה.\n"
        "4. פעילות פרו-פלסטינית ואנטי-ישראלית במדינה: הפגנות, BDS/חרם, אנטי-ציונות, קמפוסים.\n"
        "5. פעילות בוטים/חשבונות מתואמים בעד/נגד ישראל: חשיפות על רשתות בוטים, קמפיינים מתואמים, "
        "מניפולציה ברשתות חברתיות - הקשורים לישראל.\n"
        "6. אירוע דרמטי כללי וחריג בלבד (סף גבוה מאוד!): רעידת אדמה גדולה, פיגוע טרור, מלחמה, "
        "נפילת ממשלה, אסון המוני. **לא** כולל חדשות פוליטיות/כלכליות/פליליות רגילות - רק "
        "אירועים שבאמת חריגים ומשני חשיבות עולמית. תהיה קמצן מאוד עם הנושא הזה.\n\n"
        "כלל מרכזי: אם כתבה לא שייכת לאף אחד מהנושאים - important=false. "
        "level='high' אם הכתבה עוסקת ישירות ומרכזית בנושא; level='medium' אם הנושא מוזכר אך אינו "
        "המוקד; level='low' אם הקשר חלש/עקיף.\n"
        "כתוב את שדה 'reason' בעברית, עד 12 מילים.\n"
        "אם important=true: הוסף גם 'title_he', 'summary_he' (עברית) ו-'title_en', 'summary_en', "
        "'reason_en' (אנגלית; reason_en = אותו הסבר של reason, באנגלית, עד 12 מילים) - תרגום מלא ומדויק "
        "(לא סיכום נוסף, תרגום ישיר) של הכותרת והתקציר המקוריים. אם important=false, אפשר "
        "להשאיר את כל שדות התרגום האלה ריקים (\"\") כדי לחסוך מקום.\n"
        "חשוב מאוד: החזר JSON תקין בהחלט. אם יש מרכאות כפולות (\") בתוך אחד השדות, "
        "יש לתת להן escape תקין כ-\\\" כדי שה-JSON לא יישבר. אל תשתמש במרכאות כפולות רגילות "
        "בתוך ערכי מחרוזת בלי escape.\n"
        "החזר אך ורק JSON תקין (ללא טקסט נוסף, ללא markdown):\n"
        '{"results": [{"link": "...", "important": true/false, "level": "low/medium/high", '
        '"topic": "israel/immigration/islam/pro_palestinian/bot_activity/dramatic_event", '
        '"reason": "עד 12 מילים בעברית", "title_he": "תרגום מלא לעברית", "summary_he": "תרגום מלא לעברית", '
        '"title_en": "full English translation", "summary_en": "full English translation", '
        '"reason_en": "up to 12 words in English"}]}'
    )

    user_content = json.dumps(items_for_prompt, ensure_ascii=False)

    for attempt in range(2):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": 8000,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_content}],
                },
                timeout=90,
            )
            if resp.status_code != 200:
                logger.error(f"קריאה ל-Claude API נכשלה: סטטוס {resp.status_code}, תגובה: {resp.text[:1000]}")
                if resp.status_code in (400, 401, 403) and "credit" in resp.text.lower():
                    was_available = time.time() >= _claude_unavailable_until
                    _claude_unavailable_until = time.time() + CLAUDE_COOLDOWN_SECONDS
                    logger.warning(
                        f"זוהתה בעיית קרדיט/הרשאות ב-Anthropic - משהה קריאות ל-Claude "
                        f"ל-{CLAUDE_COOLDOWN_SECONDS // 60} דקות (במקום לנסות על כל באצ')"
                    )
                    # התראה לערוץ - רק במעבר ממצב תקין למצב תקול, כדי לא להציף
                    if was_available:
                        send_telegram_message(
                            "⚠️ <b>תקלה בבוט</b>\n\n"
                            "אין גישה ל-Claude (כנראה נגמר הקרדיט ב-Anthropic).\n"
                            "עד לטעינת קרדיט ההודעות יגיעו <b>ללא סיווג AI וללא תרגום מלא</b>.\n\n"
                            "לטעינה: console.anthropic.com → Billing"
                        )
                return {}
            data = resp.json()
            text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            raw_text = "".join(text_blocks).strip()
            raw_text = re.sub(r"^```json|```$", "", raw_text, flags=re.MULTILINE).strip()

            try:
                parsed = json.loads(raw_text)
                results_list = parsed.get("results", [])
            except json.JSONDecodeError as e:
                logger.warning(f"ה-JSON שחזר מ-Claude שבור ({e}), מנסה לחלץ פריטים תקינים בנפרד...")
                results_list = extract_individual_results(raw_text)
                if not results_list:
                    logger.error("לא הצלחתי לחלץ אף פריט תקין מהתגובה השבורה, מוותר על הבאצ' הזה")
                    return {}
                logger.info(f"הצלחתי לחלץ {len(results_list)} פריטים תקינים מתוך התגובה השבורה")

            result_map = {}
            for item in results_list:
                if "link" not in item:
                    continue
                result_map[item["link"]] = {
                    "important": item.get("important", False),
                    "level": item.get("level", "low"),
                    "reason": item.get("reason", ""),
                    "topic": item.get("topic", ""),
                    "title_he": item.get("title_he", ""),
                    "summary_he": item.get("summary_he", ""),
                    "title_en": item.get("title_en", ""),
                    "summary_en": item.get("summary_en", ""),
                    "reason_en": item.get("reason_en", ""),
                }
            return result_map

        except requests.exceptions.Timeout:
            if attempt == 0:
                logger.warning("קריאה ל-Claude API עברה timeout, מנסה שוב פעם אחת...")
                continue
            logger.error("קריאה ל-Claude API עברה timeout גם בניסיון החוזר, מוותר על הבאצ' הזה")
            return {}
        except Exception as e:
            logger.error(f"קריאה ל-Claude API נכשלה: {e}")
            return {}

    return {}


_last_translate_call = 0.0
TRANSLATE_MIN_INTERVAL = 0.6  # שניות בין קריאות תרגום, כדי לא להציף את גוגל טרנסלייט

# סימנים לזיהוי דף שגיאה של גוגל שנקלט בטעות כאילו הוא "תרגום"
_TRANSLATE_ERROR_MARKERS = ["error 500", "server error", "that's an error", "that’s an error"]


def translate_to_hebrew(text: str, retries: int = 1) -> str:
    global _last_translate_call

    if not text:
        return ""

    for attempt in range(retries + 1):
        elapsed = time.time() - _last_translate_call
        if elapsed < TRANSLATE_MIN_INTERVAL:
            time.sleep(TRANSLATE_MIN_INTERVAL - elapsed)

        try:
            result = GoogleTranslator(source="auto", target="iw").translate(text)
            _last_translate_call = time.time()

            if result and any(marker in result.lower() for marker in _TRANSLATE_ERROR_MARKERS):
                logger.warning(f"תרגום החזיר דף שגיאה של גוגל (ניסיון {attempt + 1}), מנסה שוב...")
                time.sleep(2 + attempt * 2)
                continue

            return result

        except Exception as e:
            _last_translate_call = time.time()
            logger.warning(f"תרגום נכשל (ניסיון {attempt + 1}): {e}")
            time.sleep(2 + attempt * 2)

    logger.error("תרגום נכשל בכל הניסיונות, מחזיר טקסט מקורי (לא מתורגם)")
    return text


_last_telegram_send = 0.0
TELEGRAM_MIN_INTERVAL = 1.2  # שניות בין הודעות, כדי לא לחרוג ממגבלת הקצב של טלגרם


def send_telegram_message(message: str) -> bool:
    global _last_telegram_send

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    if len(message) > 4090:
        message = message[:4090] + "…"
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    # מקצב את קצב השליחה כדי לא להציף את טלגרם
    elapsed = time.time() - _last_telegram_send
    if elapsed < TELEGRAM_MIN_INTERVAL:
        time.sleep(TELEGRAM_MIN_INTERVAL - elapsed)

    for attempt in range(2):
        try:
            resp = requests.post(url, data=data, timeout=15)
            _last_telegram_send = time.time()

            if resp.status_code == 429:
                retry_after = 5
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    pass
                logger.warning(f"טלגרם הגביל קצב (429), ממתין {retry_after} שניות ומנסה שוב...")
                time.sleep(retry_after + 1)
                continue

            if resp.status_code != 200:
                logger.error(f"טלגרם החזיר שגיאה: {resp.status_code} - {resp.text}")
                return False

            return True

        except requests.RequestException as e:
            logger.error(f"שליחת הודעה לטלגרם נכשלה: {e}")
            return False

    logger.error("שליחת הודעה לטלגרם נכשלה גם אחרי המתנה על 429")
    return False


TOPIC_LABELS_HE = {
    "israel": "🇮🇱 ישראל / יהודים",
    "immigration": "🛂 הגירה",
    "islam": "☪️ אסלאם",
    "pro_palestinian": "📢 פעילות פרו-פלסטינית / אנטי-ישראלית",
    "bot_activity": "🤖 רשתות בוטים / פעילות מתואמת",
    "dramatic_event": "⚠️ אירוע דרמטי כללי",
}

TOPIC_LABELS_EN = {
    "israel": "🇮🇱 Israel / Jewish",
    "immigration": "🛂 Immigration",
    "islam": "☪️ Islam",
    "pro_palestinian": "📢 Pro-Palestinian / Anti-Israel activity",
    "bot_activity": "🤖 Bot networks / Coordinated activity",
    "dramatic_event": "⚠️ Major dramatic event",
}


def esc(text: str) -> str:
    """Escape for Telegram HTML parse mode (prevents 400 errors from <, > or & in titles)."""
    return html.escape(text or "", quote=False)


def format_message(title: str, summary: str, link: str, level: str, reason: str, topic: str = "",
                    title_he: str = "", summary_he: str = "", fallback_mode: bool = False,
                    title_en: str = "", summary_en: str = "", reason_en: str = "") -> str:
    # עברית: עדיפות לתרגום של Claude; רק אם חסר (מצב חירום) - ניסיון תרגום בגוגל.
    he_title = title_he.strip() if title_he else translate_to_hebrew(title)
    he_summary = summary_he.strip() if summary_he else translate_to_hebrew(strip_html(summary)[:700])
    src = get_source_info(link)
    emoji = LEVEL_EMOJI.get(level, "🟠")

    topic_he = TOPIC_LABELS_HE.get(topic, "")
    topic_en_label = TOPIC_LABELS_EN.get(topic, "")
    if " " in topic_en_label:
        topic_en_label = topic_en_label.split(" ", 1)[1]  # בלי אימוג'י כפול
    topic_block = f"\n<b>נושא / Topic:</b>\n{topic_he} | {topic_en_label}\n" if topic_he else ""

    # באנרים קבועים
    bot_banner = "⭐🤖⭐ פעילות בוטים / השפעה מתואמת ⭐🤖⭐\n\n" if topic == "bot_activity" else ""
    fallback_banner = "⚠️ <i>מצב חירום: ללא סיווג AI, התרגום עשוי להיות חלקי</i>\n\n" if fallback_mode else ""

    # בלוק עברית
    reason_he_block = f"\n<b>מדוע מעניין:</b>\n{esc(reason)}\n" if reason else ""
    hebrew_block = (
        "🔸 <b>עברית</b>\n\n"
        f"<b>כותרת:</b>\n{esc(he_title)}\n\n"
        f"<b>תקציר:</b>\n{esc(he_summary)}\n"
        f"{reason_he_block}"
    )

    # בלוק אנגלית - רק כשיש תרגום של Claude (במצב חירום מדלגים, כדי לא להציף את גוגל)
    english_block = ""
    if title_en and summary_en:
        reason_en_block = f"\n<b>Why it matters:</b>\n{esc(reason_en)}\n" if reason_en else ""
        english_block = (
            "\n━━━━━━━━━━━━━━━\n"
            "🔹 <b>English</b>\n\n"
            f"<b>Title:</b>\n{esc(title_en.strip())}\n\n"
            f"<b>Summary:</b>\n{esc(summary_en.strip())}\n"
            f"{reason_en_block}"
        )

    return (
        f"\n{fallback_banner}{bot_banner}"
        f"{src['flag']} <b>{src['country']} | התראה ממוקדת</b>\n"
        f"{topic_block}\n"
        f"{hebrew_block}"
        f"{english_block}"
        "\n━━━━━━━━━━━━━━━\n"
        f"<b>רמת רלוונטיות / Relevance:</b>\n{emoji} {level}\n\n"
        f"<b>מקור / Source:</b>\n{src['source_name']}\n\n"
        f"<b>קישור / Link:</b>\n{esc(link)}\n"
    )


MAX_ARTICLE_AGE_HOURS = 24  # מתעלמים מכתבות ישנות יותר, אם יש להן תאריך תקין


def is_recent_enough(entry) -> bool:
    """בודק שהכתבה פורסמה ב-24 השעות האחרונות. אם אין תאריך תקין - לא מסננים (עדיף לכלול מאשר לפספס)."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return True
    try:
        published_ts = calendar.timegm(parsed)  # struct_time מגיע ב-UTC
        age_hours = (time.time() - published_ts) / 3600
        return age_hours <= MAX_ARTICLE_AGE_HOURS
    except Exception:
        return True


_diagnosed_feeds = set()  # כדי לרשום אבחון על פיד תקול פעם אחת בלבד ולא בכל סבב


def fetch_feed(feed_url: str):
    """
    מוריד RSS דרך requests עם timeout אמיתי, ורק אז מעביר ל-feedparser לפענוח.
    feedparser.parse() על URL ישירות לא מוגבל בזמן ועלול לתקוע את כל התהליך לנצח
    אם שרת כלשהו לא מגיב - זו הייתה סיבת ה"תקיעה" של הבוט לכמה ימים.
    """
    resp = requests.get(
        feed_url,
        timeout=(10, 20),  # (זמן התחברות, זמן קריאה) בשניות
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if not feed.entries and feed_url not in _diagnosed_feeds:
        _diagnosed_feeds.add(feed_url)
        snippet = resp.text[:150].replace("\n", " ")
        logger.warning(
            f"אבחון פיד ריק {feed_url}: סטטוס {resp.status_code}, "
            f"סוג תוכן {resp.headers.get('content-type')}, תחילת התוכן: {snippet}"
        )
    return feed


# ---------- סינון מקדים (חיסכון בעלויות) - כתבות שלא נשלחות ל-Claude בכלל ----------
# כתבות עם כותרת ברורה של ספורט/רכילות/מזל/לוטו. כתבה שמכילה גם מילת נושא (ישראל, הגירה, אסלאם...)
# לא מסוננת לעולם - הרשימה שמרנית בכוונה.
JUNK_SUBSTRINGS = [
    # איטלקית
    "calcio", "champions league", "europa league", "conference league", "formula 1", "motogp",
    "giro d'italia", "oroscopo", "grande fratello", "sanremo", "superenalotto",
    # גרמנית
    "fußball", "fussball", "bundesliga", "dfb-pokal", "formel 1", "horoskop",
    # יוונית
    "ποδόσφαιρ", "ποδοσφαίρ", "super league", "ωροσκόπ", "ζώδια", "στοίχημα",
    # אנגלית
    "premier league", "horoscope",
]
JUNK_WORDS_RE = re.compile(
    r"(?<!\w)(serie a|serie b|tennis|basket|volley|nba|nfl|lotto|gossip|promi|ricetta|rezept)(?!\w)",
    re.IGNORECASE,
)


def is_junk_title(title: str, summary: str = "") -> bool:
    t = (title or "").lower()
    if not t:
        return False
    if not (any(sub in t for sub in JUNK_SUBSTRINGS) or JUNK_WORDS_RE.search(t)):
        return False
    return not fallback_is_important(title, summary)  # שכבת ביטחון: מילת נושא = לא מסננים


def check_feeds(seen_links: dict):
    new_entries = []
    skipped_junk = 0

    for feed_url in RSS_FEEDS:
        try:
            feed = fetch_feed(feed_url)
        except Exception as e:
            logger.error(f"נכשל בקריאת RSS מ-{feed_url}: {e}")
            continue

        if feed.bozo and not feed.entries:
            logger.warning(f"הפיד {feed_url} נראה תקול ולא החזיר entries")
            continue

        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            link = entry.get("link", "")

            if not link or link in seen_links:
                continue

            seen_links[link] = True

            if not is_recent_enough(entry):
                continue

            if is_junk_title(title, summary):
                skipped_junk += 1
                continue

            new_entries.append({"title": title, "summary": summary, "link": link})

    if skipped_junk:
        logger.info(f"סוננו מראש (בלי עלות AI): {skipped_junk} כתבות ספורט/רכילות")

    if not new_entries:
        return

    logger.info(f"{len(new_entries)} כתבות חדשות, שולח לסיווג...")

    classification = {}
    batch_size = 10
    for i in range(0, len(new_entries), batch_size):
        batch = new_entries[i:i + batch_size]
        classification.update(classify_with_claude(batch))

    for entry in new_entries:
        link = entry["link"]
        title = entry["title"]
        summary = entry["summary"]

        result = classification.get(link)

        if result is not None:
            is_important = result["important"] and (
                IMPORTANCE_RANK.get(result["level"], 1) >= IMPORTANCE_RANK[IMPORTANCE_THRESHOLD]
            )
            level = result["level"]
            reason = result["reason"]
            topic = result.get("topic", "")
            title_he = result.get("title_he", "")
            summary_he = result.get("summary_he", "")
            title_en = result.get("title_en", "")
            summary_en = result.get("summary_en", "")
            reason_en = result.get("reason_en", "")
            used_fallback = False
        else:
            is_important = fallback_is_important(title, summary)
            level = "medium"
            reason = ""
            topic = ""
            title_he = ""
            summary_he = ""
            title_en = ""
            summary_en = ""
            reason_en = ""
            used_fallback = True

        if is_important:
            try:
                message = format_message(title, summary, link, level, reason, topic, title_he, summary_he,
                                         fallback_mode=used_fallback,
                                         title_en=title_en, summary_en=summary_en, reason_en=reason_en)
            except Exception as e:
                logger.error(f"נכשל בעיצוב הודעה עבור {link}: {e}")
                continue

            if send_telegram_message(message):
                logger.info(f"נשלחה הודעה ({level}): {title[:60]}")


def run():
    if "PUT_YOUR" in BOT_TOKEN or "PUT_YOUR" in CHAT_ID:
        logger.error("צריך להגדיר BOT_TOKEN ו-CHAT_ID לפני הרצה")
        return

    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY לא מוגדר - הבוט יעבוד רק עם סינון מילות מפתח (fallback)")

    seen_links = load_seen_links()
    logger.info(f"הבוט עלה. {len(seen_links)} קישורים ידועים מהרצות קודמות")
    if VOLUME_PATH:
        logger.info(f"דיסק קבוע מחובר - הזיכרון נשמר ב-{SEEN_LINKS_FILE}")
    else:
        logger.warning("אין דיסק קבוע מחובר - הזיכרון יימחק בכל פריסה (יגרום להודעות חוזרות)")

    while True:
        try:
            check_feeds(seen_links)
            save_seen_links(seen_links)
        except Exception as e:
            logger.error(f"שגיאה לא צפויה בלולאה הראשית: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
