import argparse
import urllib.request
import urllib.parse
import urllib.error
import email.utils
import re
import html
from html.parser import HTMLParser
import json
import datetime
import os
import shutil
import socket
import sys
import tempfile
import time
import traceback

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import validate_data  # noqa: E402


def _redact_secrets(message: str, *secrets_to_hide: str) -> str:
    """Reemplaza cualquier aparición de tokens o identificadores sensibles por '[REDACTED]'."""
    sanitized = str(message)
    for secret in secrets_to_hide:
        clean_secret = (secret or "").strip()
        if clean_secret:
            sanitized = sanitized.replace(clean_secret, "[REDACTED]")
            encoded = urllib.parse.quote(clean_secret, safe="")
            if encoded and encoded != clean_secret:
                sanitized = sanitized.replace(encoded, "[REDACTED]")
    return sanitized


def send_telegram_message(text, timeout=None) -> bool:
    """
    Envía un mensaje a Telegram aplicando timeout explícito y redacción estricta de credenciales:
    - Si TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID faltan, omite el envío sin error y retorna False.
    - Cierra siempre la respuesta HTTP y evita filtrar el token o chat_id en logs ante fallos de red.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("Telegram Token o Chat ID no configurados; notificación omitida.")
        return False

    eff_timeout = HTTP_TIMEOUT_SECONDS if timeout is None else float(timeout)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data)
        resp = urllib.request.urlopen(req, timeout=eff_timeout)
        try:
            status = getattr(resp, "status", None)
            if not isinstance(status, int):
                code_fn = getattr(resp, "getcode", None)
                maybe_code = code_fn() if callable(code_fn) else None
                status = maybe_code if isinstance(maybe_code, int) else 200
        finally:
            close_fn = getattr(resp, "close", None)
            if callable(close_fn):
                close_fn()
        return 200 <= int(status) < 300
    except Exception as e:
        safe_err = _redact_secrets(str(e), token, chat_id)
        print(f"Error enviando mensaje a Telegram: {safe_err}")
        return False

# -------------------------------------------------------------
# 0. POLÍTICA DE ACCESO HTTP Y USER-AGENT TRANSPARENTE (WIKIMEDIA)
# -------------------------------------------------------------
# Identifica de forma transparente al proyecto real conforme a Wikimedia User-Agent Policy,
# sin suplantar navegadores comerciales (Mozilla/5.0, Chrome, Safari) ni inventar correos.
WIKIMEDIA_USER_AGENT = os.environ.get(
    "GRIDBOX_WIKIMEDIA_USER_AGENT",
    "GridBoxDataBot/1.0 (https://github.com/mmnlvg020-code/gridbox-data)",
).strip()
HTTP_TIMEOUT_SECONDS = 15.0
MAX_RETRY_AFTER_SECONDS = 30.0
DEFAULT_TRANSIENT_BACKOFF_SECONDS = 2.0
MAX_TRANSIENT_RETRIES = 1
INTER_CATEGORY_DELAY_SECONDS = 1.5

# -------------------------------------------------------------
# 1. DIRECCIONES DE CALENDARIOS EN FORMATO ICS (iCal)
# -------------------------------------------------------------
ICS_URLS = {
    "WRC": "https://calendar.google.com/calendar/ical/fei68gpe16c85ed3jjdtvrn8ns%40group.calendar.google.com/public/basic.ics",
    "WEC": "https://calendar.google.com/calendar/ical/61jccgg4rshh1temqk0dj4lens%40group.calendar.google.com/public/basic.ics",
    "MOTOGP": "https://calendar.google.com/calendar/ical/832vbii8pmrvma356b4vn3v42c%40group.calendar.google.com/public/basic.ics",
    "NASCAR": "https://calendar.google.com/calendar/ical/db8c47ne2bt9qbld2mhdabm0u8%40group.calendar.google.com/public/basic.ics",
    "INDYCAR": "https://calendar.google.com/calendar/ical/hlskhf7l8ce7btind39bb9kf1o%40group.calendar.google.com/public/basic.ics"
}

# -------------------------------------------------------------
# 1.5. OVERRIDES MANUALES DE HORARIOS (UTC)
# Dado que los calendarios provistos a menudo son de "Todo el día",
# puedes especificar la hora exacta de la carrera en UTC aquí.
# Clave: Parte del nombre de la carrera en minúsculas (ej: "estonia")
# Valor: "HH:MM" (hora en UTC)
# -------------------------------------------------------------
MANUAL_TIME_OVERRIDES = {
    "estonia": "10:00",         # 13:00 GMT+3 -> 10:00 UTC
    "são paulo": "14:30",       # 11:30 GMT-3 -> 14:30 UTC
    "sao paulo": "14:30",
    "alemania": "12:00",        # 14:00 GMT+2 -> 12:00 UTC
    "deutschland": "12:00",
    "quaker state 400": "23:00",# 19:00 ET -> 23:00 UTC
    "atlanta": "23:00"
}

# -------------------------------------------------------------
# 2. PÁGINAS DE WIKIPEDIA PARA ESTADÍSTICAS Y POSICIONES
# -------------------------------------------------------------
WIKI_STANDINGS = {
    "WRC": [
        "https://en.wikipedia.org/wiki/2026_World_Rally_Championship"
    ],
    "WEC": [
        "https://en.wikipedia.org/wiki/2026_FIA_World_Endurance_Championship"
    ],
    "MOTOGP": [
        "https://en.wikipedia.org/wiki/2026_MotoGP_World_Championship"
    ],
    "NASCAR": [
        "https://en.wikipedia.org/wiki/2026_NASCAR_Cup_Series"
    ],
    "INDYCAR": [
        "https://en.wikipedia.org/wiki/2026_IndyCar_Series"
    ]
}

# -------------------------------------------------------------
# PARSEADOR DE CALENDARIOS (ICS -> JSON Races)
# -------------------------------------------------------------
def is_main_race(summary, category):
    s = summary.lower()
    # Descartar sesiones que no sean la carrera principal
    session_keywords = [
        "fp1", "fp2", "fp3", "practice", "libres", "qualifying", "quali",
        "clasificacion", "shakedown", "warmup", "warm up", "test", "shootout",
        "sprint", "qualifying race", "practices", "entrenamientos", "warm-up"
    ]
    if any(k in s for k in session_keywords):
        return False
    return True

def clean_gp_name(summary, category):
    name = summary
    if "|" in name:
        name = name.split("|")[-1]
    if ":" in name:
        name = name.split(":")[-1]
    name = name.strip()

    # Quitar prefijos de categoría al inicio
    name = re.sub(r"^(?:FIA\s+)?(?:WEC|WRC|MotoGP|IndyCar|NASCAR)\s*-\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(?:FIA\s+)?(?:WEC|WRC|MotoGP|IndyCar|NASCAR)\s+", "", name, flags=re.IGNORECASE)

    # Traducciones respetando límites de palabra (\b) para no corromper palabras como 'Rallye'
    name = re.sub(r"\bGrand Prix of\b", "Gran Premio de", name, flags=re.IGNORECASE)
    name = re.sub(r"\bGP of\b", "Gran Premio de", name, flags=re.IGNORECASE)
    name = re.sub(r"\bGP\b", "Gran Premio", name)
    name = re.sub(r"\b6 Hours of\b", "6 Horas de", name, flags=re.IGNORECASE)
    name = re.sub(r"\b8 Hours of\b", "8 Horas de", name, flags=re.IGNORECASE)
    name = re.sub(r"\b24 Hours of\b", "24 Horas de", name, flags=re.IGNORECASE)

    # Rally: traducir 'Rally of' a 'Rally de', y 'Rally <Lugar>' a 'Rally de <Lugar>' sin alterar 'Rallye'
    name = re.sub(r"\bRally of\b", "Rally de", name, flags=re.IGNORECASE)
    name = re.sub(r"\bRally\b(?!\s+(?:de\b|du\b|d'))", "Rally de", name)

    # Evitar duplicaciones como 'Gran Premio de Gran Premio de' o 'de de'
    name = re.sub(r"\bGran Premio de\s+Gran Premio de\b", "Gran Premio de", name, flags=re.IGNORECASE)
    name = re.sub(r"\bde\s+de\b", "de", name, flags=re.IGNORECASE)
    name = " ".join(name.split()).strip()
    return name

def parse_ics_calendar(category, url):
    print(f"Descargando calendario ICS para {category}...")
    req = urllib.request.Request(url, headers={'User-Agent': WIKIMEDIA_USER_AGENT})
    try:
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS)
        try:
            content = resp.read().decode('utf-8')
        finally:
            close_fn = getattr(resp, "close", None)
            if callable(close_fn):
                close_fn()
    except Exception as e:
        print(f"Error descargando ICS para {category}: {e}")
        return []

    events = []
    current_event = {}
    in_event = False

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("BEGIN:VEVENT"):
            in_event = True
            current_event = {}
        elif line.startswith("END:VEVENT"):
            in_event = False
            if current_event:
                events.append(current_event)
        elif in_event:
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.split(";")[0]
                current_event[key] = val

    races = []
    round_num = 1
    for ev in events:
        summary = ev.get("SUMMARY", "Carrera")

        # Filtrar solo carreras principales
        if not is_main_race(summary, category):
            continue

        dtstart = ev.get("DTSTART", "")
        if not dtstart:
            continue

        # Solo eventos del año 2026
        if not dtstart.startswith("2026") and not dtstart.split(":")[-1].startswith("2026"):
            continue

        # Parsear fecha
        dt_val = dtstart.split(":")[-1].strip().replace("Z", "")
        try:
            if "T" in dt_val:
                dt = datetime.datetime.strptime(dt_val[:15], "%Y%m%dT%H%M%S")
                # Sincronizar en UTC
                timestamp = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
            else:
                # Eventos de todo el día: ajustar a las 12:00:00 UTC para evitar cambios de día por zona horaria
                dt = datetime.datetime.strptime(dt_val[:8], "%Y%m%d")
                dt = dt.replace(hour=12, minute=0, second=0)
                timestamp = int(dt.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
        except Exception as e:
            print(f"Error parseando fecha para {summary}: {e}")
            continue

        location = ev.get("LOCATION", "Circuito oficial")
        # Limpiar barras invertidas del formato ICS
        location = location.replace("\\,", ",").replace("\\", "").strip()

        race_name = clean_gp_name(summary, category)

        # Aplicar override manual de horario si corresponde
        for override_key, override_time in MANUAL_TIME_OVERRIDES.items():
            if override_key in race_name.lower():
                hh, mm = map(int, override_time.split(":"))
                dt_override = datetime.datetime.fromtimestamp(timestamp / 1000, tz=datetime.timezone.utc)
                dt_override = dt_override.replace(hour=hh, minute=mm, second=0)
                timestamp = int(dt_override.timestamp() * 1000)
                # print(f"   [!] Ajustando hora de '{race_name}' a {override_time} UTC")
                break

        race = {
            "id": f"{category.lower()}-2026-{round_num}",
            "category": category,
            "name": race_name,
            "circuit": location.split(",")[0].strip(),
            "country": location.split(",")[-1].strip() if "," in location else "",
            "countryCode": "",
            "dateTimestamp": timestamp,
            "round": round_num,
            "season": 2026,
            "isCompleted": False,
            "circuitId": "",
            "locality": ""
        }
        races.append(race)
        round_num += 1

    # Ordenar por fecha cronológicamente
    races.sort(key=lambda x: x["dateTimestamp"])

    # Reasignar números de ronda tras ordenarlas
    for i, r in enumerate(races):
        r["round"] = i + 1
        r["id"] = f"{category.lower()}-2026-{i+1}"

    print(f" -> Sincronizadas {len(races)} carreras para {category}")
    return races

# -------------------------------------------------------------
# PARSEADOR DE ESTADÍSTICAS (Wikipedia -> JSON Standings)
# -------------------------------------------------------------
class VisiblePointsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pieces = []
        self.in_ignored_tag = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("style", "script", "sup", "ref"):
            self.in_ignored_tag += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("style", "script", "sup", "ref"):
            self.in_ignored_tag = max(0, self.in_ignored_tag - 1)

    def handle_data(self, data):
        if self.in_ignored_tag == 0:
            self.pieces.append(data)

def parse_points_cell(raw_html: str, dash_verified_as_zero: bool = False):
    """
    Interpreta robustamente el contenido de una celda de puntuación de Wikipedia.
    - Elimina bloques <style>, <script>, <sup> y <ref> completos.
    - Ignora atributos HTML como 'title' o 'data-mw' que puedan contener penalizaciones o metadatos con números.
    - Lee exclusivamente el valor visible correspondiente a los puntos.
    - Soporta notas al pie en corchetes ([1], [a]) y datos auxiliares entre paréntesis (ej. '450 (3)').
    - Interpreta ceros numéricos expresos ('0', '0.0', '0,0') como 0.0.
    - Un guion ('-', '–', '—', '−') solo se interpreta como 0.0 si la tabla verifica explícitamente dicho significado.
    - Rechaza valores no numéricos, abandonos o ambiguos retornando None (sin forzar 0.0 ni inventar datos).
    """
    if raw_html is None:
        return None

    s = raw_html.strip()
    if not s:
        return None

    # Extraer texto visible descartando bloques no visuales y atributos HTML
    parser = VisiblePointsParser()
    try:
        parser.feed(s)
        text = "".join(parser.pieces).strip()
    except Exception:
        return None

    if not text:
        return None

    # Rechazar explícitamente códigos de no clasificación o abandono
    non_scoring = {
        "DNS", "DNF", "DSQ", "DQ", "DNQ", "WD", "RET", "NC", "EX",
        "DNP", "C", "T", "N/A", "TBD", "PO", "DNA", "DISQ"
    }
    if text.upper() in non_scoring:
        return None

    # Ceros numéricos expresos legítimos: '0', '0.0', '0,0'
    # Ningún guion ('-', '–', '—', '−') se convierte automáticamente en 0.0 sin significado explícitamente verificado
    zero_tokens = {"0", "0.0", "0,0"}
    if dash_verified_as_zero:
        zero_tokens.update({"-", "–", "—", "−"})

    if text in zero_tokens:
        return 0.0

    # Eliminar notas al pie en corchetes remanentes ([1], [a], [*])
    text = re.sub(r"\[[^\]]*\]", "", text).strip()
    # Eliminar marcadores auxiliares finales
    text = text.rstrip("*†‡ ").strip()

    # Manejar información auxiliar entre paréntesis (ej. '450 (3)')
    paren_match = re.match(r"^([0-9.,]+)\s*\([^\)]*\)$", text)
    if paren_match:
        text = paren_match.group(1).strip()

    # Validar formato numérico estricto
    clean_num = text.replace(",", "")
    if not re.match(r"^[0-9]+(?:\.[0-9]+)?$", clean_num):
        return None

    try:
        val = float(clean_num)
        return val if val >= 0.0 else None
    except ValueError:
        return None

def clean_html_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    # Remover bloques de estilo/script antes de procesar etiquetas
    s = re.sub(r"<style\b[^>]*>.*?</style>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<script\b[^>]*>.*?</script>", "", s, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", s)
    text = html.unescape(text)
    return " ".join(text.split()).strip()

def find_driver_col_index(headers, category: str):
    driver_keywords = ["rider", "driver", "drivers", "piloto", "pilotos"]
    excluded_keywords = [
        "co-driver", "codriver", "copiloto", "copilotos",
        "entrant", "owner", "manufacturer", "constructor", "team", "car", "engine"
    ]
    for idx, h in enumerate(headers):
        has_driver = any(re.search(r"\b" + kw + r"\b", h) for kw in driver_keywords)
        has_excluded = any(ex in h for ex in excluded_keywords)
        if has_driver and not has_excluded:
            return idx
    return None

def find_points_col_index(headers):
    for idx, h in enumerate(headers):
        if h in ["pts", "points", "puntos", "total pts", "total points", "total"]:
            return idx
    excluded = ["stage", "playoff", "bonus", "behind", "gap", "penalties", "penalty", "diff"]
    for idx, h in enumerate(headers):
        if any(re.search(r"\b" + kw + r"\b", h) for kw in ["pts", "points", "puntos"]):
            if not any(ex in h for ex in excluded):
                return idx
    return None

def find_pos_col_index(headers):
    for idx, h in enumerate(headers):
        if any(re.search(r"\b" + kw + r"\b", h) for kw in ["pos", "pos.", "position", "posicion", "posición", "rank", "rk"]):
            return idx
    return None

def is_ineligible_driver(row, name_idx, pos_idx, points_idx, table_body: str = "") -> tuple:
    """
    Comprueba si un participante de NASCAR tiene evidencia oficial, inequívoca y verificable
    de inelegibilidad para puntuar en el campeonato de pilotos (ej. Corey Heim).

    Requiere:
    1. Una marca o indicación en las celdas del participante (ej. †, &dagger;, &#8224;, (i), [i], o 'ineligible').
    2. Respaldo inequívoco que corresponda EXACTAMENTE a la marca del participante:
       - Si la marca es textual ('ineligible' o 'not eligible' en la celda): se acepta directamente.
       - Si la marca es una daga (†, &dagger;, &#8224;): la leyenda debe explicar explícitamente
         que la daga significa inelegibilidad para puntos. No es válido si solo explica (i).
       - Si la marca es (i) o [i]: la leyenda debe explicar explícitamente que (i) significa
         inelegibilidad para puntos. No es válido si solo explica la daga.

    Si la marca es ambigua o carece de la leyenda correspondiente, retorna (False, "").
    """
    dagger_markers = ["†", "&dagger;", "&#8224;"]
    index_markers = ["(i)", "[i]"]

    cells_to_check = []
    if name_idx is not None and name_idx < len(row):
        cells_to_check.append(row[name_idx].get("inner_html", ""))
    if pos_idx is not None and pos_idx < len(row):
        cells_to_check.append(row[pos_idx].get("inner_html", ""))
    if points_idx is not None and points_idx < len(row):
        cells_to_check.append(row[points_idx].get("inner_html", ""))

    found_marker_type = None  # "text", "dagger", "index"
    found_marker = None

    for html_content in cells_to_check:
        if "ineligible" in html_content.lower() or "not eligible" in html_content.lower():
            found_marker_type = "text"
            found_marker = "ineligible"
            break
        for marker in dagger_markers:
            if marker in html_content:
                found_marker_type = "dagger"
                found_marker = "†"
                break
        if found_marker:
            break
        for marker in index_markers:
            if marker in html_content:
                found_marker_type = "index"
                found_marker = "(i)"
                break
        if found_marker:
            break

    if not found_marker:
        return False, ""

    if found_marker_type == "text":
        return True, found_marker

    table_lower = table_body.lower()

    if found_marker_type == "dagger":
        dagger_patterns = [
            r"(?:†|&dagger;|&#8224;)[^<\n]*\b(?:ineligible|not\s+eligible)\b[^<\n]*\bpoints\b",
            r"(?:†|&dagger;|&#8224;)\s*[-–—:]?\s*(?:ineligible\b|not\s+eligible\b)"
        ]
        for pat in dagger_patterns:
            if re.search(pat, table_lower):
                return True, found_marker
        return False, ""

    if found_marker_type == "index":
        index_patterns = [
            r"(?:\([iI]\)|\[[iI]\])[^<\n]*\b(?:ineligible|not\s+eligible)\b[^<\n]*\bpoints\b",
            r"(?:\([iI]\)|\[[iI]\])\s*[-–—:]?\s*(?:ineligible\b|not\s+eligible\b)"
        ]
        for pat in index_patterns:
            if re.search(pat, table_lower):
                return True, found_marker
        return False, ""

    return False, ""

class LeafTableExtractor(HTMLParser):
    """
     Analizador estructural basado en pila sobre HTMLParser estándar que identifica
    todas las tablas HTML hoja (tablas que no contienen subtablas <table> internas)
    sin truncar estructuras anidadas en el primer cierre </table>.

    Para cada tabla hoja conserva:
    - 'attrs': atributos de la etiqueta <table> propia.
    - 'body': HTML interno exacto de la tabla hoja.
    - 'start_pos' y 'end_pos': desplazamientos de caracteres de la tabla hoja.
    - 'outer_start' y 'outer_end': desplazamientos de la tabla contenedora raíz cuando
      la tabla hoja está envuelta en una tabla de maquetación (igual a start_pos/end_pos
      si no está anidada).
    """
    def __init__(self, raw_html: str):
        super().__init__(convert_charrefs=False)
        self.raw_html = raw_html
        # Precalcular desplazamientos acumulados por línea para convertir (lineno, col) en índice absoluto
        self.line_offsets = [0]
        for line in raw_html.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        self.stack = []
        self.completed_leaves = []

    def _pos_to_offset(self, lineno: int, col: int) -> int:
        return self.line_offsets[lineno - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "table":
            return
        # Si existe una tabla abierta en la pila, marcarla como tabla contenedora padre
        if self.stack:
            self.stack[-1]["has_child_table"] = True

        start_offset = self._pos_to_offset(*self.getpos())
        raw_start_tag = self.get_starttag_text() or ""
        body_start = start_offset + len(raw_start_tag)
        if raw_start_tag.lower().startswith("<table") and raw_start_tag.endswith(">"):
            attrs_str = raw_start_tag[6:-1].strip()
        else:
            attrs_str = ""

        outer_start = self.stack[0]["start_offset"] if self.stack else start_offset
        self.stack.append({
            "attrs": attrs_str,
            "start_offset": start_offset,
            "body_start": body_start,
            "outer_start": outer_start,
            "has_child_table": False,
            "leaf_children": [],
        })

    def handle_endtag(self, tag):
        if tag.lower() != "table" or not self.stack:
            return
        node = self.stack.pop()
        body_end = self._pos_to_offset(*self.getpos())
        close_match = re.match(r"</table\s*>", self.raw_html[body_end:], re.IGNORECASE)
        end_offset = body_end + (close_match.end() if close_match else 0)

        if not node["has_child_table"]:
            leaf = {
                "attrs": node["attrs"],
                "body": self.raw_html[node["body_start"]:body_end],
                "start_pos": node["start_offset"],
                "end_pos": end_offset,
                "outer_start": node["outer_start"],
                "outer_end": end_offset,
            }
            if self.stack:
                self.stack[-1]["leaf_children"].append(leaf)
            else:
                self.completed_leaves.append(leaf)
        else:
            # Tabla contenedora: propagar sus tablas hoja manteniendo el orden original del documento
            if self.stack:
                self.stack[-1]["leaf_children"].extend(node["leaf_children"])
            else:
                for leaf in node["leaf_children"]:
                    leaf["outer_end"] = end_offset
                    self.completed_leaves.append(leaf)


def extract_leaf_tables(raw_html: str) -> list:
    """
    Extrae en orden documental todas las tablas hoja de raw_html soportando cualquier
    nivel de anidamiento de tablas de maquetación sin introducir dependencias externas.
    """
    extractor = LeafTableExtractor(raw_html)
    extractor.feed(raw_html)
    # Si quedase un contenedor externo sin cerrar al final del documento, volcar sus hojas completadas
    for pending in extractor.stack:
        for leaf in pending["leaf_children"]:
            extractor.completed_leaves.append(leaf)
    return extractor.completed_leaves


def _resolve_header_column_indices(tr_matches: list) -> tuple:
    """
    Resuelve sobre las filas iniciales de encabezado los índices de Posición, Piloto y Puntos
    para validar si un rowspan vertical en columnas críticas está legítimamente alineado.
    """
    header_rows = []
    for r in tr_matches:
        cell_matches = list(re.finditer(r"<(th|td)\b([^>]*)>(.*?)</\1>", r, re.DOTALL | re.IGNORECASE))
        if not cell_matches:
            continue
        cells = []
        for cm in cell_matches:
            tag = cm.group(1).lower()
            attrs = cm.group(2)
            inner_html = cm.group(3)
            rs_m = re.search(r"\browspan\s*=\s*['\"]?(\d+)", attrs, re.IGNORECASE)
            cs_m = re.search(r"\bcolspan\s*=\s*['\"]?(\d+)", attrs, re.IGNORECASE)
            cells.append({
                "tag": tag,
                "attrs": attrs,
                "rowspan": max(1, int(rs_m.group(1))) if rs_m else 1,
                "colspan": max(1, int(cs_m.group(1))) if cs_m else 1,
                "text": clean_html_text(inner_html),
            })
        is_th_only = all(c["tag"] == "th" for c in cells)
        has_scope_col = any("scope=\"col\"" in c["attrs"].lower() or "scope='col'" in c["attrs"].lower() for c in cells)
        if is_th_only or has_scope_col:
            header_rows.append(cells)
        else:
            if not header_rows:
                header_rows.append(cells)
            break

    if not header_rows:
        return None, None, None

    header_grid = {}
    for r_idx, row in enumerate(header_rows):
        c_idx = 0
        for cell in row:
            while (r_idx, c_idx) in header_grid:
                c_idx += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    header_grid[(r_idx + dr, c_idx + dc)] = cell["text"]
            c_idx += cell["colspan"]

    num_cols = max((c + 1 for (_, c) in header_grid), default=0)
    column_headers = []
    for c in range(num_cols):
        parts = []
        for r_idx in range(len(header_rows)):
            t = header_grid.get((r_idx, c), "")
            if t and t.lower() not in [p.lower() for p in parts]:
                parts.append(t)
        column_headers.append(" ".join(parts).lower())

    pos_idx = find_pos_col_index(column_headers)
    name_idx = find_driver_col_index(column_headers, "")
    points_idx = find_points_col_index(column_headers)
    return pos_idx, name_idx, points_idx


def has_complex_table_structure(table_body: str) -> bool:
    """
    Detecta si una tabla HTML contiene estructuras complejas no soportadas que provoquen
    desplazamientos ambiguos, solapamientos 2D o spans verticales imposibles:
    - Permite notas al pie o leyendas al final de la tabla (filas de pie con celdas combinadas).
    - Permite divisores de sección reglamentarios (ej. Championship 4 o Ineligible for driver points).
    - Permite agrupaciones de encabezados multinivel compuestas exclusivamente por <th> y separadores
      verticales decorativos vacíos (<th rowspan="N"></th>) en columnas no críticas (>= 2).
    - Permite rowspan o colspan en columnas de datos posteriores a piloto (ej. Bike, Team o resultados SPR/RAC).
    - Permite subfilas de un mismo participante (ej. piloto con dos equipos en MotoGP) únicamente
      cuando Posición, Piloto y Puntos comparten exactamente el mismo rowspan vertical con colspan=1.
    - Rechaza spans verticales que excedan el total de filas de la tabla o solapamientos 2D entre celdas.
    - Rechaza rowspan desalineado en columnas críticas (tests 15, 19, 48).
    - Rechaza cualquier colspan > 1 en las primeras columnas de datos que fusionen Posición y Piloto (tests 16, 20).
    """
    tr_matches = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_body, re.DOTALL | re.IGNORECASE)
    total_rows = len(tr_matches)
    pos_idx, name_idx, points_idx = _resolve_header_column_indices(tr_matches)

    critical_all_cols = {0, 1}
    if pos_idx is not None:
        critical_all_cols.add(pos_idx)
    if name_idx is not None:
        critical_all_cols.add(name_idx)
    if points_idx is not None:
        critical_all_cols.add(points_idx)

    # Seguimiento 2D unificado de rowspans activos hacia filas inferiores:
    # col_idx -> {"rem": filas pendientes debajo de la fila actual, "origin": "th_row"|"data", "empty_th": bool}
    active_rowspans = {}

    for r_idx, r in enumerate(tr_matches):
        cell_matches = list(re.finditer(r"<(td|th)\b([^>]*)>(.*?)</\1>", r, re.DOTALL | re.IGNORECASE))
        if not cell_matches:
            continue

        cells = []
        for cm in cell_matches:
            tag = cm.group(1).lower()
            attrs = cm.group(2)
            inner_html = cm.group(3)
            rs_m = re.search(r"\browspan\s*=\s*['\"]?(\d+)", attrs, re.IGNORECASE)
            cs_m = re.search(r"\bcolspan\s*=\s*['\"]?(\d+)", attrs, re.IGNORECASE)
            cells.append({
                "tag": tag,
                "rowspan": max(1, int(rs_m.group(1))) if rs_m else 1,
                "colspan": max(1, int(cs_m.group(1))) if cs_m else 1,
                "text": clean_html_text(inner_html),
            })

        has_td = any(c["tag"] == "td" for c in cells)

        # Caso A: Fila compuesta exclusivamente por etiquetas <th> (encabezados iniciales, subcabeceras
        # repetidas o divisores de sección con <th colspan="N">)
        if not has_td:
            # Una fila de solo <th> jamás puede interrumpir un rowspan activo de datos de un participante
            if any(info["origin"] == "data" for info in active_rowspans.values()):
                return True

            # Divisor o título de ancho completo en una única celda <th colspan="N">
            if len(cells) == 1 and cells[0]["colspan"] > 1:
                if cells[0]["rowspan"] > 1:
                    return True
                if any(not info["empty_th"] for info in active_rowspans.values()):
                    return True
                # Cerrar cualquier separador decorativo vacío previo al cruzar un divisor de ancho completo
                active_rowspans.clear()
                continue

            # Posicionar celdas <th> respetando columnas ocupadas por rowspans previos de cabecera
            placed_th = {}
            c_idx = 0
            for cell in cells:
                while c_idx in active_rowspans:
                    c_idx += 1
                # Rechazar spans verticales imposibles que superen el total de filas de la tabla
                if r_idx + cell["rowspan"] > total_rows:
                    return True
                # Rechazar solapamientos 2D donde un colspan invade una columna ya ocupada
                for dc in range(cell["colspan"]):
                    if (c_idx + dc) in active_rowspans:
                        return True
                placed_th[c_idx] = cell
                c_idx += cell["colspan"]

            # Consumir la fila actual en los rowspans previos y registrar los nuevos rowspans > 1 hacia abajo
            next_active = {
                col: {**info, "rem": info["rem"] - 1}
                for col, info in active_rowspans.items()
                if info["rem"] > 1
            }
            for col_pos, cell in placed_th.items():
                if cell["rowspan"] > 1:
                    is_empty_sep = cell["text"] == ""
                    for dc in range(cell["colspan"]):
                        next_active[col_pos + dc] = {
                            "rem": cell["rowspan"] - 1,
                            "origin": "th_row",
                            "empty_th": is_empty_sep,
                        }
            active_rowspans = next_active
            continue

        # Caso B: Fila con al menos una celda <td> (datos de participantes, notas o divisores <td>)
        # 1. Filas de una sola celda (pie de tabla, notas o divisores de sección de ancho completo)
        if len(cells) == 1:
            # Rechazar si interrumpe datos reales o encabezados con contenido textual
            if any(info["origin"] == "data" or not info["empty_th"] for info in active_rowspans.values()):
                return True
            first_cs = cells[0]["colspan"]
            first_rs = cells[0]["rowspan"]
            if first_rs > 1:
                return True
            if first_cs > 1:
                r_clean = clean_html_text(r).lower()
                remaining_rows = tr_matches[r_idx:]
                is_footer = all(
                    len(re.findall(r"<(?:td|th)\b", sub, re.IGNORECASE)) <= 2 for sub in remaining_rows
                )
                is_section_divider = any(
                    k in r_clean
                    for k in [
                        "championship", "round of", "playoff", "eliminated",
                        "regular season", "ineligible", "cut-off",
                    ]
                )
                if is_footer or is_section_divider:
                    # Cerrar limpiamente cualquier separador decorativo vacío que llegue al pie/divisor
                    active_rowspans.clear()
                    continue
                return True
            active_rowspans.clear()
            continue

        # Prohibir que un rowspan originado en cabecera invada columnas críticas (Posición, Piloto o Puntos)
        for col, info in active_rowspans.items():
            if info["origin"] == "th_row" and (col in critical_all_cols or not info["empty_th"]):
                return True

        # 2. Posicionar celdas en su columna 2D real respetando rowspans activos de cabeceras o datos previos
        placed_by_col = {}
        c_idx = 0
        for cell in cells:
            while c_idx in active_rowspans:
                c_idx += 1
            # Prohibir fusiones horizontales (colspan > 1) en las columnas críticas 0 o 1
            if cell["colspan"] > 1 and c_idx < 2:
                return True
            # Rechazar spans verticales imposibles que excedan el total de filas de la tabla
            if r_idx + cell["rowspan"] > total_rows:
                return True
            # Rechazar solapamientos 2D donde un colspan invade una columna ocupada por un rowspan activo
            for dc in range(cell["colspan"]):
                if (c_idx + dc) in active_rowspans:
                    return True
            placed_by_col[c_idx] = cell
            c_idx += cell["colspan"]

        # Verificar cualquier rowspan > 1 declarado en las primeras dos columnas (o en pos_idx / name_idx)
        critical_check_cols = {0, 1}
        if pos_idx is not None:
            critical_check_cols.add(pos_idx)
        if name_idx is not None:
            critical_check_cols.add(name_idx)

        critical_rowspans = [
            placed_by_col[col]["rowspan"]
            for col in critical_check_cols
            if col in placed_by_col and placed_by_col[col]["rowspan"] > 1
        ]

        if critical_rowspans:
            # Solo es legítimo si la tabla tiene columnas válidas de participante y puntos, y las columnas
            # críticas (Posición si existe, Piloto y Puntos) comparten idéntico rowspan con colspan=1
            if name_idx is None or points_idx is None:
                return True
            target_rs = critical_rowspans[0]
            required_cols = [name_idx, points_idx]
            if pos_idx is not None:
                required_cols.append(pos_idx)

            for req_col in required_cols:
                req_cell = placed_by_col.get(req_col)
                if req_cell is None or req_cell["rowspan"] != target_rs or req_cell["colspan"] != 1:
                    return True

            # Deben existir suficientes filas de datos posteriores para completar el rowspan
            if r_idx + target_rs > total_rows:
                return True
            for next_offset in range(1, target_rs):
                if not re.search(r"<td\b", tr_matches[r_idx + next_offset], re.IGNORECASE):
                    return True

        # Consumir una fila en los rowspans previos y registrar los nuevos rowspans > 1 de esta fila
        next_active = {
            col: {**info, "rem": info["rem"] - 1}
            for col, info in active_rowspans.items()
            if info["rem"] > 1
        }
        for col_pos, cell in placed_by_col.items():
            if cell["rowspan"] > 1:
                for dc in range(cell["colspan"]):
                    next_active[col_pos + dc] = {
                        "rem": cell["rowspan"] - 1,
                        "origin": "data",
                        "empty_th": False,
                    }
        active_rowspans = next_active

    # Si quedan rowspans pendientes sin filas suficientes al finalizar la tabla, rechazar
    if active_rowspans:
        return True

    return False


def classify_nascar_section_row(row: list):
    """
    Identifica encabezados internos de subsección de ancho completo dentro de una tabla de NASCAR:
    - Retorna 'INELIGIBLE' cuando la fila es un divisor explícito de participantes inelegibles
      para puntos del campeonato (ej. '<th colspan="41">Ineligible for driver points</th>'),
      excluyendo leyendas de símbolos que comienzan con marcas como '†' o '(i)'.
    - Retorna 'ELIGIBLE' cuando la fila declara una subsección clasificatoria (ej. playoffs o corte).
    - Retorna None para cualquier otra fila.
    """
    if not row:
        return None

    is_full_width = (
        (len(row) == 1 and row[0].get("colspan", 1) > 1)
        or (len(row) > 1 and all(c is row[0] for c in row) and row[0].get("colspan", 1) > 1)
    )
    if not is_full_width:
        return None

    text = row[0].get("text", "").strip().lower()
    if not text:
        return None

    if re.match(r"^(?:drivers?\s+)?(?:ineligible|not\s+eligible)\s+for\s+(?:[a-z0-9']+\s+){0,4}points\.?$", text):
        return "INELIGIBLE"

    if any(k in text for k in ("championship", "round of", "playoff", "eliminated", "regular season", "cut-off")):
        return "ELIGIBLE"

    return None

def is_ignorable_row(row: list) -> bool:
    """
    Determina si una fila en data_rows es un encabezado intermedio, leyenda, nota, pie de tabla
    o divisor de sección (ej. Championship 4 en NASCAR).
    Permite omitir únicamente estas filas sin rechazar el lote ni descartar participantes legítimos.
    """
    if not row:
        return True

    # 1. Todos los elementos son TH (encabezado intermedio repetido)
    if all(c.get("tag") == "th" for c in row):
        return True

    # 2. Fila estructural de nota, pie de tabla o divisor de sección
    if len(row) == 1 and row[0].get("colspan", 1) > 1:
        return True

    # Si todas las columnas de la fila apuntan a la misma celda (fila de ancho completo expandida)
    if len(row) > 1 and all(c is row[0] for c in row) and row[0].get("colspan", 1) > 1:
        return True

    row_text = " ".join(c.get("text", "") for c in row).strip()
    if not row_text:
        return True

    row_lower = row_text.lower()

    # 3. Encabezado repetido compuesto exclusivamente por palabras clave de columnas
    header_keywords = {
        "pos", "position", "pos.", "rk", "rank", "no", "nat", "driver", "rider",
        "car", "team", "constructor", "manufacturer", "points", "pts", "pts.",
        "rounds", "grand", "prix", "races", "entry"
    }
    words = set(re.findall(r"\w+", row_lower))
    if words and words.issubset(header_keywords):
        return True

    # 4. Notas al pie o leyendas estructuradas: sólo si la fila tiene 1 o 2 celdas
    # y comienza con un prefijo inequívoco de nota/leyenda. Nunca aplicar a filas normales de datos (>= 3 celdas).
    if len(row) <= 2:
        legend_prefixes = (
            "key:", "legend:", "note:", "notes:", "source:", "source :",
            "bold indicates", "italic indicates", "asterisk", "key / legend",
            "unclassified", "abbreviations", "points system:"
        )
        first_text = row[0].get("text", "").lower().strip()
        if any(first_text.startswith(p) for p in legend_prefixes):
            return True

    return False

def parse_table_grid(table_html: str):
    """
    Parsea una tabla HTML y resuelve una cuadrícula 2D completa (grid) expandiendo
    estructuradamente atributos rowspan y colspan tanto en encabezados como en celdas de datos.
    Retorna (column_headers, data_rows, is_unsupported_complex).
    """
    row_matches = re.findall(r"<tr\b([^>]*)>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE)
    if not row_matches:
        return [], [], True

    parsed_rows = []
    for tr_attrs, tr_body in row_matches:
        cell_matches = re.finditer(r"<(th|td)\b([^>]*)>(.*?)</\1>", tr_body, re.DOTALL | re.IGNORECASE)
        cells = []
        for cm in cell_matches:
            tag = cm.group(1).lower()
            attrs = cm.group(2)
            inner_html = cm.group(3)

            rowspan_m = re.search(r"\browspan\s*=\s*['\"]?(\d+)", attrs, re.IGNORECASE)
            colspan_m = re.search(r"\bcolspan\s*=\s*['\"]?(\d+)", attrs, re.IGNORECASE)
            rowspan = int(rowspan_m.group(1)) if rowspan_m else 1
            colspan = int(colspan_m.group(1)) if colspan_m else 1

            cells.append({
                "tag": tag,
                "attrs": attrs,
                "rowspan": max(1, rowspan),
                "colspan": max(1, colspan),
                "inner_html": inner_html,
                "text": clean_html_text(inner_html)
            })
        if cells:
            parsed_rows.append(cells)

    if not parsed_rows:
        return [], [], True

    header_rows = []
    data_rows = []
    in_header = True

    for row in parsed_rows:
        is_th_only = all(c["tag"] == "th" for c in row)
        has_scope_col = any("scope=\"col\"" in c["attrs"].lower() or "scope='col'" in c["attrs"].lower() for c in row)
        if in_header and (is_th_only or has_scope_col):
            header_rows.append(row)
        else:
            in_header = False
            data_rows.append(row)

    if not header_rows and parsed_rows:
        if any(c["tag"] == "th" for c in parsed_rows[0]):
            header_rows = [parsed_rows[0]]
            data_rows = parsed_rows[1:]
        else:
            header_rows = [parsed_rows[0]]
            data_rows = parsed_rows[1:]

    # Resolver matriz de encabezados (header_grid) y trasladar a data_grid cualquier rowspan
    # originado en header_rows que continúe verticalmente dentro de data_rows (ej. separador
    # vertical <th rowspan="17"></th> en NASCAR Cup 2026), preservando índices absolutos de columna.
    num_header_rows = len(header_rows)
    num_data_rows = len(data_rows)
    total_parsed_rows = num_header_rows + num_data_rows

    header_grid = {}
    data_grid = {}
    for r_idx, row in enumerate(header_rows):
        c_idx = 0
        for cell in row:
            while (r_idx, c_idx) in header_grid:
                c_idx += 1
            # Rechazar spans verticales imposibles que excedan el total de filas de la tabla
            if r_idx + cell["rowspan"] > total_parsed_rows:
                return [], [], True
            text = cell["text"]
            for dr in range(cell["rowspan"]):
                abs_r = r_idx + dr
                for dc in range(cell["colspan"]):
                    abs_c = c_idx + dc
                    if abs_r < num_header_rows:
                        # Rechazar solapamiento 2D dentro de los propios encabezados
                        if (abs_r, abs_c) in header_grid:
                            return [], [], True
                        header_grid[(abs_r, abs_c)] = text
                    else:
                        # Propagar la celda del encabezado hacia data_grid en la misma columna absoluta
                        data_r = abs_r - num_header_rows
                        if (data_r, abs_c) in data_grid:
                            return [], [], True
                        data_grid[(data_r, abs_c)] = cell
            c_idx += cell["colspan"]

    num_cols = max((c + 1 for (r, c) in header_grid), default=0)

    column_headers = []
    for c in range(num_cols):
        parts = []
        for r in range(num_header_rows):
            t = header_grid.get((r, c), "")
            if t and t.lower() not in [p.lower() for p in parts]:
                parts.append(t)
        column_headers.append(" ".join(parts).lower())

    if num_cols == 0:
        return [], [], True

    # Resolver filas de datos y subcabeceras repetidas en cuadrícula 2D unificada
    for r_idx, row in enumerate(data_rows):
        # Divisor de sección o pie de ancho completo (ej. <th colspan="41">Ineligible for driver points</th>)
        if len(row) == 1 and row[0]["colspan"] > 1:
            if row[0]["rowspan"] > 1:
                return [], [], True
            # Comprobar si el divisor/pie colisiona con algún rowspan activo en esta fila:
            # - Si colisiona con datos o encabezados con texto, es un solapamiento inválido -> rechazar.
            # - Si solo colisiona con un separador vertical decorativo vacío (<th rowspan="N"></th>),
            #   limpiar cualquier extensión residual del separador decorativo y expandir la fila de ancho completo.
            for c in range(num_cols):
                existing = data_grid.get((r_idx, c))
                if existing is not None:
                    if not (existing["tag"] == "th" and existing["text"] == ""):
                        return [], [], True
                    for rr in range(r_idx + 1, num_data_rows):
                        if data_grid.get((rr, c)) is existing:
                            del data_grid[(rr, c)]
            for c in range(num_cols):
                data_grid[(r_idx, c)] = row[0]
            continue

        c_idx = 0
        for cell in row:
            while (r_idx, c_idx) in data_grid:
                c_idx += 1
            # Rechazar desbordamiento horizontal respecto a las columnas definidas por el encabezado
            if c_idx + cell["colspan"] > num_cols:
                return [], [], True
            # Rechazar spans verticales imposibles que excedan las filas disponibles en data_rows
            if r_idx + cell["rowspan"] > num_data_rows:
                return [], [], True
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    target_pos = (r_idx + dr, c_idx + dc)
                    # Rechazar solapamientos 2D (ej. colspan invadiendo una columna ocupada por rowspan)
                    if target_pos in data_grid:
                        return [], [], True
                    data_grid[target_pos] = cell
            c_idx += cell["colspan"]

    grid_data_rows = []
    for r_idx in range(len(data_rows)):
        row_cells = []
        for c in range(num_cols):
            cell = data_grid.get((r_idx, c))
            if cell is not None:
                row_cells.append(cell)
            else:
                row_cells.append({
                    "tag": "td",
                    "attrs": "",
                    "rowspan": 1,
                    "colspan": 1,
                    "inner_html": "",
                    "text": ""
                })
        grid_data_rows.append(row_cells)

    return column_headers, grid_data_rows, False

def parse_retry_after(header_value):
    """
    Interpreta la cabecera estándar HTTP Retry-After según RFC 9110:
    - Si es un número de segundos no negativo (ej. '3' o '2.5'), retorna float(segundos).
    - Si es una fecha HTTP (ej. 'Fri, 27 Nov 2026 10:15:30 GMT'), calcula el delta respecto a UTC.
    - Si está ausente, vacía, negativa o malformada, retorna None.
    """
    if header_value is None:
        return None
    raw = str(header_value).strip()
    if not raw:
        return None
    if re.match(r"^\d+(?:\.\d+)?$", raw):
        val = float(raw)
        return val if val >= 0.0 else None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        delta = (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def fetch_wikimedia_html(
    url: str,
    category: str,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    max_retries: int = MAX_TRANSIENT_RETRIES,
    max_retry_after: float = MAX_RETRY_AFTER_SECONDS,
):
    """
    Descarga el HTML de Wikipedia cumpliendo la política de acceso de Wikimedia:
    - Envía User-Agent transparente con identidad real del repositorio y sin tokens de navegador.
    - Aplica timeout explícito de 15.0 segundos en cada llamada a urlopen.
    - Gestiona los estados HTTP de forma diferenciada y sin reintentos agresivos:
      * HTTP 200: retorna (contenido_html, False).
      * HTTP 429 con Retry-After válido <= max_retry_after: respeta exactamente Retry-After y ejecuta
        como máximo 1 único reintento; si persiste o supera el techo, detiene la fuente.
      * HTTP 429 sin Retry-After: ante incertidumbre sobre la ventana de bloqueo, realiza 0 reintentos
        y detiene inmediatamente la extracción de esa fuente.
      * HTTP 403 (y otros 4xx): 0 reintentos y detiene la fuente.
      * HTTP 500, 502, 503: error temporal del servidor; respeta Retry-After si existe (<= max_retry_after)
        o espera DEFAULT_TRANSIENT_BACKOFF_SECONDS (2.0s) para como máximo 1 único reintento.
      * Timeout (socket.timeout / TimeoutError / URLError): 0 reintentos y detiene la fuente.
    Retorna (html_text_or_None, stop_source_bool).
    """
    headers = {
        "User-Agent": WIKIMEDIA_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            try:
                raw_bytes = resp.read()
            finally:
                close_fn = getattr(resp, "close", None)
                if callable(close_fn):
                    close_fn()
            return raw_bytes.decode("utf-8"), False

        except urllib.error.HTTPError as exc:
            status_code = getattr(exc, "code", None)
            hdrs = getattr(exc, "headers", None)
            retry_after_raw = hdrs.get("Retry-After") if hdrs is not None and hasattr(hdrs, "get") else None
            retry_after_sec = parse_retry_after(retry_after_raw)
            close_exc = getattr(exc, "close", None)
            if callable(close_exc):
                close_exc()

            if status_code == 429:
                if retry_after_sec is not None and retry_after_sec <= max_retry_after and attempt < max_retries:
                    print(
                        f" /!\\ HTTP 429 en {category} ({url}) con Retry-After={retry_after_sec}s; "
                        f"respetando espera antes de un único reintento ({attempt + 1}/{max_retries})."
                    )
                    time.sleep(retry_after_sec)
                    continue
                print(
                    f"Error HTTP 429 al descargar página de Wikipedia para {category} "
                    f"(Retry-After={retry_after_raw!r}). Deteniendo fuente sin reintentos adicionales."
                )
                return None, True

            if status_code == 403:
                print(f"Error HTTP 403 (Forbidden) al descargar Wikipedia para {category} ({url}). Deteniendo fuente.")
                return None, True

            if status_code in (500, 502, 503):
                if retry_after_sec is not None and retry_after_sec > max_retry_after:
                    print(
                        f"Error HTTP {status_code} en {category} con Retry-After={retry_after_sec}s "
                        f"(> {max_retry_after}s). Deteniendo fuente sin bloquear."
                    )
                    return None, True
                if attempt < max_retries:
                    wait_sec = retry_after_sec if retry_after_sec is not None else DEFAULT_TRANSIENT_BACKOFF_SECONDS
                    print(
                        f" /!\\ Error temporal HTTP {status_code} en {category} ({url}); "
                        f"esperando {wait_sec}s para un único reintento ({attempt + 1}/{max_retries})."
                    )
                    time.sleep(wait_sec)
                    continue
                print(f"Error temporal persistente HTTP {status_code} en {category} ({url}). Deteniendo fuente.")
                return None, True

            print(f"Error HTTP {status_code} al descargar página de Wikipedia para {category}: {exc}")
            return None, True

        except (socket.timeout, TimeoutError) as exc:
            print(f"Timeout ({timeout}s) al descargar página de Wikipedia para {category} ({url}): {exc}")
            return None, True

        except urllib.error.URLError as exc:
            print(f"Error de conexión al descargar página de Wikipedia para {category} ({url}): {exc}")
            return None, True

        except Exception as exc:
            print(f"Error inesperado al descargar página de Wikipedia para {category}: {exc}")
            return None, True

    return None, True


def fetch_wikipedia_standings(category, urls):
    target_year = None
    if urls:
        m = re.search(r"(?:/|_)(20\d{2})(?:/|_)", urls[0])
        if m:
            target_year = m.group(1)

    for url in urls:
        if target_year:
            m = re.search(r"(?:/|_)(20\d{2})(?:/|_)", url)
            if m and m.group(1) != target_year:
                print(f" /!\\ Omitiendo '{url}': corresponde a temporada {m.group(1)} (distinta a {target_year}).")
                continue

        print(f"Buscando posiciones en Wikipedia para {category} ({url})...")
        content, stop_source = fetch_wikimedia_html(url, category)
        if content is None:
            if stop_source:
                break
            continue

        # Extraer tablas hoja mediante analizador con pila para soportar tablas contenedoras anidadas
        leaf_tables = extract_leaf_tables(content)
        for tbl in leaf_tables:
            table_attrs = tbl["attrs"]
            table_body = tbl["body"]
            start_pos = tbl["outer_start"]
            end_pos = tbl["outer_end"]

            # Estructura compleja: descartar si hay desplazamiento de columnas por rowspan/colspan
            if has_complex_table_structure(table_body):
                print(f" /!\\ Omitiendo tabla con estructura compleja (rowspan/colspan desplazados) en {category}.")
                continue

            # Contexto de notas inmediatamente posterior a esta tabla (sin invadir otras tablas o secciones)
            post_table = content[end_pos : end_pos + 3000]
            next_boundary = re.search(r"<(?:h[1-6]|table)\b", post_table, re.IGNORECASE)
            table_notes = post_table[:next_boundary.start()] if next_boundary else post_table[:1500]
            table_context = f"{table_body}\n{table_notes}"

            # Contexto previo (encabezado h1-h6 anterior y caption de la tabla)
            preceding = content[:start_pos]
            caption_match = re.search(r"<caption.*?>(.*?)</caption>", table_body, re.DOTALL | re.IGNORECASE)
            caption = clean_html_text(caption_match.group(1)).lower() if caption_match else ""

            heading_matches = list(re.finditer(r"<h([1-6])[^>]*>(.*?)</h\1>", preceding, re.DOTALL | re.IGNORECASE))
            if heading_matches:
                last_heading_match = heading_matches[-1]
                intermediate = preceding[last_heading_match.end():]
                # Si entre el encabezado y esta tabla ya se cerró otra tabla (</table>) y esta tabla no tiene caption propio,
                # el encabezado pertenecía a una sección o tabla anterior ya consumida.
                if "</table>" in intermediate.lower() and not caption:
                    last_heading = ""
                elif len(intermediate) > 4000 and not caption:
                    last_heading = ""
                else:
                    last_heading = clean_html_text(last_heading_match.group(2)).lower()
            else:
                last_heading = ""

            full_context = f"{last_heading} {caption}".strip()

            # Filtros por categoría y verificación positiva
            if category == "WEC":
                # Verificación positiva estricta para campeonato de pilotos Hypercar
                is_hypercar = "hypercar" in full_context
                is_driver = any(kw in full_context for kw in ["driver", "drivers", "piloto", "pilotos"])
                is_excluded = any(term in full_context for term in ["lmgt3", "gt3", "lmp2", "manufacturer", "constructor", "team", "teams", "cup"])
                if not (is_hypercar and is_driver and not is_excluded):
                    continue
            elif category == "WRC":
                if any(term in full_context for term in ["co-driver", "codriver", "copiloto", "manufacturer", "constructor", "fabricante"]):
                    continue
            elif category == "INDYCAR":
                if any(term in full_context for term in ["entrant", "engine", "chassis"]):
                    continue
            elif category == "NASCAR":
                if any(term in full_context for term in ["manufacturer", "owner"]):
                    continue
            elif category == "MOTOGP":
                if any(term in full_context for term in ["constructor", "constructors", "manufacturer", "manufacturers", "team", "teams"]) and not any(term in full_context for term in ["rider", "riders", "piloto", "pilotos"]):
                    continue

            # Parsear encabezados multinivel y filas de datos rechazando cuadrículas complejas/ambiguas
            column_headers, data_rows, is_complex = parse_table_grid(table_body)
            if is_complex or not column_headers or not data_rows:
                continue

            name_idx = find_driver_col_index(column_headers, category)
            points_idx = find_points_col_index(column_headers)
            pos_idx = find_pos_col_index(column_headers)

            if name_idx is None or points_idx is None:
                continue

            standings = []
            batch_has_error = False
            default_pos = 1
            # Estado de subsección local exclusivo de esta tabla (nunca se filtra a otras tablas ni categorías)
            in_nascar_ineligible_section = False
            last_logical_cells = None

            table_lower = table_body.lower()
            dash_verified_as_zero = bool(re.search(
                r"(?:[-–—−]\s*[:=]\s*0\b|dash\s+indicates\s+0\b|guion\s+indica\s+0\b|[-–—−]\s*indicates\s+zero)",
                table_lower
            ))

            for row in data_rows:
                # En NASCAR, actualizar estado si la fila es un encabezado interno de subsección de ancho completo
                if category == "NASCAR":
                    sec_kind = classify_nascar_section_row(row)
                    if sec_kind == "INELIGIBLE":
                        in_nascar_ineligible_section = True
                        continue
                    elif sec_kind == "ELIGIBLE":
                        in_nascar_ineligible_section = False
                        continue

                # Comprobar si la fila es encabezado intermedio, leyenda o pie de página
                if is_ignorable_row(row):
                    continue

                # Si no es ignorable, debe contener todas las columnas requeridas (pos, name, points)
                if len(row) <= max(name_idx, points_idx):
                    print(f"Error: Fila incompleta ({len(row)} celdas <= índice requerido {max(name_idx, points_idx)}) en clasificación de {category}. Rechazando lote.")
                    batch_has_error = True
                    break

                # Detectar subfilas de continuación demostrada de un mismo participante (misma instancia de celda
                # expandida por rowspan en Posición, Piloto y Puntos, ej. piloto con dos equipos en MotoGP)
                current_logical_cells = (
                    row[pos_idx] if (pos_idx is not None and pos_idx < len(row)) else None,
                    row[name_idx],
                    row[points_idx],
                )
                if (
                    last_logical_cells is not None
                    and current_logical_cells[1] is last_logical_cells[1]
                    and current_logical_cells[2] is last_logical_cells[2]
                    and (pos_idx is None or current_logical_cells[0] is last_logical_cells[0])
                    and current_logical_cells[1].get("rowspan", 1) > 1
                ):
                    continue

                # Extraer posición oficial si está presente
                raw_pos = ""
                if pos_idx is not None and pos_idx < len(row):
                    raw_pos = row[pos_idx]["text"]
                    raw_pos = re.sub(r"\[.*?\]", "", raw_pos).strip()

                # Distinguir filas decorativas o no clasificatorias (NC, DNQ, EX, DQ, etc.)
                if raw_pos.lower() in ["nc", "dnq", "ex", "dq", "dsq", "ret", "wd", "dna", "dnp"]:
                    continue

                name = row[name_idx]["text"]
                name = re.sub(r"\[.*?\]", "", name).strip()
                name = re.sub(r"\s*\([iI]\)", "", name)
                name = name.rstrip("†*‡ ").strip()

                is_valid_name = (
                    bool(name) and
                    not name.isdigit() and
                    len(name) <= 40 and
                    name.lower() not in ["pos", "rider", "driver", "points", "pts", "total", "team", "manufacturer", "key"] and
                    not (name.startswith("#") and ("racing" in name.lower() or "team" in name.lower() or "global" in name.lower()))
                )

                if not is_valid_name:
                    if raw_pos.isdigit() and int(raw_pos) > 0:
                        print(f"Error: Fila con posición válida '{raw_pos}' pero nombre de piloto inválido/ausente '{name}' ({category}). Columnas desplazadas o incompletas. Rechazando lote.")
                    else:
                        print(f"Error: Fila de participante no interpretable en {category}: pos='{raw_pos}', name='{name}'. Rechazando lote.")
                    batch_has_error = True
                    break

                # Extraer puntos de la columna identificada por encabezado
                raw_pts_html = row[points_idx]["inner_html"]
                pts = parse_points_cell(raw_pts_html, dash_verified_as_zero=dash_verified_as_zero)

                # En NASCAR, si la fila pertenece a la subsección explícita 'Ineligible for driver points',
                # exigir ausencia tanto de puntos numéricos como de posición numérica y omitir al participante
                if category == "NASCAR" and in_nascar_ineligible_section:
                    has_numeric_pos = bool(raw_pos) and any(ch.isdigit() for ch in raw_pos)
                    if pts is not None or has_numeric_pos:
                        print(
                            f"Error: Registro inconsistente con posición o puntos numéricos dentro de subsección "
                            f"inelegible ({category}): '{name}' (pos='{raw_pos}', pts='{raw_pts_html}'). Rechazando lote."
                        )
                        batch_has_error = True
                        break
                    print(f" -> Omitiendo participante en subsección inelegible para puntos ({category}): '{name}'.")
                    continue

                # Si los puntos son ilegibles o ausentes fuera de subsección inelegible:
                if pts is None:
                    # Inelegibilidad reglamentaria por marca individual: EXCLUSIVA para NASCAR Cup con evidencia inequívoca
                    if category == "NASCAR":
                        is_inelig, marker = is_ineligible_driver(row, name_idx, pos_idx, points_idx, table_context)
                        if is_inelig:
                            print(f" -> Omitiendo participante inelegible para puntos de campeonato ({category}): '{name}' (marca: '{marker}').")
                            continue

                    print(f"Error: Puntos ilegibles en piloto válido '{name}' ({category}): '{raw_pts_html}'. Rechazando lote.")
                    batch_has_error = True
                    break

                # Validar posición oficial: rechazar si la columna existe pero su valor es corrupto o ambiguo
                if pos_idx is not None:
                    if raw_pos.isdigit() and int(raw_pos) > 0:
                        pos = int(raw_pos)
                    else:
                        print(f"Error: Posición inválida o ambigua en piloto válido '{name}' ({category}): '{raw_pos}'. Rechazando lote.")
                        batch_has_error = True
                        break
                else:
                    pos = default_pos

                standing = {
                    "position": pos,
                    "name": name,
                    "points": pts,
                    "wins": 0,
                    "category": category,
                    "isDriver": True,
                    "teamName": "",
                    "nationality": ""
                }
                standings.append(standing)
                last_logical_cells = current_logical_cells
                default_pos += 1

            if batch_has_error:
                continue

            if standings:
                print(f" -> Clasificación sincronizada con éxito ({len(standings)} pilotos).")
                return standings

    print(f" /!\\ No se encontraron tablas de clasificación válidas para {category}")
    return []

def _safe_unlink(path: str) -> None:
    """Elimina de forma segura un archivo temporal o de respaldo si todavía existe en disco."""
    if path:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f" /!\\ No se pudo limpiar el archivo temporal '{path}': {exc}")


def _write_temp_json_fsync(target_path: str, payload) -> str:
    """
    Serializa un payload JSON en un archivo temporal dentro del mismo directorio de target_path,
    ejecutando flush() y os.fsync() antes de cerrar el descriptor.
    Si ocurre cualquier excepción o interrupción, limpia el temporal sin modificar target_path.
    """
    abs_target = os.path.abspath(target_path)
    target_dir = os.path.dirname(abs_target) or "."
    base_name = os.path.basename(abs_target)
    os.makedirs(target_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=f".{base_name}.", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
            json.dump(payload, tmp_fh, ensure_ascii=False, indent=2)
            tmp_fh.flush()
            os.fsync(tmp_fh.fileno())
        return tmp_path
    except BaseException:
        _safe_unlink(tmp_path)
        raise


def atomic_write_json(target_path: str, payload) -> None:
    """Sustituye atómicamente un único archivo JSON mediante temporal en el mismo volumen + os.replace()."""
    tmp_path = _write_temp_json_fsync(target_path, payload)
    try:
        os.replace(tmp_path, target_path)
    except BaseException:
        _safe_unlink(tmp_path)
        raise


def _create_same_dir_backup(target_path: str) -> str:
    """Crea una copia exacta sincronizada a disco (.bak) en el mismo directorio para rollback compensatorio."""
    abs_target = os.path.abspath(target_path)
    target_dir = os.path.dirname(abs_target) or "."
    base_name = os.path.basename(abs_target)
    fd, bak_path = tempfile.mkstemp(prefix=f".{base_name}.", suffix=".bak", dir=target_dir)
    try:
        with os.fdopen(fd, "wb") as bak_fh, open(abs_target, "rb") as src_fh:
            shutil.copyfileobj(src_fh, bak_fh)
            bak_fh.flush()
            os.fsync(bak_fh.fileno())
        return bak_path
    except BaseException:
        _safe_unlink(bak_path)
        raise


def sync_standings_to_file(
    standings_path: str,
    wiki_sources: dict = None,
    inter_request_delay: float = INTER_CATEGORY_DELAY_SECONDS,
) -> bool:
    """
    Sincroniza todas las clasificaciones configuradas y actualiza standings_path únicamente
    si todas las fuentes responden con datos válidos, preservando intactos los datos existentes
    cuando cualquier solicitud HTTP falla o resulta rechazada.
    """
    sources = WIKI_STANDINGS if wiki_sources is None else wiki_sources
    all_standings = {}
    for idx, (category, urls) in enumerate(sources.items()):
        if idx > 0 and inter_request_delay > 0:
            time.sleep(inter_request_delay)
        standings = fetch_wikipedia_standings(category, urls)
        if not standings:
            print(
                f" /!\\ Fallo o respuesta vacía en '{category}'; se preservan los datos existentes "
                f"en '{standings_path}' sin sobrescribir con un archivo incompleto."
            )
            return False
        all_standings[category] = standings

    if all_standings and len(all_standings) == len(sources):
        atomic_write_json(standings_path, all_standings)
        print(f" Clasificaciones {standings_path} actualizadas con éxito.")
        return True
    return False


def sync_all_data(
    races_path: str,
    standings_path: str,
    *,
    expected_season: int = 2026,
    ics_sources: dict = None,
    wiki_sources: dict = None,
    inter_request_delay: float = INTER_CATEGORY_DELAY_SECONDS,
) -> bool:
    """
    Ejecuta la sincronización conjunta todo-o-nada de calendarios y clasificaciones (BACK-03B):
    1. Si falla la extracción de cualquier categoría en ICS o Wikipedia, bloquea toda escritura.
    2. Escribe ambos candidatos en archivos temporales (.tmp) con flush() + os.fsync().
    3. Ejecuta validate_data.validate_pipeline() sobre ambos temporales ANTES de tocar archivos productivos.
    4. Promueve los temporales mediante os.replace() con respaldo previo (.bak) y rollback compensatorio
       si falla el segundo os.replace().
       Nota de límites: dos llamadas consecutivas a os.replace() no constituyen una transacción atómica
       del kernel ante una terminación abrupta (SIGKILL/corte eléctrico) justo entre ambas sustituciones.
    """
    ics_map = ICS_URLS if ics_sources is None else ics_sources
    wiki_map = WIKI_STANDINGS if wiki_sources is None else wiki_sources
    if not ics_map or not wiki_map:
        return False

    all_races = []
    for category, url in ics_map.items():
        cat_races = parse_ics_calendar(category, url)
        if not cat_races:
            print(
                f" /!\\ Extracción de calendario vacía o fallida en '{category}'; "
                "se bloquea la actualización conjunta (política todo-o-nada)."
            )
            return False
        all_races.extend(cat_races)

    all_standings = {}
    for idx, (category, urls) in enumerate(wiki_map.items()):
        if idx > 0 and inter_request_delay > 0:
            time.sleep(inter_request_delay)
        standings = fetch_wikipedia_standings(category, urls)
        if not standings:
            print(
                f" /!\\ Extracción de clasificación vacía o fallida en '{category}'; "
                "se bloquea la actualización conjunta (política todo-o-nada)."
            )
            return False
        all_standings[category] = standings

    tmp_races = None
    tmp_standings = None
    bak_races = None
    preserve_backup_on_exit = False
    try:
        try:
            tmp_races = _write_temp_json_fsync(races_path, all_races)
            tmp_standings = _write_temp_json_fsync(standings_path, all_standings)
        except OSError as exc:
            print(f"Error de disco escribiendo archivos temporales candidatos: {exc}")
            return False

        overall_valid, val_results = validate_data.validate_pipeline(
            tmp_races,
            tmp_standings,
            expected_season=expected_season,
        )
        if not overall_valid:
            validate_data.print_report(val_results)
            print(" /!\\ Validación conjunta previa rechazada; archivos productivos preservados intactos.")
            return False

        races_existed = os.path.exists(races_path)
        if races_existed:
            try:
                bak_races = _create_same_dir_backup(races_path)
            except OSError as exc:
                print(f"Error preparando respaldo previo de '{races_path}': {exc}")
                return False

        try:
            os.replace(tmp_races, races_path)
            tmp_races = None
        except OSError as exc:
            print(f"Error promoviendo '{races_path}': {exc}")
            return False

        try:
            os.replace(tmp_standings, standings_path)
            tmp_standings = None
        except BaseException as replace_exc:
            # Rollback compensatorio: diferenciar estrictamente ROLLBACK_OK de ROLLBACK_FAILED y
            # preservar el archivo .bak en disco para recuperación manual si la restauración falla.
            rollback_state = "ROLLBACK_FAILED"
            rollback_detail = ""
            try:
                if races_existed:
                    if not bak_races or not os.path.exists(bak_races):
                        preserve_backup_on_exit = True
                        rollback_detail = "respaldo previo ausente o inaccesible"
                    else:
                        os.replace(bak_races, races_path)
                        bak_races = None
                        rollback_state = "ROLLBACK_OK"
                else:
                    if os.path.exists(races_path):
                        os.unlink(races_path)
                    rollback_state = "ROLLBACK_OK"
            except OSError as rb_exc:
                rollback_state = "ROLLBACK_FAILED"
                rollback_detail = str(rb_exc)
                if races_existed and bak_races:
                    preserve_backup_on_exit = True

            if rollback_state == "ROLLBACK_OK":
                print(
                    f"[ROLLBACK_OK] Falló la promoción de '{standings_path}' ({replace_exc}); "
                    f"se restauró el estado previo de '{races_path}'."
                )
            else:
                if races_existed and bak_races:
                    print(
                        f"[ROLLBACK_FAILED] Falló la promoción de '{standings_path}' ({replace_exc}) y "
                        f"falló la restauración de '{races_path}' ({rollback_detail}). NO se restauró el "
                        f"estado previo; se conserva intacto el respaldo para recuperación manual en: '{bak_races}'."
                    )
                else:
                    print(
                        f"[ROLLBACK_FAILED] Falló la promoción de '{standings_path}' ({replace_exc}) y "
                        f"no pudo retirarse '{races_path}' recién creado ({rollback_detail}). Estado local inconsistente."
                    )

            if isinstance(replace_exc, Exception):
                return False
            raise

        print(f" Conjunto actualizado y validado con éxito: {races_path} y {standings_path}.")
        return True
    finally:
        _safe_unlink(tmp_races)
        _safe_unlink(tmp_standings)
        if not preserve_backup_on_exit:
            _safe_unlink(bak_races)


def resolve_ci_notification_state(env_map: dict = None) -> tuple:
    """
    Resuelve el estado final comprobado del workflow de GitHub Actions:
    - ('PUBLISHED', '') solo si el job y el paso publish terminaron en 'success' con PUBLISH_RESULT='PUBLISHED'.
    - ('NO_CHANGES', '') si el job y el paso publish terminaron en 'success' con PUBLISH_RESULT='NO_CHANGES'.
    - ('FAILED', '<etapa>') en cualquier otro caso, identificando el primer paso fallido o cancelado.
    """
    env = os.environ if env_map is None else env_map
    job_status = (env.get("JOB_STATUS") or "").strip().lower()
    step_publish = (env.get("STEP_PUBLISH") or "").strip().lower()
    publish_result = (env.get("PUBLISH_RESULT") or "").strip().upper()

    if job_status == "success" and step_publish == "success":
        if publish_result == "PUBLISHED":
            return "PUBLISHED", ""
        if publish_result == "NO_CHANGES":
            return "NO_CHANGES", ""
        return "FAILED", "publish_output_missing"

    ordered_steps = (
        ("checkout", env.get("STEP_CHECKOUT")),
        ("setup_python", env.get("STEP_SETUP")),
        ("update", env.get("STEP_UPDATE")),
        ("unit_tests", env.get("STEP_TESTS")),
        ("validate", env.get("STEP_VALIDATE")),
        ("publish", env.get("STEP_PUBLISH")),
    )
    for step_name, raw_state in ordered_steps:
        state = (raw_state or "").strip().lower()
        if state in ("failure", "cancelled", "timed_out"):
            return "FAILED", f"{step_name} ({state})"

    return "FAILED", f"job_status={job_status or 'unknown'}"


def dispatch_workflow_notification(
    outcome: str,
    *,
    failed_step: str = "",
    commit_sha: str = "",
    run_url: str = "",
) -> bool:
    """
    Despacha notificaciones veraces según la política aprobada 2A:
    - NO_CHANGES: silencio estricto (retorna False sin enviar mensaje).
    - PUBLISHED: anuncia exclusivamente publicación remota confirmada tras git push exitoso.
    - FAILED (o valor desconocido): envía una única alerta indicando la etapa comprobada del fallo.
    """
    norm_outcome = (outcome or "").strip().upper()
    if norm_outcome == "NO_CHANGES":
        print("Estado comprobado: NO_CHANGES (sin cambios que publicar; notificación silenciosa).")
        return False

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if norm_outcome == "PUBLISHED":
        lines = ["✅ GridBox Data: Publicación remota confirmada (races.json y standings.json)."]
        if commit_sha:
            lines.append(f"Commit: {commit_sha.strip()}")
        if run_url:
            lines.append(f"Workflow: {run_url.strip()}")
        msg = _redact_secrets("\n".join(lines), token, chat_id)
        return send_telegram_message(msg)

    step_detail = failed_step.strip() or (f"estado desconocido ({norm_outcome})" if norm_outcome != "FAILED" else "no especificada")
    lines = [
        "🚨 GridBox Data: Fallo comprobado en sincronización/publicación.",
        f"Etapa: {step_detail}",
    ]
    if run_url:
        lines.append(f"Workflow: {run_url.strip()}")
    msg = _redact_secrets("\n".join(lines), token, chat_id)
    return send_telegram_message(msg)


# -------------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sincronizador y notificador CI verificado de datos GridBox")
    parser.add_argument("--races-out", default=os.path.join(_SCRIPT_DIR, "races.json"))
    parser.add_argument("--standings-out", default=os.path.join(_SCRIPT_DIR, "standings.json"))
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=INTER_CATEGORY_DELAY_SECONDS)
    parser.add_argument(
        "--notify-from-ci",
        action="store_true",
        help="Evalúa resultados comprobados del workflow CI y notifica según política 2A",
    )

    args = parser.parse_args([] if argv is None else argv)

    if args.notify_from_ci:
        outcome, failed_step = resolve_ci_notification_state()
        dispatch_workflow_notification(
            outcome,
            failed_step=failed_step,
            commit_sha=os.environ.get("COMMIT_SHA", ""),
            run_url=os.environ.get("RUN_URL", ""),
        )
        return 0

    ok = sync_all_data(
        args.races_out,
        args.standings_out,
        expected_season=args.season,
        inter_request_delay=args.delay,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
