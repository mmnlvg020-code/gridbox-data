import urllib.request
import re
import html
import json
import datetime

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
# 2. PÁGINAS DE WIKIPEDIA PARA ESTADÍSTICAS Y POSICIONES
# -------------------------------------------------------------
WIKI_STANDINGS = {
    "WRC": [
        "https://en.wikipedia.org/wiki/2026_World_Rally_Championship",
        "https://en.wikipedia.org/wiki/2025_World_Rally_Championship"
    ],
    "WEC": [
        "https://en.wikipedia.org/wiki/2026_FIA_World_Endurance_Championship",
        "https://en.wikipedia.org/wiki/2025_FIA_World_Endurance_Championship"
    ],
    "MOTOGP": [
        "https://en.wikipedia.org/wiki/2026_MotoGP_World_Championship",
        "https://en.wikipedia.org/wiki/2025_MotoGP_World_Championship"
    ],
    "NASCAR": [
        "https://en.wikipedia.org/wiki/2026_NASCAR_Cup_Series",
        "https://en.wikipedia.org/wiki/2025_NASCAR_Cup_Series"
    ],
    "INDYCAR": [
        "https://en.wikipedia.org/wiki/2026_IndyCar_Series",
        "https://en.wikipedia.org/wiki/2025_IndyCar_Series"
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
    
    # Quitar prefijos molestos
    name = re.sub(r"^(FIA\s+)?(WEC|WRC|MotoGP|IndyCar|NASCAR)\s*-\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^(FIA\s+)?(WEC|WRC|MotoGP|IndyCar|NASCAR)\s*", "", name, flags=re.IGNORECASE)
    
    # Traducciones básicas para que se vea premium en español
    name = name.replace("Grand Prix of", "Gran Premio de")
    name = name.replace("GP of", "Gran Premio de")
    name = name.replace("GP", "Gran Premio")
    name = name.replace("6 Hours of", "6 Horas de")
    name = name.replace("8 Hours of", "8 Horas de")
    name = name.replace("24 Hours of", "24 Horas de")
    name = name.replace("Rally of", "Rally de")
    name = name.replace("Rally", "Rally de")
    
    name = " ".join(name.split())
    # Corregir doble "de" si se traduce mal
    name = name.replace("Rally de de", "Rally de")
    return name

def parse_ics_calendar(category, url):
    print(f"Descargando calendario ICS para {category}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        content = urllib.request.urlopen(req).read().decode('utf-8')
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
            "circuitId": ""
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
def fetch_wikipedia_standings(category, urls):
    name_col_keywords = ["rider", "driver", "drivers", "piloto", "pilotos"]
    points_col_keywords = ["points", "pts", "puntos"]
    
    for url in urls:
        print(f"Buscando posiciones en Wikipedia para {category} ({url})...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            content = urllib.request.urlopen(req).read().decode('utf-8')
        except Exception as e:
            print(f"Error al descargar página de Wikipedia: {e}")
            continue
            
        tables = re.findall(r"<table.*?>(.*?)</table>", content, re.DOTALL)
        for t in tables:
            if "wikitable" not in t:
                continue
                
            rows = re.findall(r"<tr.*?>(.*?)</tr>", t, re.DOTALL)
            if not rows:
                continue
                
            headers = []
            for r in rows:
                th_matches = re.findall(r"<th.*?>(.*?)</th>", r, re.DOTALL)
                if th_matches:
                    headers = [re.sub("<[^<]+?>", " ", h).strip().lower() for h in th_matches]
                    break
                    
            has_name = any(any(kw in h for kw in name_col_keywords) for h in headers)
            has_points = any(any(kw in h for kw in points_col_keywords) for h in headers)
            
            if not (has_name and has_points):
                continue
                
            standings = []
            pos = 1
            for r in rows:
                if 'scope="col"' in r:
                    continue
                    
                td_cells = re.findall(r"<td.*?>(.*?)</td>", r, re.DOTALL)
                cells_raw = re.findall(r"<(t[dh]).*?>(.*?)</\1>", r, re.DOTALL)
                cells_clean = [html.unescape(re.sub("<[^<]+?>", " ", c[1]).strip()) for c in cells_raw]
                
                if len(td_cells) < 1:
                    continue
                    
                # El nombre siempre es el primer td de la fila
                name = html.unescape(re.sub("<[^<]+?>", " ", td_cells[0]).strip())
                name = re.sub(r"\[.*?\]", "", name).strip()
                
                # Los puntos totales siempre se ubican al final
                pts_str = cells_clean[-1]
                try:
                    pts_str = re.sub(r"\[.*?\]", "", pts_str).strip()
                    pts_str = pts_str.replace(",", "")
                    pts = float(pts_str) if pts_str else 0.0
                except:
                    pts = 0.0
                    
                if not name or name.isdigit() or len(name) > 40 or name.lower() in ["pos", "rider", "driver", "points", "pts"]:
                    continue
                    
                # Limpiar texto de banderas o espacios extra
                name = re.sub(r"\[.*?\]", "", name).strip()
                
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
                pos += 1
                if pos > 10:
                    break
                    
            if standings:
                print(f" -> Clasificación sincronizada con éxito ({len(standings)} pilotos).")
                return standings
                
    print(f" /!\\ No se encontraron tablas de clasificación válidas para {category}")
    return []

# -------------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# -------------------------------------------------------------
def main():
    # 1. Sincronizar todos los calendarios reales
    all_races = []
    for category, url in ICS_URLS.items():
        all_races.extend(parse_ics_calendar(category, url))
        
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    races_path = os.path.join(script_dir, "races.json")
    standings_path = os.path.join(script_dir, "standings.json")
        
    if all_races:
        with open(races_path, "w", encoding="utf-8") as f:
            json.dump(all_races, f, ensure_ascii=False, indent=2)
        print(f" Calendario {races_path} actualizado con éxito.")
        
    # 2. Sincronizar todas las clasificaciones reales
    all_standings = {}
    for category, urls in WIKI_STANDINGS.items():
        standings = fetch_wikipedia_standings(category, urls)
        if standings:
            all_standings[category] = standings
            
    if all_standings:
        with open(standings_path, "w", encoding="utf-8") as f:
            json.dump(all_standings, f, ensure_ascii=False, indent=2)
        print(f" Clasificaciones {standings_path} actualizadas con éxito.")

if __name__ == "__main__":
    main()
