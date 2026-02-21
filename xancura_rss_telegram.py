import time
import re
import json
import os
import requests
import xml.etree.ElementTree as ET

# ====== CONFIG via GitHub Secrets ======
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

X_USER = os.environ.get("X_USER", "xancura")  # pon el @ exacto en Secrets si quieres

POLL_SECONDS = 1  # en Actions no usamos sleep largo
STATE_FILE = "xancura_rss_state.json"

RSS_URLS = [
    f"https://nitter.net/{X_USER}/rss",
    f"https://nitter.privacydev.net/{X_USER}/rss",
    f"https://nitter.poast.org/{X_USER}/rss",
]

KEYWORDS = ["ALERTA","TSUNAMI","EVACU","EVACUACION","SISMO","TERREMOTO","MAREMOTO","EMERGENCIA","SHOA","SENAPRED"]

UA = {"User-Agent": "Xancura-RSS-Telegram/1.0"}

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

def fetch_rss():
    last_err = None
    for url in RSS_URLS:
        try:
            r = requests.get(url, headers=UA, timeout=25)
            r.raise_for_status()
            return r.text, url
        except Exception as e:
            last_err = e
    raise last_err

def parse_items(rss_xml: str):
    root = ET.fromstring(rss_xml)
    ch = root.find("channel")
    if ch is None:
        return []
    out = []
    for it in ch.findall("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link or title).strip()
        pub = (it.findtext("pubDate") or "").strip()
        desc = (it.findtext("description") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s{2,}", " ", desc).strip()
        text = desc if len(desc) > len(title) else title
        out.append({"guid": guid, "text": text, "link": link, "pub": pub})
    return out

def load_state():
    # En Actions el filesystem no persiste entre runs.
    # Solución simple: no guardamos estado; solo mandamos el último si es alerta.
    return {"last_guid": None}

def main():
    xml, used = fetch_rss()
    items = parse_items(xml)
    if not items:
        return

    latest = items[0]
    if looks_like_alert(latest["text"]):
        msg = f"🚨 XANCURA ALERTA\n🕒 {latest['pub']}\n\n{latest['text']}\n\nFuente: {latest['link'] or used}"
        send_telegram(msg)

if __name__ == "__main__":
    main()
