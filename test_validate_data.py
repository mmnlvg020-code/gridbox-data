#!/usr/bin/env python3
"""
test_validate_data.py
Suite de pruebas unitarias aisladas para validate_data.py.
Cubre los 11 escenarios requeridos por la Fase 3A.3a utilizando fixtures locales sin red.
"""

import unittest
import tempfile
import os
import re
import shlex
import subprocess
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from validate_data import (
    validate_races_data,
    validate_standings_data,
    validate_pipeline,
    REQUIRED_CATEGORIES
)

def create_valid_race(category: str, round_num: int, season: int = 2026) -> dict:
    return {
        "id": f"{category.lower()}-{season}-{round_num}",
        "category": category,
        "name": f"Gran Premio de {category} {round_num}",
        "circuit": f"Circuito {category}",
        "country": "Spain",
        "countryCode": "ES",
        "dateTimestamp": 1773583200000, # Marzo 2026 UTC
        "round": round_num,
        "season": season,
        "isCompleted": False,
        "circuitId": "circuit-1"
    }

def create_valid_races_payload(season: int = 2026) -> list:
    payload = []
    for cat in REQUIRED_CATEGORIES:
        for r in range(1, 4):
            payload.append(create_valid_race(cat, r, season))
    return payload

def create_valid_standings_payload() -> dict:
    payload = {}
    for cat in REQUIRED_CATEGORIES:
        payload[cat] = [
            {"position": 1, "name": f"Piloto Uno {cat}", "points": 100.0, "category": cat},
            {"position": 2, "name": f"Piloto Dos {cat}", "points": 80.0, "category": cat},
            {"position": 3, "name": f"Piloto Tres {cat}", "points": 60.0, "category": cat},
            {"position": 4, "name": f"Piloto Cuatro {cat}", "points": 0.0, "category": cat} # Cero legítimo al final
        ]
    return payload

class TestValidateData(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_temp_json(self, filename: str, content: str) -> str:
        path = os.path.join(self.temp_dir.name, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # 1. JSON válido
    def test_01_json_valido(self):
        races = create_valid_races_payload(2026)
        standings = create_valid_standings_payload()

        res_races = validate_races_data(races, expected_season=2026)
        self.assertTrue(res_races.is_valid, f"races.json debe ser válido: {res_races.errors}")

        res_standings = validate_standings_data(standings, expected_season=2026)
        self.assertTrue(res_standings.is_valid, f"standings.json debe ser válido: {res_standings.errors}")

    # 2. JSON mal formado
    def test_02_json_mal_formado(self):
        malformed_path = self.write_temp_json("malformed.json", '{"category": "WRC", unclosed...')
        standings_path = self.write_temp_json("standings.json", json.dumps(create_valid_standings_payload()))

        valid, results = validate_pipeline(malformed_path, standings_path, 2026)
        self.assertFalse(valid)
        self.assertTrue(any("sintaxis JSON" in str(e) for e in results[0].errors))

    # 3. Archivo vacío
    def test_03_archivo_vacio(self):
        empty_races_path = self.write_temp_json("races_empty.json", "")
        empty_standings_path = self.write_temp_json("standings_empty.json", "{}")

        valid, results = validate_pipeline(empty_races_path, empty_standings_path, 2026)
        self.assertFalse(valid)
        self.assertFalse(results[0].is_valid)
        self.assertFalse(results[1].is_valid)

    # 4. Categoría obligatoria ausente
    def test_04_categoria_obligatoria_ausente(self):
        # Falta NASCAR en races y en standings
        races = [r for r in create_valid_races_payload(2026) if r["category"] != "NASCAR"]
        standings = create_valid_standings_payload()
        del standings["NASCAR"]

        res_races = validate_races_data(races, 2026)
        self.assertFalse(res_races.is_valid)
        self.assertTrue(any("NASCAR" in str(e) for e in res_races.errors))

        res_standings = validate_standings_data(standings, 2026)
        self.assertFalse(res_standings.is_valid)
        self.assertTrue(any("NASCAR" in str(e) for e in res_standings.errors))

    # 5. Identificadores duplicados
    def test_05_identificadores_duplicados(self):
        races = create_valid_races_payload(2026)
        # Duplicar el ID de la primera carrera en la segunda
        races[1]["id"] = races[0]["id"]

        res = validate_races_data(races, 2026)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("duplicado" in str(e) for e in res.errors))

    # 6. Temporada incorrecta
    def test_06_temporada_incorrecta(self):
        races = create_valid_races_payload(2026)
        # Modificar temporada de una carrera a 2024
        races[0]["season"] = 2024

        res = validate_races_data(races, expected_season=2026)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("Temporada" in str(e) for e in res.errors))

    # 7. Puntuaciones corruptas (Caso Palou: líder 0.0 pts mientras el 2do tiene puntos)
    def test_07_puntuaciones_corruptas(self):
        standings = create_valid_standings_payload()
        # Corromper IndyCar: líder con 0.0 y segundo con 80.0
        standings["INDYCAR"][0]["points"] = 0.0
        standings["INDYCAR"][1]["points"] = 80.0

        res = validate_standings_data(standings, 2026)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("líder de INDYCAR" in str(e) and "0.0 puntos" in str(e) for e in res.errors))

    # 8. Cero puntos legítimos (ej. pretemporada donde todos tienen 0.0, o pilotos de cola con 0.0)
    def test_08_cero_puntos_legitimos(self):
        standings_preseason = {}
        for cat in REQUIRED_CATEGORIES:
            standings_preseason[cat] = [
                {"position": 1, "name": f"Piloto 1 {cat}", "points": 0.0, "category": cat},
                {"position": 2, "name": f"Piloto 2 {cat}", "points": 0.0, "category": cat},
                {"position": 3, "name": f"Piloto 3 {cat}", "points": 0.0, "category": cat}
            ]
        # Todos en 0.0 debe ser válido (pretemporada legítima)
        res = validate_standings_data(standings_preseason, 2026)
        self.assertTrue(res.is_valid, f"Pretemporada con todos en 0.0 debe ser válida: {res.errors}")

    # 9. Fecha inválida
    def test_09_fecha_invalida(self):
        races = create_valid_races_payload(2026)
        races[0]["dateTimestamp"] = -1000 # Timestamp negativo inválido

        res = validate_races_data(races, 2026)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("Timestamp de fecha inválido" in str(e) for e in res.errors))

    # 10. Archivo parcialmente generado (campos faltantes en elementos)
    def test_10_archivo_parcialmente_generado(self):
        races = create_valid_races_payload(2026)
        # Eliminar 'round' y 'name' en una carrera
        del races[0]["round"]
        del races[0]["name"]

        res = validate_races_data(races, 2026)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("Nombre de carrera ausente" in str(e) or "ronda inválido" in str(e) for e in res.errors))

    # 11. Error en un archivo con el otro válido (atomicidad del validador y del workflow CLI)
    def test_11_error_en_un_archivo_con_el_otro_valido(self):
        import subprocess

        valid_races = create_valid_races_payload(2026)
        corrupted_standings = create_valid_standings_payload()
        del corrupted_standings["WRC"] # Corromper standings omitiendo una categoría obligatoria

        races_path = self.write_temp_json("races.json", json.dumps(valid_races))
        standings_path = self.write_temp_json("standings.json", json.dumps(corrupted_standings))

        # 1. Verificación a nivel de función Python
        overall_valid, results = validate_pipeline(races_path, standings_path, 2026)
        self.assertTrue(results[0].is_valid, "races.json individualmente es válido")
        self.assertFalse(results[1].is_valid, "standings.json individualmente es inválido")
        self.assertFalse(overall_valid, "El pipeline DEBE fallar atómicamente cuando uno de los dos archivos es inválido")

        # 2. Verificación a nivel de invocación CLI (comportamiento idéntico al runner de GitHub Actions)
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate_data.py")
        proc = subprocess.run(
            [sys.executable, script_path, "--races", races_path, "--standings", standings_path, "--season", "2026"],
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 1, "La invocación CLI del validador DEBE terminar con código de salida 1 (error)")
        self.assertIn("PUBLICACIÓN BLOQUEADA POR ERRORES DE INTEGRIDAD", proc.stdout)

    # 12. Empates legítimos en posiciones oficiales (ej. WEC Hypercar) permitidos
    def test_12_empates_oficiales_wec_validos(self):
        standings = create_valid_standings_payload()
        # En WEC Hypercar, múltiples pilotos comparten el mismo coche y posición oficial
        standings["WEC"] = [
            {"position": 1, "name": "Sébastien Buemi", "points": 115.0, "category": "WEC"},
            {"position": 1, "name": "Brendon Hartley", "points": 115.0, "category": "WEC"},
            {"position": 1, "name": "Ryō Hirakawa", "points": 115.0, "category": "WEC"},
            {"position": 2, "name": "Kamui Kobayashi", "points": 97.0, "category": "WEC"},
            {"position": 2, "name": "Mike Conway", "points": 97.0, "category": "WEC"}
        ]

        res = validate_standings_data(standings, 2026)
        self.assertTrue(res.is_valid, f"Empates oficiales con pilotos distintos deben ser válidos: {res.errors}")

    # 13. Participante duplicado dentro de una misma categoría rechazado
    def test_13_participante_duplicado_rechazado(self):
        standings = create_valid_standings_payload()
        # Mismo piloto duplicado en la misma categoría (corrupción de datos)
        standings["WEC"] = [
            {"position": 1, "name": "Sébastien Buemi", "points": 115.0, "category": "WEC"},
            {"position": 2, "name": "Sébastien Buemi", "points": 97.0, "category": "WEC"}
        ]

        res = validate_standings_data(standings, 2026)
        self.assertFalse(res.is_valid, "El validador DEBE rechazar participantes duplicados en la misma categoría")
        self.assertTrue(any("Participante duplicado" in str(e) for e in res.errors))

    # 14. NASCAR 2026 (FV-07): Clasificación correcta y empates de puntos legítimos aprueban
    def test_14_nascar_2026_orden_puntos_correcto_y_empates_validos(self):
        standings = create_valid_standings_payload()
        standings["NASCAR"] = [
            {"position": 1, "name": "Kyle Larson", "points": 2501.0, "category": "NASCAR"},
            {"position": 2, "name": "Christopher Bell", "points": 2452.0, "category": "NASCAR"},
            {"position": 3, "name": "Denny Hamlin", "points": 2445.0, "category": "NASCAR"},
            {"position": 4, "name": "Ryan Blaney", "points": 2407.0, "category": "NASCAR"},
            {"position": 6, "name": "Chase Briscoe", "points": 2386.0, "category": "NASCAR"},
            {"position": 7, "name": "Joey Logano", "points": 2386.0, "category": "NASCAR"},
            {"position": 17, "name": "Shane van Gisbergen", "points": 774.0, "category": "NASCAR"},
            {"position": 40, "name": "Katherine Legge", "points": 8.0, "category": "NASCAR"},
            {"position": 41, "name": "B. J. McLeod", "points": 8.0, "category": "NASCAR"},
        ]
        res = validate_standings_data(standings, expected_season=2026)
        self.assertTrue(res.is_valid, f"Clasificación NASCAR 2026 monótona con empates debe aprobar: {res.errors}")

    # 15. NASCAR 2026 (FV-07): Stage Points mezclados (puntos crecientes en posiciones inferiores) fallan
    def test_15_nascar_2026_stage_points_mezclados_rechazado(self):
        standings = create_valid_standings_payload()
        standings["NASCAR"] = [
            {"position": 1, "name": "Kyle Larson", "points": 304.0, "category": "NASCAR"},
            {"position": 2, "name": "Christopher Bell", "points": 287.0, "category": "NASCAR"},
            {"position": 3, "name": "Denny Hamlin", "points": 295.0, "category": "NASCAR"},
            {"position": 4, "name": "Ryan Blaney", "points": 317.0, "category": "NASCAR"},
            {"position": 16, "name": "Chris Buescher", "points": 118.0, "category": "NASCAR"},
            {"position": 17, "name": "Shane van Gisbergen", "points": 774.0, "category": "NASCAR"},
        ]
        res = validate_standings_data(standings, expected_season=2026)
        self.assertFalse(res.is_valid, "Stage Points mezclados en NASCAR 2026 DEBEN rechazarse")
        self.assertTrue(any("NASCAR" in str(e) and "puntos" in str(e).lower() for e in res.errors))

    # 16. NASCAR 2026 (FV-07): Valores negativos o corruptos fallan
    def test_16_nascar_2026_valores_negativos_o_corruptos_rechazados(self):
        for bad_pts in (-15.0, None, "2501"):
            standings = create_valid_standings_payload()
            standings["NASCAR"] = [
                {"position": 1, "name": "Kyle Larson", "points": 2501.0, "category": "NASCAR"},
                {"position": 2, "name": "Christopher Bell", "points": bad_pts, "category": "NASCAR"},
            ]
            res = validate_standings_data(standings, expected_season=2026)
            self.assertFalse(res.is_valid, f"Valor inválido de puntos ({bad_pts!r}) debe fallar")

    # 17. Alcance acotado (FV-07): La regla específica de NASCAR 2026 no se impone a otras categorías ni temporadas
    def test_17_regla_nascar_2026_acotada_sin_afectar_otras_categorias_ni_temporadas(self):
        # 1. Otra temporada explícita (ej. 2025 con formato eliminatorio Championship 4): no se impone la regla 2026
        standings_2025 = create_valid_standings_payload()
        standings_2025["NASCAR"] = [
            {"position": 1, "name": "Piloto Campeon Final 4", "points": 5040.0, "category": "NASCAR"},
            {"position": 5, "name": "Piloto Eliminado Con Mas Puntos Acumulados", "points": 5045.0, "category": "NASCAR"},
        ]
        res_2025 = validate_standings_data(standings_2025, expected_season=2025)
        self.assertTrue(res_2025.is_valid, f"No debe imponerse la regla NASCAR 2026 a temporada 2025: {res_2025.errors}")

        # 2. Sin expected_season explícito (standings.json carece de campo season propio): no introducir supuestos silenciosos
        res_unspecified = validate_standings_data(standings_2025, expected_season=None)
        self.assertTrue(res_unspecified.is_valid, f"Sin temporada explícita no debe asumirse silenciosamente 2026: {res_unspecified.errors}")

        # 3. Empates oficiales de WEC en 2026 siguen siendo válidos simultáneamente con NASCAR 2026 válido
        standings_wec = create_valid_standings_payload()
        standings_wec["WEC"] = [
            {"position": 1, "name": "Sébastien Buemi", "points": 115.0, "category": "WEC"},
            {"position": 1, "name": "Brendon Hartley", "points": 115.0, "category": "WEC"},
            {"position": 1, "name": "Ryō Hirakawa", "points": 115.0, "category": "WEC"},
        ]
        res_wec = validate_standings_data(standings_wec, expected_season=2026)
        self.assertTrue(res_wec.is_valid, f"Empates oficiales de WEC deben seguir aprobando: {res_wec.errors}")

    @staticmethod
    def _parse_workflow_validation_gate(workflow_text: str):
        """
        Analiza el contenido YAML de sync_data.yml utilizando únicamente la biblioteca estándar
        (compatible con el entorno sin paquetes externos de GitHub Actions):
        - Divide los pasos ('- name: ...') en orden secuencial de ejecución.
        - Localiza el paso de compuerta ('Validate data integrity') y extrae la línea activa
          (no comentada) que invoca validate_data.py junto con sus argumentos CLI.
        - Localiza los índices de todos los pasos que ejecutan comandos activos de publicación
          ('git add', 'git commit' o 'git push') para verificar precedencia estricta.
        """
        step_headers = list(re.finditer(r"^\s*-\s+name:\s*(.+?)\s*$", workflow_text, re.MULTILINE))
        steps = []
        for idx, match in enumerate(step_headers):
            start = match.end()
            end = step_headers[idx + 1].start() if idx + 1 < len(step_headers) else len(workflow_text)
            body = workflow_text[start:end]
            active_lines = [
                ln.strip()
                for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            steps.append((idx, match.group(1), active_lines))

        val_step_idx = None
        val_cmd_line = None
        val_cli_args = None
        publish_step_indices = []

        for idx, step_name, active_lines in steps:
            for ln in active_lines:
                if "validate_data.py" in ln and ("python" in ln):
                    tokens = shlex.split(ln)
                    if len(tokens) >= 2 and tokens[0].startswith("python") and tokens[1].endswith("validate_data.py"):
                        val_step_idx = idx
                        val_cmd_line = ln
                        val_cli_args = tokens[2:]
                if re.match(r"^git\s+(?:add|commit|push)\b", ln):
                    if idx not in publish_step_indices:
                        publish_step_indices.append(idx)

        return val_cmd_line, val_cli_args, val_step_idx, publish_step_indices

    # 18. Integración CLI real reproduciendo los flags efectivos de sync_data.yml (FV-07.2)
    def test_18_integracion_cli_comando_efectivo_workflow(self):
        """
        Extrae los argumentos CLI efectivos configurados en .github/workflows/sync_data.yml
        para validate_data.py y los ejecuta mediante subprocess sobre archivos temporales:
        1. NASCAR 2026 semánticamente corrupto (Stage Points mezclados) -> exit 1.
        2. NASCAR 2026 semánticamente válido (monótono con empates) -> exit 0.
        3. Calendario races.json de temporada distinta de 2026 (2025) -> exit 1.
        """
        base_dir = os.path.dirname(os.path.abspath(__file__))
        workflow_path = os.path.join(base_dir, ".github", "workflows", "sync_data.yml")
        script_path = os.path.join(base_dir, "validate_data.py")

        with open(workflow_path, "r", encoding="utf-8") as wf:
            workflow_text = wf.read()

        val_cmd_line, val_cli_args, _, _ = self._parse_workflow_validation_gate(workflow_text)
        self.assertIsNotNone(val_cli_args, "sync_data.yml debe contener un comando activo python validate_data.py")

        races_2026_path = self.write_temp_json("races_2026.json", json.dumps(create_valid_races_payload(2026)))

        # Caso 1: NASCAR 2026 corrupto (Stage Points mezclados: 304, 287, 295, 317, 118, 774) -> DEBE fallar con exit 1
        standings_corrupt = create_valid_standings_payload()
        standings_corrupt["NASCAR"] = [
            {"position": 1, "name": "Kyle Larson", "points": 304.0, "category": "NASCAR"},
            {"position": 2, "name": "Christopher Bell", "points": 287.0, "category": "NASCAR"},
            {"position": 3, "name": "Denny Hamlin", "points": 295.0, "category": "NASCAR"},
            {"position": 4, "name": "Ryan Blaney", "points": 317.0, "category": "NASCAR"},
            {"position": 16, "name": "Chris Buescher", "points": 118.0, "category": "NASCAR"},
            {"position": 17, "name": "Shane van Gisbergen", "points": 774.0, "category": "NASCAR"},
        ]
        standings_corrupt_path = self.write_temp_json("standings_corrupt.json", json.dumps(standings_corrupt))

        proc_corrupt = subprocess.run(
            [sys.executable, script_path, *val_cli_args, "--races", races_2026_path, "--standings", standings_corrupt_path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc_corrupt.returncode,
            1,
            f"El comando del workflow ('{val_cmd_line}') DEBE rechazar con exit 1 NASCAR 2026 corrupto",
        )
        self.assertIn("Incoherencia semántica de puntos en NASCAR 2026", proc_corrupt.stdout)
        self.assertIn("PUBLICACIÓN BLOQUEADA POR ERRORES DE INTEGRIDAD", proc_corrupt.stdout)

        # Caso 2: NASCAR 2026 correcto (puntos monótonos no crecientes con empate legítimo) -> DEBE aprobar con exit 0
        standings_valid = create_valid_standings_payload()
        standings_valid["NASCAR"] = [
            {"position": 1, "name": "Kyle Larson", "points": 2501.0, "category": "NASCAR"},
            {"position": 2, "name": "Christopher Bell", "points": 2452.0, "category": "NASCAR"},
            {"position": 3, "name": "Denny Hamlin", "points": 2445.0, "category": "NASCAR"},
            {"position": 4, "name": "Ryan Blaney", "points": 2407.0, "category": "NASCAR"},
            {"position": 6, "name": "Chase Briscoe", "points": 2386.0, "category": "NASCAR"},
            {"position": 7, "name": "Joey Logano", "points": 2386.0, "category": "NASCAR"},
            {"position": 41, "name": "B. J. McLeod", "points": 8.0, "category": "NASCAR"},
        ]
        standings_valid_path = self.write_temp_json("standings_valid.json", json.dumps(standings_valid))

        proc_valid = subprocess.run(
            [sys.executable, script_path, *val_cli_args, "--races", races_2026_path, "--standings", standings_valid_path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_valid.returncode, 0, f"Lote NASCAR 2026 válido debe salir con 0: {proc_valid.stdout}")
        self.assertIn("TODOS LOS ARCHIVOS CUMPLEN LAS REGLAS DE PUBLICACIÓN", proc_valid.stdout)

        # Caso 3: Calendario de temporada distinta de 2026 (2025) -> DEBE bloquearse con exit 1
        races_2025_path = self.write_temp_json("races_2025.json", json.dumps(create_valid_races_payload(2025)))
        proc_wrong_season = subprocess.run(
            [sys.executable, script_path, *val_cli_args, "--races", races_2025_path, "--standings", standings_valid_path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc_wrong_season.returncode,
            1,
            f"El comando del workflow ('{val_cmd_line}') DEBE rechazar calendarios de temporada != 2026",
        )
        self.assertIn("Temporada (2025) no coincide con la esperada (2026)", proc_wrong_season.stdout)
        self.assertIn("PUBLICACIÓN BLOQUEADA POR ERRORES DE INTEGRIDAD", proc_wrong_season.stdout)

    # 19. Guardia contractual de regresión sobre .github/workflows/sync_data.yml (FV-07.2)
    def test_19_guardia_regresion_workflow_exige_season_2026_antes_de_git_push(self):
        """
        Detecta inmediatamente si alguien elimina '--season 2026' del paso de validación
        en .github/workflows/sync_data.yml o reordena la validación después de git add/commit/push.
        """
        base_dir = os.path.dirname(os.path.abspath(__file__))
        workflow_path = os.path.join(base_dir, ".github", "workflows", "sync_data.yml")
        with open(workflow_path, "r", encoding="utf-8") as wf:
            workflow_text = wf.read()

        val_cmd_line, val_cli_args, val_step_idx, publish_step_indices = self._parse_workflow_validation_gate(
            workflow_text
        )
        self.assertIsNotNone(val_cmd_line, "Debe existir el comando python validate_data.py en sync_data.yml")
        self.assertEqual(
            val_cli_args,
            ["--season", "2026"],
            f"El paso de validación del workflow debe pasar explícitamente '--season 2026' (actual: '{val_cmd_line}')",
        )
        self.assertTrue(publish_step_indices, "Debe identificarse el paso de git add/commit/push en sync_data.yml")
        for pub_idx in publish_step_indices:
            self.assertLess(
                val_step_idx,
                pub_idx,
                "El paso 'Validate data integrity (blocking gate)' DEBE ejecutarse antes de git add/commit/push",
            )

        # Verificar que el detector captura una regresión hipotética donde se elimine '--season 2026'
        regressed_yaml = workflow_text.replace("python validate_data.py --season 2026", "python validate_data.py")
        _, regressed_args, _, _ = self._parse_workflow_validation_gate(regressed_yaml)
        self.assertNotEqual(
            regressed_args,
            ["--season", "2026"],
            "El detector debe distinguir cuando '--season 2026' es eliminado del workflow",
        )

    # 20. Guardia contractual BACK-03B sobre sync_data.yml (Decisiones 1A y 2A)
    def test_20_workflow_contract_both_jsons_and_verified_telegram_gate(self):
        """
        Verifica en .github/workflows/sync_data.yml:
        1. 'git add races.json standings.json' incluye explícitamente ambos JSON en el mismo commit
           (y prohíbe 'git add .' o 'git add -A').
        2. Existe concurrency group para evitar carreras de git push.
        3. Existe un único paso final de notificación con 'if: always()' situado DESPUÉS de git push,
           que inyecta TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID exclusivamente en ese paso.
        """
        base_dir = os.path.dirname(os.path.abspath(__file__))
        workflow_path = os.path.join(base_dir, ".github", "workflows", "sync_data.yml")
        with open(workflow_path, "r", encoding="utf-8") as wf:
            workflow_text = wf.read()

        self.assertIn("concurrency:", workflow_text, "Debe declarar concurrency para evitar carreras de push")
        self.assertRegex(
            workflow_text,
            r"(?m)^\s*git\s+add\s+races\.json\s+standings\.json\s*$",
            "El workflow debe preparar conjuntamente 'git add races.json standings.json'",
        )
        self.assertNotRegex(
            workflow_text,
            r"(?m)^\s*git\s+add\s+(?:\.|-A|--all)\b",
            "Prohibido 'git add .' o 'git add -A' (no deben subirse temporales ni archivos ajenos)",
        )

        # Verificar precedencia: validate -> publish -> notify (if: always())
        step_headers = list(re.finditer(r"^\s*-\s+name:\s*(.+?)\s*$", workflow_text, re.MULTILINE))
        publish_idx = None
        notify_indices = []
        for idx, match in enumerate(step_headers):
            start = match.end()
            end = step_headers[idx + 1].start() if idx + 1 < len(step_headers) else len(workflow_text)
            body = workflow_text[start:end]
            if re.search(r"(?m)^\s*git\s+push\b", body):
                publish_idx = idx
            if "--notify" in body or "TELEGRAM_BOT_TOKEN" in body:
                notify_indices.append((idx, body))

        self.assertIsNotNone(publish_idx, "Debe existir el paso con git push")
        self.assertEqual(len(notify_indices), 1, "Debe existir exactamente 1 paso con credenciales/envío Telegram")
        notify_idx, notify_body = notify_indices[0]
        self.assertGreater(notify_idx, publish_idx, "La notificación Telegram debe ejecutarse DESPUÉS de git push")
        self.assertIn("if: always()", notify_body, "El paso final de notificación debe declarar 'if: always()'")
        self.assertIn("secrets.TELEGRAM_BOT_TOKEN", notify_body)
        self.assertIn("secrets.TELEGRAM_CHAT_ID", notify_body)

    # 21. Simulación Git real en repositorios locales temporales (BACK-03B T9: commit, no-op, fallo commit y fallo push)
    def test_21_local_git_atomic_commit_noop_and_commit_push_failures(self):
        """
        Prueba extremo a extremo sobre repositorios Git locales temporales (100 % offline):
        1. Cambio válido en races.json y standings.json -> 1 único commit remoto con ambos archivos -> PUBLISHED.
        2. Segunda ejecución sin cambios -> git diff --staged --quiet es 0 -> 0 commits nuevos -> NO_CHANGES (silencio).
        3. Fallo de git commit -> remoto intacto -> estado resuelto FAILED (nunca PUBLISHED).
        4. Fallo de git push (rechazo en remoto) -> remoto intacto en el commit previo -> estado FAILED (nunca PUBLISHED).
        """
        import update_data

        resolve_fn = getattr(update_data, "resolve_ci_notification_state", None)
        self.assertIsNotNone(resolve_fn, "update_data debe exponer resolve_ci_notification_state")

        def run_git(args, cwd, check=True):
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            if check and proc.returncode != 0:
                raise RuntimeError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr}")
            return proc

        remote_dir = os.path.join(self.temp_dir.name, "remote.git")
        work_dir = os.path.join(self.temp_dir.name, "work")
        os.makedirs(remote_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)

        run_git(["init", "--bare", "-b", "main"], cwd=remote_dir)
        run_git(["init", "-b", "main"], cwd=work_dir)
        run_git(["config", "user.name", "GridBox Test Bot"], cwd=work_dir)
        run_git(["config", "user.email", "bot@example.invalid"], cwd=work_dir)
        run_git(["remote", "add", "origin", remote_dir], cwd=work_dir)

        def push_if_shell_permitted():
            """
            Ejecuta 'git push' real contra remote_dir. En Linux/GitHub Actions actualiza el bare repo;
            en el sandbox restringido de Windows donde MSYS2 sh.exe bloquea NtCreateDirectoryObject
            (0xC0000022), detecta esa restricción específica del entorno sin enmascarar otros fallos.
            """
            p = run_git(["push", "-u", "origin", "main"], cwd=work_dir, check=False)
            if p.returncode == 0:
                return True
            if "NtCreateDirectoryObject" in p.stderr or "0xC0000022" in p.stderr:
                return False
            raise RuntimeError(f"git push falló inesperadamente ({p.returncode}): {p.stderr}")

        # Commit semilla inicial
        races_v1 = create_valid_races_payload(2026)
        standings_v1 = create_valid_standings_payload()
        with open(os.path.join(work_dir, "races.json"), "w", encoding="utf-8") as fh:
            json.dump(races_v1, fh, indent=2)
        with open(os.path.join(work_dir, "standings.json"), "w", encoding="utf-8") as fh:
            json.dump(standings_v1, fh, indent=2)
        # Crear archivo .tmp ajeno para comprobar que 'git add races.json standings.json' no lo sube
        with open(os.path.join(work_dir, ".orphan.tmp"), "w", encoding="utf-8") as fh:
            fh.write("temp")

        run_git(["add", "races.json", "standings.json"], cwd=work_dir)
        run_git(["commit", "-m", "Initial seed"], cwd=work_dir)
        seed_local_sha = run_git(["rev-parse", "HEAD"], cwd=work_dir).stdout.strip()
        bare_push_supported = push_if_shell_permitted()
        if bare_push_supported:
            self.assertEqual(run_git(["rev-parse", "HEAD"], cwd=remote_dir).stdout.strip(), seed_local_sha)

        # Caso A: Actualización conjunta válida -> 1 solo commit con ambos archivos -> PUBLISHED
        races_v2 = create_valid_races_payload(2026)
        races_v2[0]["name"] = "Gran Premio Actualizado WRC"
        standings_v2 = create_valid_standings_payload()
        standings_v2["WRC"][0]["points"] = 243.0
        with open(os.path.join(work_dir, "races.json"), "w", encoding="utf-8") as fh:
            json.dump(races_v2, fh, indent=2)
        with open(os.path.join(work_dir, "standings.json"), "w", encoding="utf-8") as fh:
            json.dump(standings_v2, fh, indent=2)

        overall_ok, _ = validate_pipeline(
            os.path.join(work_dir, "races.json"),
            os.path.join(work_dir, "standings.json"),
            2026,
        )
        self.assertTrue(overall_ok)

        run_git(["add", "races.json", "standings.json"], cwd=work_dir)
        diff_proc = run_git(["diff", "--staged", "--quiet"], cwd=work_dir, check=False)
        self.assertEqual(diff_proc.returncode, 1, "Debe detectar cambios preparados en ambos JSON")
        run_git(["commit", "-m", "Auto-update races.json and standings.json [validated]"], cwd=work_dir)

        new_commit_sha = run_git(["rev-parse", "HEAD"], cwd=work_dir).stdout.strip()
        self.assertNotEqual(new_commit_sha, seed_local_sha)
        changed_files = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", new_commit_sha], cwd=work_dir).stdout.splitlines()
        self.assertEqual(
            sorted(changed_files),
            ["races.json", "standings.json"],
            "Ambos JSON deben viajar juntos en un único commit sin incluir archivos .tmp",
        )
        if bare_push_supported:
            self.assertTrue(push_if_shell_permitted())
            self.assertEqual(run_git(["rev-parse", "HEAD"], cwd=remote_dir).stdout.strip(), new_commit_sha)

        outcome_pub, failed_pub = resolve_fn({
            "JOB_STATUS": "success",
            "STEP_PUBLISH": "success",
            "PUBLISH_RESULT": "PUBLISHED",
        })
        self.assertEqual((outcome_pub, failed_pub), ("PUBLISHED", ""))

        # Caso B: Ejecución subsiguiente sin cambios -> diff --staged --quiet == 0 -> NO_CHANGES
        run_git(["add", "races.json", "standings.json"], cwd=work_dir)
        noop_diff = run_git(["diff", "--staged", "--quiet"], cwd=work_dir, check=False)
        self.assertEqual(noop_diff.returncode, 0, "Sin cambios no debe generar commit")
        self.assertEqual(run_git(["rev-parse", "HEAD"], cwd=work_dir).stdout.strip(), new_commit_sha)

        outcome_noop, failed_noop = resolve_fn({
            "JOB_STATUS": "success",
            "STEP_PUBLISH": "success",
            "PUBLISH_RESULT": "NO_CHANGES",
        })
        self.assertEqual((outcome_noop, failed_noop), ("NO_CHANGES", ""))

        # Caso C: Fallo simulado de git commit (mensaje vacío rechazado por Git) -> HEAD intacto -> FAILED
        standings_v2["WRC"][0]["points"] = 250.0
        with open(os.path.join(work_dir, "standings.json"), "w", encoding="utf-8") as fh:
            json.dump(standings_v2, fh, indent=2)
        run_git(["add", "races.json", "standings.json"], cwd=work_dir)
        bad_commit = run_git(["commit", "-m", ""], cwd=work_dir, check=False)
        self.assertNotEqual(bad_commit.returncode, 0, "git commit fallido debe retornar código != 0")
        self.assertEqual(run_git(["rev-parse", "HEAD"], cwd=work_dir).stdout.strip(), new_commit_sha)

        outcome_c_fail, step_c_fail = resolve_fn({
            "JOB_STATUS": "failure",
            "STEP_UPDATE": "success",
            "STEP_TESTS": "success",
            "STEP_VALIDATE": "success",
            "STEP_PUBLISH": "failure",
            "PUBLISH_RESULT": "FAILED",
        })
        self.assertEqual(outcome_c_fail, "FAILED")
        self.assertIn("publish", step_c_fail)

        # Caso D: Commit local exitoso pero fallo de git push -> código != 0 y estado FAILED (nunca PUBLISHED)
        run_git(["commit", "-m", "Commit local previo a fallo de push"], cwd=work_dir)
        bad_push = run_git(["push", "nonexistent_remote_url_dir", "main"], cwd=work_dir, check=False)
        self.assertNotEqual(bad_push.returncode, 0, "git push fallido debe retornar código != 0")
        if bare_push_supported:
            self.assertEqual(
                run_git(["rev-parse", "HEAD"], cwd=remote_dir).stdout.strip(),
                new_commit_sha,
                "Ante fallo de git push, el repositorio remoto debe conservar intacto el commit anterior",
            )
        outcome_p_fail, step_p_fail = resolve_fn({
            "JOB_STATUS": "failure",
            "STEP_UPDATE": "success",
            "STEP_TESTS": "success",
            "STEP_VALIDATE": "success",
            "STEP_PUBLISH": "failure",
            "PUBLISH_RESULT": "FAILED",
        })
        self.assertEqual(outcome_p_fail, "FAILED")
        self.assertIn("publish", step_p_fail)

    # 22. Guardia contractual PREDEPLOY-01: ejecución obligatoria de ambas suites en sync_data.yml
    def test_22_workflow_runs_both_validator_and_extractor_suites_before_publish(self):
        """
        Verifica en .github/workflows/sync_data.yml:
        1. El paso 'Run validation test suite' (id: unit_tests) ejecuta tanto
           'python test_validate_data.py' como 'python test_extractor.py'.
        2. Ambas suites se ejecutan antes de 'validate' y antes de 'publish' (git add/commit/push).
        3. Los archivos test_validate_data.py, test_extractor.py y fixtures/*.html existen en las
           rutas relativas esperadas por la raíz del repositorio satélite.
        """
        base_dir = os.path.dirname(os.path.abspath(__file__))
        workflow_path = os.path.join(base_dir, ".github", "workflows", "sync_data.yml")
        with open(workflow_path, "r", encoding="utf-8") as wf:
            workflow_text = wf.read()

        step_headers = list(re.finditer(r"^\s*-\s+name:\s*(.+?)\s*$", workflow_text, re.MULTILINE))
        unit_step_idx = None
        unit_active_lines = []
        validate_step_idx = None
        publish_step_idx = None

        for idx, match in enumerate(step_headers):
            start = match.end()
            end = step_headers[idx + 1].start() if idx + 1 < len(step_headers) else len(workflow_text)
            body = workflow_text[start:end]
            active_lines = [
                ln.strip()
                for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if match.group(1) == "Run validation test suite":
                unit_step_idx = idx
                unit_active_lines = active_lines
            if any("validate_data.py" in ln and "--season" in ln for ln in active_lines):
                validate_step_idx = idx
            if any(re.match(r"^git\s+(?:add|commit|push)\b", ln) for ln in active_lines):
                publish_step_idx = idx

        self.assertIsNotNone(unit_step_idx, "Debe existir el paso 'Run validation test suite'")
        self.assertIn("id: unit_tests", unit_active_lines)
        self.assertIn("python test_validate_data.py", unit_active_lines)
        self.assertIn("python test_extractor.py", unit_active_lines)
        self.assertLess(
            unit_active_lines.index("python test_validate_data.py"),
            unit_active_lines.index("python test_extractor.py"),
        )
        self.assertIsNotNone(validate_step_idx)
        self.assertIsNotNone(publish_step_idx)
        self.assertLess(unit_step_idx, validate_step_idx, "Las suites unitarias deben preceder a validate")
        self.assertLess(validate_step_idx, publish_step_idx, "La validación debe preceder a publish")

        # Verificar existencia física en las rutas esperadas del repositorio satélite
        self.assertTrue(os.path.isfile(os.path.join(base_dir, "test_validate_data.py")))
        self.assertTrue(os.path.isfile(os.path.join(base_dir, "test_extractor.py")))
        fixtures_dir = os.path.join(base_dir, "fixtures")
        self.assertTrue(os.path.isdir(fixtures_dir))
        html_fixtures = [f for f in os.listdir(fixtures_dir) if f.endswith(".html")]
        self.assertGreater(len(html_fixtures), 0, "El directorio fixtures/ debe contener archivos .html")

if __name__ == "__main__":
    unittest.main()
