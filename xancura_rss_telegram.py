import re
import os
import requests
import xml.etree.ElementTree as ET

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
X_USER = os.environ.get("X_USER", "xancura")

UA = {"User-Agent": "Mozilla/5.0 (XancuraRSSBot/2.0)"}

RSS_URLS = [
    f"https://nitter.net/{X_USER}/rss",
    f"https://nitter.privacydev.net/{X_USER}/rss",
    f"https://nitter.poast.org/{X_USER}/rss",
    f"https://nitter.1d4.us/{X_USER}/rss",
    f"https://nitter.fdn.fr/{X_USER}/rss",
]

KEYWORDS = ["ALERTA","TSUNAMI","EVACU","EVACUACION","SISMO","TERREMOTO","MAREMOTO","EMERGENCIA","SHOA","SENAPRED"]

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError("Faltan TELEGRAM_TOKEN o CHAT_ID (Secrets).")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text[:3900], "disable_web_page_preview": True}
    r = requests.post(url, data=payload, timeout=20)
    r.raise_for_status()

def looks_like_alert(text: str) -> bool:
    up = text.upper()
    return any(k in up for k in KEYWORDS)

def fetch_rss_xml():
    """
    Devuelve (xml_text, used_url).
    Maneja mirrors caídos, respuestas vacías, HTML, 403/429.
    """
    last_err = None
    for url in RSS_URLS:
        try:
            r = requests.get(url, headers=UA, timeout=25)

            # si está rate-limited o prohibido, probar otro
            if r.status_code in (403, 429, 500, 502, 503, 504):
                last_err = RuntimeError(f"{url} -> HTTP {r.status_code}")
                continue

            r.raise_for_status()
            txt = (r.text or "").strip()

            # vacío
            if not txt:
                last_err = RuntimeError(f"{url} -> respuesta vacía")
                continue

            # si es HTML (bloqueo / cloudflare / error page)
            if "<html" in txt.lower() or "<!doctype html" in txt.lower():
                last_err = RuntimeError(f"{url} -> devolvió HTML (bloqueo)")
                continue

            # sanity check: debe tener tags rss/feed
            low = txt.lower()
            if "<rss" not in low and "<feed" not in low:
                last_err = RuntimeError(f"{url} -> no parece RSS/Atom")
                continue

            return txt, url

        except Exception as e:
            last_err = e
            continue

    raise last_err if last_err else RuntimeError("No se pudo obtener RSS de ningún mirror")

def parse_items(rss_xml: str):
    root = ET.fromstring(rss_xml)
    ch = root.find("channel")
    if ch is None:
        return []

    items = []
    for it in ch.findall("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        desc = (it.findtext("description") or "").strip()

        # limpiar HTML
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s{2,}", " ", desc).strip()

        text = desc if len(desc) > len(title) else title
        items.append({"text": text, "link": link, "pub": pub})

    return items

def main():
    # 1) bajar rss
    try:
        xml, used = fetch_rss_xml()
    except Exception as e:
        # no crashear: avisar por Telegram 1 sola vez si quieres (lo dejo comentado)
        # send_telegram(f"⚠️ Xancura RSS: no pude leer RSS ({e})")
        print(f"Xancura RSS error: {e}")
        return  # salir OK para que el workflow no quede rojo

    # 2) parsear
    try:
        items = parse_items(xml)
    except Exception as e:
        print(f"Parse error: {e}")
        return  # salir OK

    if not items:
        print("No hay items en RSS")
        return

    latest = items[0]
    if looks_like_alert(latest["text"]):
        msg = f"🚨 XANCURA ALERTA\n🕒 {latest['pub']}\n\n{latest['text']}\n\nFuente: {latest['link'] or used}"
        send_telegram(msg)
    else:
        print("Último post no parece alerta (filtrado).")

if __name__ == "__main__":
    main()
