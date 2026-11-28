#!/usr/bin/env python3
"""
validate_data.py
Validador de integridad para los archivos de datos de GridBox (races.json y standings.json).
Verifica sintaxis, esquemas, completitud de categorías, consistencia temporal y calidad de datos.
"""

import sys
import os
import json
import argparse
import datetime
from typing import Dict, List, Any, Optional, Tuple

REQUIRED_CATEGORIES = ["WRC", "WEC", "MOTOGP", "NASCAR", "INDYCAR"]

class ValidationError:
    def __init__(self, category: str, field: str, message: str, is_critical: bool = True):
        self.category = category
        self.field = field
        self.message = message
        self.is_critical = is_critical

    def __str__(self):
        prefix = "CRÍTICO" if self.is_critical else "ADVERTENCIA"
        cat_str = f"[{self.category}] " if self.category else ""
        field_str = f"({self.field}): " if self.field else ": "
        return f"[{prefix}] {cat_str}{field_str}{self.message}"

class ValidationResult:
    def __init__(self, file_name: str):
        self.file_name = file_name
        self.errors: List[ValidationError] = []
        self.stats: Dict[str, Any] = {}

    @property
    def is_valid(self) -> bool:
        return not any(e.is_critical for e in self.errors)

    def add_error(self, category: str, field: str, message: str, is_critical: bool = True):
        self.errors.append(ValidationError(category, field, message, is_critical))

def validate_races_data(data: Any, expected_season: Optional[int] = None) -> ValidationResult:
    res = ValidationResult("races.json")

    if not isinstance(data, list):
        res.add_error("", "root", "La raíz del archivo races.json debe ser una lista JSON (array).")
        return res

    if len(data) == 0:
        res.add_error("", "root", "El archivo races.json está vacío (0 carreras).")
        return res

    res.stats["total_races"] = len(data)
    seen_ids = set()
    categories_present = set()
    races_by_category: Dict[str, List[dict]] = {cat: [] for cat in REQUIRED_CATEGORIES}

    for i, race in enumerate(data):
        if not isinstance(race, dict):
            res.add_error("", f"item_{i}", f"El elemento en el índice {i} no es un objeto JSON.")
            continue

        cat = race.get("category", "")
        race_id = race.get("id", "")
        name = race.get("name", "")
        circuit = race.get("circuit", "")
        date_ts = race.get("dateTimestamp")
        round_num = race.get("round")
        season = race.get("season")

        if cat not in REQUIRED_CATEGORIES:
            res.add_error(str(cat), f"race_{i}", f"Categoría desconocida o ausente: '{cat}'.")
        else:
            categories_present.add(cat)
            races_by_category[cat].append(race)

        # Validación de campos obligatorios
        if not race_id or not isinstance(race_id, str):
            res.add_error(cat, f"item_{i}.id", "Identificador de carrera ausente o no es string.")
        elif race_id in seen_ids:
            res.add_error(cat, f"id_{race_id}", f"Identificador de carrera duplicado: '{race_id}'.")
        else:
            seen_ids.add(race_id)

        if not name or not isinstance(name, str):
            res.add_error(cat, f"{race_id}.name", "Nombre de carrera ausente o vacío.")
        elif "<" in name or ">" in name:
            res.add_error(cat, f"{race_id}.name", f"Nombre de carrera contiene etiquetas HTML: '{name}'.")

        # Validación de timestamp
        if date_ts is None or not isinstance(date_ts, (int, float)) or date_ts <= 0:
            res.add_error(cat, f"{race_id}.dateTimestamp", f"Timestamp de fecha inválido: {date_ts}.")
        else:
            try:
                dt_utc = datetime.datetime.fromtimestamp(date_ts / 1000.0, datetime.timezone.utc)
                # Coherencia básica de año/temporada
                if expected_season is not None:
                    # Permitir margen de eventos de frontera (noviembre/diciembre del año anterior o enero del posterior)
                    if abs(dt_utc.year - expected_season) > 1:
                        res.add_error(cat, f"{race_id}.dateTimestamp",
                                      f"Año de fecha ({dt_utc.year}) difiere significativamente de la temporada esperada ({expected_season}).")
            except Exception as e:
                res.add_error(cat, f"{race_id}.dateTimestamp", f"Error convirtiendo timestamp a fecha UTC: {e}.")

        # Validación de ronda
        if round_num is None or not isinstance(round_num, int) or round_num <= 0:
            res.add_error(cat, f"{race_id}.round", f"Número de ronda inválido: {round_num}.")

        # Validación de temporada
        if season is None or not isinstance(season, int) or season < 2000:
            res.add_error(cat, f"{race_id}.season", f"Temporada inválida: {season}.")
        elif expected_season is not None and season != expected_season:
            res.add_error(cat, f"{race_id}.season", f"Temporada ({season}) no coincide con la esperada ({expected_season}).")

    # Comprobación de categorías obligatorias completas
    missing_cats = set(REQUIRED_CATEGORIES) - categories_present
    if missing_cats:
        for mc in sorted(list(missing_cats)):
            res.add_error(mc, "category", f"Categoría obligatoria ausente por completo en races.json: {mc}.")

    # Comprobación de orden y numeración por categoría
    for cat, r_list in races_by_category.items():
        res.stats[f"races_{cat.lower()}"] = len(r_list)
        if len(r_list) > 0:
            rounds = [r.get("round") for r in r_list if isinstance(r.get("round"), int)]
            if len(rounds) != len(set(rounds)):
                res.add_error(cat, "round", "Existen rondas duplicadas dentro de la categoría.")

    return res

def validate_standings_data(data: Any, expected_season: Optional[int] = None) -> ValidationResult:
    res = ValidationResult("standings.json")

    if not isinstance(data, dict):
        res.add_error("", "root", "La raíz del archivo standings.json debe ser un objeto JSON (dict).")
        return res

    if len(data) == 0:
        res.add_error("", "root", "El archivo standings.json está vacío (0 categorías).")
        return res

    missing_cats = set(REQUIRED_CATEGORIES) - set(data.keys())
    if missing_cats:
        for mc in sorted(list(missing_cats)):
            res.add_error(mc, "category", f"Categoría obligatoria ausente en standings.json: {mc}.")

    for cat in REQUIRED_CATEGORIES:
        if cat not in data:
            continue

        entries = data[cat]
        if not isinstance(entries, list):
            res.add_error(cat, "entries", f"Las entradas de {cat} deben ser una lista.")
            continue

        res.stats[f"standings_{cat.lower()}"] = len(entries)

        if len(entries) == 0:
            res.add_error(cat, "entries", f"Lista de clasificación vacía para {cat}.")
            continue

        seen_positions = set()
        seen_names = set()
        # Seguimiento acotado exclusivamente a NASCAR Cup 2026: en el formato Chase 2026 (reseeding inicial
        # sin rondas eliminatorias Championship 4), la clasificación oficial de pilotos mantiene posiciones
        # ordenadas y puntos totales de campeonato monótonos no crecientes (permitiendo empates legítimos).
        # Nota contractual: standings.json no almacena un campo interno "season", por lo que esta regla
        # únicamente se activa cuando el invocador proporciona explícitamente expected_season == 2026.
        enforce_nascar_2026_monotonic = (cat == "NASCAR" and expected_season == 2026)
        prev_nascar_pos = None
        prev_nascar_points = None
        prev_nascar_name = None

        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                res.add_error(cat, f"entry_{idx}", f"Entrada en índice {idx} no es un objeto JSON.")
                continue

            pos = entry.get("position")
            name = entry.get("name", "")
            pts = entry.get("points")
            entry_cat = entry.get("category", "")

            # Validación de categoría interna
            if entry_cat != cat:
                res.add_error(cat, f"entry_{idx}.category", f"Categoría del elemento ('{entry_cat}') no coincide con clave ('{cat}').")

            # Validación de posición
            pos_is_valid = pos is not None and isinstance(pos, int) and not isinstance(pos, bool) and pos > 0
            if not pos_is_valid:
                res.add_error(cat, f"entry_{idx}.position", f"Posición inválida: {pos}.")
            else:
                # Se permiten empates oficiales (múltiples pilotos compartiendo posición, ej. WEC Hypercar)
                seen_positions.add(pos)

            # Validación de nombre y unicidad de participante
            if not name or not isinstance(name, str):
                res.add_error(cat, f"entry_{idx}.name", "Nombre de piloto ausente o vacío.")
            else:
                norm_name = name.strip().lower()
                if norm_name in seen_names:
                    res.add_error(cat, f"entry_{idx}.name", f"Participante duplicado en {cat}: '{name}'.")
                else:
                    seen_names.add(norm_name)

                if "<" in name or ">" in name or "{" in name or "}" in name or ".mw-parser" in name:
                    res.add_error(cat, f"entry_{idx}.name", f"Nombre contiene HTML/CSS o artefactos de Wikipedia: '{name}'.")
                if name.startswith("#") and " " in name and ("racing" in name.lower() or "team" in name.lower() or "global" in name.lower()):
                    res.add_error(cat, f"entry_{idx}.name", f"Nombre parece ser un equipo/coche y no un piloto: '{name}'.")

            # Validación de puntos
            pts_is_valid = pts is not None and isinstance(pts, (int, float)) and not isinstance(pts, bool)
            if not pts_is_valid:
                res.add_error(cat, f"entry_{idx}.points", f"Puntos no numéricos o ausentes: {pts}.")
            elif pts < 0:
                pts_is_valid = False
                res.add_error(cat, f"entry_{idx}.points", f"Puntos negativos no permitidos: {pts}.")

            # Validación semántica acotada a NASCAR Cup 2026:
            # Verifica orden coherente de posiciones y monotonía no creciente de puntos totales de campeonato,
            # detectando desplazamientos de columnas hacia Stage Points (ej. P4 con 317 > P1 con 304 o P17 con 774 > P16 con 118).
            if enforce_nascar_2026_monotonic and pos_is_valid and pts_is_valid:
                if prev_nascar_pos is not None and pos < prev_nascar_pos:
                    res.add_error(
                        cat,
                        f"entry_{idx}.position",
                        f"Orden de posiciones incoherente en NASCAR 2026: '{name}' figura en posición {pos} después de '{prev_nascar_name}' en posición {prev_nascar_pos}.",
                    )
                if prev_nascar_points is not None and pts > prev_nascar_points:
                    res.add_error(
                        cat,
                        f"entry_{idx}.points",
                        f"Incoherencia semántica de puntos en NASCAR 2026: '{name}' (pos {pos}, {pts} pts) supera en puntos a '{prev_nascar_name}' (pos {prev_nascar_pos}, {prev_nascar_points} pts). Posible lectura de Stage Points o desplazamiento de columnas.",
                    )
                prev_nascar_pos = pos
                prev_nascar_points = pts
                prev_nascar_name = name

        # Heurística de integridad de datos (no certificación de exactitud deportiva):
        # Detecta conversiones numéricas fallidas en el extractor que caen silenciosamente en el bloque
        # 'except: pts = 0.0' (anomalía detectada en Álex Palou por notas al pie complejas de Wikipedia).
        # Si la posición 1 tiene 0.0 puntos pero existen pilotos posteriores con puntuación positiva en el
        # mismo campeonato, se clasifica como corrupción de datos y se rechaza.
        if len(entries) >= 2:
            p1_pts = entries[0].get("points", 0.0)
            if isinstance(p1_pts, (int, float)) and p1_pts == 0.0:
                has_positive_later = any(
                    isinstance(e.get("points"), (int, float)) and e.get("points", 0.0) > 0.0
                    for e in entries[1:]
                )
                if has_positive_later:
                    res.add_error(cat, "points",
                                  f"Heurística de integridad: El líder de {cat} (posición 1, {entries[0].get('name')}) figura con 0.0 puntos mientras otros pilotos tienen puntuación positiva. Conversión corrupta rechazada.")

    return res

def validate_pipeline(races_path: str, standings_path: str, expected_season: Optional[int] = None) -> Tuple[bool, List[ValidationResult]]:
    results = []

    # 1. Validar races.json
    races_res = ValidationResult("races.json")
    if not os.path.exists(races_path):
        races_res.add_error("", "file", f"El archivo no existe: {races_path}")
    else:
        try:
            with open(races_path, "r", encoding="utf-8") as f:
                races_data = json.load(f)
            races_res = validate_races_data(races_data, expected_season)
        except json.JSONDecodeError as e:
            races_res.add_error("", "syntax", f"Error de sintaxis JSON en {races_path}: {e}")
        except Exception as e:
            races_res.add_error("", "read", f"Error leyendo {races_path}: {e}")
    results.append(races_res)

    # 2. Validar standings.json
    standings_res = ValidationResult("standings.json")
    if not os.path.exists(standings_path):
        standings_res.add_error("", "file", f"El archivo no existe: {standings_path}")
    else:
        try:
            with open(standings_path, "r", encoding="utf-8") as f:
                standings_data = json.load(f)
            standings_res = validate_standings_data(standings_data, expected_season)
        except json.JSONDecodeError as e:
            standings_res.add_error("", "syntax", f"Error de sintaxis JSON en {standings_path}: {e}")
        except Exception as e:
            standings_res.add_error("", "read", f"Error leyendo {standings_path}: {e}")
    results.append(standings_res)

    overall_valid = all(r.is_valid for r in results)
    return overall_valid, results

def print_report(results: List[ValidationResult]):
    print("=" * 70)
    print(" REPORTE DE VALIDACIÓN DE DATOS GRIDBOX")
    print("=" * 70)

    for r in results:
        status_str = "VÁLIDO (APROBADO)" if r.is_valid else "INVÁLIDO (RECHAZADO)"
        print(f"\nArchivo: {r.file_name} -> {status_str}")
        if r.stats:
            print("  Estadísticas:")
            for k, v in r.stats.items():
                print(f"    - {k}: {v}")
        if r.errors:
            print(f"  Defectos detectados ({len(r.errors)}):")
            for err in r.errors:
                print(f"    {err}")
        else:
            print("  Sin errores.")

    print("\n" + "=" * 70)
    overall = all(r.is_valid for r in results)
    if overall:
        print(" RESULTADO FINAL: TODOS LOS ARCHIVOS CUMPLEN LAS REGLAS DE PUBLICACIÓN.")
    else:
        print(" RESULTADO FINAL: PUBLICACIÓN BLOQUEADA POR ERRORES DE INTEGRIDAD.")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Validador de datos de GridBox (races.json y standings.json)")
    parser.add_argument("--races", default=None, help="Ruta al archivo races.json")
    parser.add_argument("--standings", default=None, help="Ruta al archivo standings.json")
    parser.add_argument("--season", type=int, default=None, help="Temporada esperada (ej. 2026)")
    parser.add_argument("--races-only", action="store_true", help="Validar únicamente races.json")
    parser.add_argument("--standings-only", action="store_true", help="Validar únicamente standings.json")

    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    races_file = args.races or os.path.join(script_dir, "races.json")
    standings_file = args.standings or os.path.join(script_dir, "standings.json")

    if args.races_only:
        res = ValidationResult("races.json")
        try:
            with open(races_file, "r", encoding="utf-8") as f:
                res = validate_races_data(json.load(f), args.season)
        except Exception as e:
            res.add_error("", "file", str(e))
        print_report([res])
        sys.exit(0 if res.is_valid else 1)

    if args.standings_only:
        res = ValidationResult("standings.json")
        try:
            with open(standings_file, "r", encoding="utf-8") as f:
                res = validate_standings_data(json.load(f), args.season)
        except Exception as e:
            res.add_error("", "file", str(e))
        print_report([res])
        sys.exit(0 if res.is_valid else 1)

    overall_valid, results = validate_pipeline(races_file, standings_file, args.season)
    print_report(results)
    sys.exit(0 if overall_valid else 1)

if __name__ == "__main__":
    main()
