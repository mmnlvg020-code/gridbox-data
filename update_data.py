import requests
import json
import time

# API Key de desarrollo de TheSportsDB (Totalmente gratuita y pública)
TSDB_API_KEY = "3"
BASE_URL = f"https://www.thesportsdb.com/api/v1/json/{TSDB_API_KEY}"

# IDs de ligas en TheSportsDB
LEAGUE_IDS = {
    "WRC": "4431",
    "WEC": "4434",
    "MOTOGP": "4430",
    "NASCAR": "4432",
    "INDYCAR": "4433"
}

def fetch_schedules():
    all_races = []
    print("Obteniendo calendarios...")
    for category, league_id in LEAGUE_IDS.items():
        try:
            url = f"{BASE_URL}/eventsseason.php?id={league_id}&s=2026"
            response = requests.get(url).json()
            events = response.get("events", [])
            
            if not events:
                # Fallback a próximos eventos si la temporada completa no está cargada
                url = f"{BASE_URL}/eventsnextleague.php?id={league_id}"
                response = requests.get(url).json()
                events = response.get("events", [])
                
            if events:
                for idx, event in enumerate(events):
                    # Parsear timestamp de la fecha
                    date_str = event.get("strDate", "")
                    time_str = event.get("strTime", "00:00:00")
                    
                    # Convertir a timestamp en milisegundos
                    try:
                        timestamp_seconds = time.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                        timestamp_ms = int(time.mktime(timestamp_seconds) * 1000)
                    except:
                        try:
                            timestamp_seconds = time.strptime(date_str, "%Y-%m-%d")
                            timestamp_ms = int(time.mktime(timestamp_seconds) * 1000)
                        except:
                            timestamp_ms = int(time.time() * 1000) + (idx * 86400000 * 7) # Fallback secuencial
                            
                    race = {
                        "id": f"{category.lower()}-2026-{idx+1}",
                        "category": category,
                        "name": event.get("strEvent", "Gran Premio"),
                        "circuit": event.get("strVenue", "Circuito del Campeonato"),
                        "country": event.get("strCountry", ""),
                        "countryCode": "",
                        "dateTimestamp": timestamp_ms,
                        "round": idx + 1,
                        "season": 2026
                    }
                    all_races.append(race)
                    print(f" -> Cargada carrera: {category} - {race['name']}")
        except Exception as e:
            print(f"Error cargando calendario para {category}: {e}")
            
    return all_races

def fetch_standings():
    all_standings = {}
    print("\nObteniendo clasificaciones...")
    for category, league_id in LEAGUE_IDS.items():
        try:
            url = f"{BASE_URL}/lookuptable.php?l={league_id}&s=2026"
            response = requests.get(url).json()
            table = response.get("table", [])
            
            if not table:
                # Fallback a standings del año anterior si aún no hay datos de 2026
                url = f"{BASE_URL}/lookuptable.php?l={league_id}&s=2025"
                response = requests.get(url).json()
                table = response.get("table", [])
                
            category_standings = []
            if table:
                for idx, entry in enumerate(table[:10]): # Tomar los 10 primeros pilotos
                    standing = {
                        "position": idx + 1,
                        "name": entry.get("strTeam", entry.get("strPlayer", "Piloto Oficial")),
                        "points": float(entry.get("intPoints", "0") or "0"),
                        "wins": int(entry.get("intWin", "0") or "0"),
                        "category": category,
                        "isDriver": True,
                        "teamName": entry.get("strTeam", "Equipo"),
                        "nationality": ""
                    }
                    category_standings.append(standing)
                print(f" -> Cargadas posiciones: {category} ({len(category_standings)} pilotos)")
            else:
                print(f" -> Sin clasificaciones en API para {category}")
                
            all_standings[category] = category_standings
        except Exception as e:
            print(f"Error cargando clasificación para {category}: {e}")
            
    return all_standings

def main():
    races = fetch_schedules()
    if races:
        with open("races.json", "w", encoding="utf-8") as f:
            json.dump(races, f, ensure_ascii=False, indent=2)
        print(" races.json guardado con éxito.")
        
    standings = fetch_standings()
    if standings:
        with open("standings.json", "w", encoding="utf-8") as f:
            json.dump(standings, f, ensure_ascii=False, indent=2)
        print(" standings.json guardado con éxito.")

if __name__ == "__main__":
    main()
