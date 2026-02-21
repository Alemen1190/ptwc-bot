import time
import re
import json
import base64
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# ================== CONFIG ==================
TOKEN ="7866955393:AAES9j7kpyGpB9hBykPqjpypWWB2HuZxo6s"       # <- CAMBIA ESTO
CHAT_ID = "1661902760"            # <- tu chat id

PTWC_ATOM = "https://www.tsunami.gov/events/xml/PHEBAtom.xml"
STATE_FILE = "ptwc_state.json"

POLL_SECONDS = 45

# True = manda solo líneas con Chile (o puertos chilenos). False = manda PTIME completo si existe.
CHILE_ONLY = True

CHILE_KEYWORDS = [
    "CHILE", "ARICA", "IQUIQUE", "ANTOFAGASTA", "COQUIMBO", "VALPARAISO",
    "SAN ANTONIO", "TALCAHUANO", "CONCEPCION", "PUERTO MONTT",
    "CHAITEN", "CASTRO", "QUELLON", "PUNTA ARENAS", "MAGALLANES"
]

UA_HEADERS = {
    "User-Agent": "PTWC-Telegram-Notifier/2.0 (local-script)",
    "Accept": "application/atom+xml,application/xml,text/xml,text/plain,*/*",
}
# ===========================================


# -------- Telegram --------
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text[:3900],
        "disable_web_page_preview": True
    }
    r = requests.post(url, data=payload, timeout=20)
    r.raise_for_status()


# -------- State --------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_entry_id": None, "last_updated": None}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


# -------- Fetch --------
def http_get(url: str) -> str:
    r = requests.get(url, headers=UA_HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def fetch_atom() -> str:
    return http_get(PTWC_ATOM)


# -------- Atom parsing --------
def parse_atom_latest(atom_xml: str):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(atom_xml)
    entries = root.findall("a:entry", ns)
    if not entries:
        return None

    e = entries[0]
    entry_id = (e.findtext("a:id", default="", namespaces=ns) or "").strip()
    title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
    updated = (e.findtext("a:updated", default="", namespaces=ns) or "").strip()

    links = []
    for lk in e.findall("a:link", ns):
        href = lk.attrib.get("href", "")
        if href:
            links.append(href)

    return {"id": entry_id, "title": title, "updated": updated, "links": links}


def candidate_urls_from_links(links: list[str]) -> list[str]:
    """
    Ordena candidatos:
    1) *TEX.xml*
    2) *TEXT.xml*
    3) cualquier xml NO CAP
    4) si solo hay CAP, intentamos reemplazar CAP -> TEXT/TEX
    """
    out = []

    def add_if(h):
        if h and h not in out:
            out.append(h)

    # 1) TEX
    for h in links:
        if "TEX.XML" in h.upper():
            add_if(h)

    # 2) TEXT
    for h in links:
        if "TEXT.XML" in h.upper():
            add_if(h)

    # 3) XML no CAP
    for h in links:
        if h.lower().endswith(".xml") and "cap.xml" not in h.lower():
            add_if(h)

    # 4) CAP fallback + transformaciones
    for h in links:
        if "cap.xml" in h.lower():
            # CAP directo (por si lo necesitamos)
            add_if(h)

            # intentos de “convertir” URL CAP a TEXT/TEX (muchas veces funciona)
            add_if(re.sub(r"PHEBCAP\.xml", "PHEBTEXT.xml", h, flags=re.IGNORECASE))
            add_if(re.sub(r"PHEBCAP\.xml", "TEX.xml", h, flags=re.IGNORECASE))
            add_if(re.sub(r"CAP\.xml", "TEXT.xml", h, flags=re.IGNORECASE))
            add_if(re.sub(r"CAP\.xml", "TEX.xml", h, flags=re.IGNORECASE))

    return out


# -------- Extract bulletin text from TEX/TEXT XML --------
def extract_text_from_xml(xml_text: str) -> str:
    """
    Intenta sacar el boletín legible de TEX/TEXT xml:
    - Busca el bloque más largo con texto real (muchas letras/espacios)
    - Fallback: quita tags
    """
    # 1) parse XML y tomar el “mejor texto”
    try:
        root = ET.fromstring(xml_text)
        candidates = []
        for el in root.iter():
            if el.text:
                t = el.text.strip()
                if len(t) > 120:
                    # Heurística: texto “humano” debe tener espacios y letras
                    if re.search(r"[A-Za-z].*\s+.*[A-Za-z]", t):
                        candidates.append(t)
        if candidates:
            return max(candidates, key=len)
    except Exception:
        pass

    # 2) fallback: limpiar tags
    cleaned = re.sub(r"<[^>]+>", "\n", xml_text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned


# -------- Extract bulletin text from CAP XML --------
def extract_text_from_cap(cap_xml: str) -> str:
    """
    CAP suele traer contenido en <description> o en <resource><derefUri> (base64).
    Intentamos:
    - description/info
    - derefUri base64 decodificado
    """
    # Parse robusto ignorando namespaces
    try:
        root = ET.fromstring(cap_xml)
    except Exception:
        return extract_text_from_xml(cap_xml)

    # 1) Buscar <description> largo
    descs = []
    for el in root.iter():
        tag = el.tag.lower()
        if tag.endswith("description") and el.text:
            t = el.text.strip()
            if len(t) > 80:
                descs.append(t)
    if descs:
        return max(descs, key=len)

    # 2) Buscar <derefUri> (a veces base64)
    derefs = []
    for el in root.iter():
        tag = el.tag.lower()
        if tag.endswith("derefuri") and el.text:
            derefs.append(el.text.strip())

    for d in derefs:
        # algunos vienen con prefijo "data:...;base64,"
        b64 = d
        if "base64," in d:
            b64 = d.split("base64,", 1)[1].strip()

        # base64 a bytes -> texto
        try:
            raw = base64.b64decode(b64, validate=False)
            # intentar utf-8, luego latin-1
            try:
                txt = raw.decode("utf-8", errors="replace")
            except Exception:
                txt = raw.decode("latin-1", errors="replace")
            # si parece texto humano, lo usamos
            if re.search(r"[A-Za-z].*\s+.*[A-Za-z]", txt) and len(txt) > 120:
                return txt.strip()
        except Exception:
            continue

    # 3) fallback
    return extract_text_from_xml(cap_xml)


def looks_like_garbage(text: str) -> bool:
    """
    Detecta el “texto raro” tipo base64 en líneas con muchos /+== y pocas palabras.
    """
    if not text:
        return True
    sample = text.strip()[:500]
    # si casi no hay espacios pero sí muchos símbolos base64
    if sample.count(" ") < 3 and (sample.count("/") + sample.count("+") + sample.count("=")) > 30:
        return True
    return False


# -------- PTIME extraction --------
def extract_ptime_section(bulletin_text: str) -> str | None:
    lines = [ln.rstrip() for ln in bulletin_text.splitlines()]
    upper = [ln.upper() for ln in lines]

    headers = [
        "ESTIMATED TIMES OF ARRIVAL",
        "ESTIMATED TSUNAMI ARRIVAL",
        "TSUNAMI ARRIVAL TIMES",
        "ARRIVAL TIMES",
        "FORECAST TSUNAMI ARRIVAL",
        "FORECASTS OF TSUNAMI ARRIVAL",
        "ETA"
    ]

    start = None
    for i, ln in enumerate(upper):
        if any(h in ln for h in headers):
            start = i
            break

    if start is None:
        return None

    collected = []
    empty_streak = 0
    for j in range(start, min(start + 180, len(lines))):
        ln = lines[j].strip("\ufeff").rstrip()
        if not ln:
            empty_streak += 1
            if collected and empty_streak >= 2:
                break
            continue
        empty_streak = 0
        collected.append(ln)

    if not collected:
        return None

    if CHILE_ONLY:
        filtered = []
        for ln in collected:
            up = ln.upper()
            if any(k in up for k in CHILE_KEYWORDS):
                filtered.append(ln)
        if filtered:
            collected = filtered

    return "\n".join(collected[:50]).strip() if collected else None


def build_message(latest, bulletin_text: str, source_url: str | None):
    title = latest.get("title", "")
    updated = latest.get("updated", "") or datetime.utcnow().isoformat() + "Z"

    ptime = extract_ptime_section(bulletin_text)

    if ptime:
        msg = f"🚨 PTWC NUEVO BOLETÍN\n📰 {title}\n🕒 {updated}\n\n⏱️ PTIME / ETA:\n{ptime}"
    else:
        # resumen: primeras líneas legibles
        useful = []
        for ln in bulletin_text.splitlines():
            ln = ln.strip()
            if ln:
                useful.append(ln)
            if len(useful) >= 18:
                break
        msg = f"🌊 PTWC: {title}\n🕒 {updated}\n\n" + "\n".join(useful)

    if source_url:
        msg += f"\n\nFuente: {source_url}"
    return msg


def fetch_best_bulletin_text(candidates: list[str]) -> tuple[str, str | None]:
    """
    Prueba URLs candidatas en orden y devuelve (texto, url_usada).
    - Si es CAP.xml usa extractor CAP
    - Si el resultado se ve basura, prueba siguiente
    """
    last_text = ""
    last_url = None

    for url in candidates:
        try:
            xml = http_get(url)
            last_url = url

            if "cap.xml" in url.lower():
                text = extract_text_from_cap(xml)
            else:
                text = extract_text_from_xml(xml)

            last_text = text

            if not looks_like_garbage(text):
                return text, url

        except Exception:
            continue

    return last_text or "", last_url


def main():
    state = load_state()

    send_telegram("✅ PTWC bot activo (V2). TEX/TEXT primero, CAP solo si es necesario. Boletines + PTIME/ETA.")

    while True:
        try:
            atom = fetch_atom()
            latest = parse_atom_latest(atom)

            if not latest:
                time.sleep(POLL_SECONDS)
                continue

            candidates = candidate_urls_from_links(latest.get("links", []))
            bulletin_text, used_url = fetch_best_bulletin_text(candidates)

        except Exception as e:
            send_telegram(f"Error PTWC: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
