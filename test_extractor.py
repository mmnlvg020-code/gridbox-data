#!/usr/bin/env python3
"""
test_extractor.py
Suite de pruebas unitarias y fixtures offline para GridBox (update_data.py).
Fase 3A.3b.3 (Cierre técnico) — Verificación de selectores por categoría,
verificación positiva de WEC Hypercar, preservación de empates oficiales,
rechazo de posiciones corruptas y detección de estructuras complejas (rowspan/colspan).
Ejecución 100% offline.
"""

import hashlib
import io
import json
import socket
import tempfile
import unittest
import os
import sys
import urllib.error
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from update_data import (
    clean_gp_name,
    parse_points_cell,
    has_complex_table_structure,
    parse_table_grid,
    is_ineligible_driver,
    fetch_wikipedia_standings,
    WIKI_STANDINGS
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

def load_fixture(filename: str) -> str:
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

class TestExtractorCategorySelectors(unittest.TestCase):
    """
    Verifica selectores de tablas por categoría, resolución de columnas,
    manejo de empates y prevención de fallbacks.
    """

    # -------------------------------------------------------------------------
    # 1. NASCAR: Selección de tabla de pilotos sin clase 'wikitable'
    # -------------------------------------------------------------------------
    def test_01_nascar_table_without_wikitable_is_selected(self):
        """
        NASCAR Cup utiliza tablas con clase 'sortable' en lugar de 'wikitable'.
        fetch_wikipedia_standings debe encontrar la tabla basada en encabezados
        (Pos, Driver, Pts) y extraer los pilotos correctamente.
        """
        fixture_html = load_fixture("nascar_standings_nowikitable.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar.local"])

            self.assertEqual(len(results), 3, "Debe extraer los 3 pilotos de NASCAR")
            self.assertEqual(results[0]["name"], "William Byron")
            self.assertEqual(results[0]["points"], 2050.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Kyle Larson")
            self.assertEqual(results[2]["name"], "Christopher Bell")

    # -------------------------------------------------------------------------
    # 2. IndyCar: Puntos de Álex Palou con anotaciones HTML
    # -------------------------------------------------------------------------
    def test_02_indycar_palou_footnote_preserves_points(self):
        """
        La celda de puntos de Álex Palou contiene una anotación al pie (631<sup>a</sup>).
        parse_points_cell() y la resolución de columnas extraen exactamente 631.0 puntos.
        """
        fixture_html = load_fixture("indycar_palou_footnote.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("INDYCAR", ["http://dummy-indycar.local"])

            self.assertGreater(len(results), 0, "Debe procesar la tabla de IndyCar")
            palou = next((r for r in results if "Palou" in r["name"]), None)
            self.assertIsNotNone(palou, "Álex Palou debe estar en los resultados")
            self.assertEqual(palou["points"], 631.0,
                             "Álex Palou debe conservar sus 631.0 puntos a pesar de la nota al pie.")

    # -------------------------------------------------------------------------
    # 3. IndyCar: Rechazo de Entrant Championship
    # -------------------------------------------------------------------------
    def test_03_indycar_entrant_table_is_rejected(self):
        """
        La tabla de Entrant Championship contiene '#10 Chip Ganassi Racing' y
        encabezado 'Driver / Entrant'. Debe ser descartada por completo,
        evitando introducir equipos o chasis como pilotos.
        """
        fixture_html = load_fixture("indycar_entrant_table.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("INDYCAR", ["http://dummy-indycar-entrant.local"])

            self.assertEqual(len(results), 0,
                             "La tabla de Entrant Championship debe ser rechazada, devolviendo lista vacía.")

    # -------------------------------------------------------------------------
    # 4. WEC: Posiciones oficiales y preservación de empates legítimos
    # -------------------------------------------------------------------------
    def test_04_wec_shared_points_preserves_official_ties(self):
        """
        Mike Conway, Kamui Kobayashi y Nyck de Vries comparten el coche #7 con 75 pts.
        El extractor debe leer la columna oficial de posición (1) y no forzar
        posiciones consecutivas artificiales (1, 2, 3).
        """
        fixture_html = load_fixture("wec_shared_points.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WEC", ["http://dummy-wec.local"])

            conway = next((r for r in results if "Conway" in r["name"]), None)
            kobayashi = next((r for r in results if "Kobayashi" in r["name"]), None)
            devries = next((r for r in results if "Vries" in r["name"]), None)

            self.assertIsNotNone(conway)
            self.assertIsNotNone(kobayashi)
            self.assertIsNotNone(devries)

            # Empate oficial en posición 1
            self.assertEqual(conway["position"], 1)
            self.assertEqual(kobayashi["position"], 1,
                             "Kobayashi debe conservar su posición oficial 1 en empate con Conway.")
            self.assertEqual(devries["position"], 1,
                             "De Vries debe conservar su posición oficial 1 en empate con Conway.")
            self.assertEqual(conway["points"], 75.0)
            self.assertEqual(kobayashi["points"], 75.0)
            self.assertEqual(devries["points"], 75.0)

    # -------------------------------------------------------------------------
    # 5. WEC: Selección de Hypercar y descarte de LMGT3 con caption
    # -------------------------------------------------------------------------
    def test_05_wec_hypercar_selected_and_lmgt3_discarded(self):
        """
        La página de WEC contiene tablas de Hypercar y LMGT3 con caption.
        El selector debe identificar expresamente la tabla Hypercar y descartar LMGT3.
        """
        fixture_html = load_fixture("wec_hypercar_vs_lmgt3.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WEC", ["http://dummy-wec-classes.local"])

            self.assertGreater(len(results), 0)
            self.assertEqual(results[0]["name"], "Mike Conway")
            names = [r["name"] for r in results]
            self.assertIn("Kévin Estre", names)
            self.assertNotIn("Klaus Bachler", names,
                             "Los pilotos de LMGT3 no deben mezclarse con la clasificación de Hypercar.")

    # -------------------------------------------------------------------------
    # 5b. WEC: Verificación positiva de Hypercar cuando LMGT3 no tiene caption
    # -------------------------------------------------------------------------
    def test_05b_wec_lmgt3_without_caption_followed_by_hypercar(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Tabla LMGT3 sin caption situada antes de la tabla Hypercar.
        El selector debe analizar el contexto de sección ('<h3>...</h3>'),
        descartar LMGT3 y verificar positivamente Hypercar.
        """
        html_lmgt3_first = """
        <h2>Championship standings</h2>

        <h3>FIA Endurance Trophy for LMGT3 Drivers</h3>
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><th>1</th><td>Klaus Bachler</td><td>68</td></tr>
        </table>

        <h3>FIA Hypercar World Endurance Drivers' Championship</h3>
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><th>1</th><td>Mike Conway</td><td>75</td></tr>
          <tr><th>1</th><td>Kamui Kobayashi</td><td>75</td></tr>
        </table>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html_lmgt3_first.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WEC", ["http://dummy-wec-lmgt3-first.local"])

            self.assertEqual(len(results), 2, "Debe extraer los pilotos de Hypercar")
            self.assertEqual(results[0]["name"], "Mike Conway")
            self.assertEqual(results[1]["name"], "Kamui Kobayashi")
            names = [r["name"] for r in results]
            self.assertNotIn("Klaus Bachler", names,
                             "Nunca debe extraerse LMGT3 como Hypercar, incluso si la tabla LMGT3 no tiene caption.")

    # -------------------------------------------------------------------------
    # 5c. WEC: Rechazo si no se confirma positivamente Hypercar
    # -------------------------------------------------------------------------
    def test_05c_wec_unconfirmed_table_is_rejected(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Si una tabla de WEC no puede identificarse positivamente como campeonato
        de pilotos Hypercar en su contexto (sección/caption), debe rechazarse.
        """
        html_generic = """
        <h2>Championship standings</h2>
        <h3>Overall Trophy</h3>
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><th>1</th><td>Generic Driver</td><td>100</td></tr>
        </table>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html_generic.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WEC", ["http://dummy-wec-generic.local"])

            self.assertEqual(len(results), 0,
                             "Debe rechazar la extracción si no puede confirmarse con seguridad que es Hypercar.")

    # -------------------------------------------------------------------------
    # 6. WRC: Selección exclusiva de pilotos sin copilotos ni fabricantes
    # -------------------------------------------------------------------------
    def test_06_wrc_driver_championship_without_codrivers_or_manufacturers(self):
        """
        El fixture de WRC contiene tablas de Fabricantes, Copilotos y Pilotos.
        El extractor debe ignorar Fabricantes y Copilotos ('Scott Martin') y extraer
        únicamente la clasificación de Pilotos ('Elfyn Evans').
        """
        fixture_html = load_fixture("wrc_driver_vs_codriver.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WRC", ["http://dummy-wrc.local"])

            self.assertGreater(len(results), 0)
            self.assertEqual(results[0]["name"], "Elfyn Evans",
                             "Debe extraer a Elfyn Evans (piloto) y no a Scott Martin (copiloto).")
            names = [r["name"] for r in results]
            self.assertNotIn("Scott Martin", names, "Los copilotos deben ser descartados.")
            self.assertNotIn("Toyota Gazoo Racing WRT", names, "Los fabricantes deben ser descartados.")

    # -------------------------------------------------------------------------
    # 7. MotoGP: Ausencia en pretemporada sin fallback a temporada previa
    # -------------------------------------------------------------------------
    def test_07_motogp_preseason_no_fallback_to_prior_season(self):
        """
        Si la temporada 2026 está en pretemporada (sin tabla de puntos),
        no debe permitirse fallback silencioso a la URL de 2025.
        Debe devolver lista vacía indicando ausencia de datos.
        """
        fixture_2026_empty = load_fixture("motogp_preseason_empty.html")
        fixture_2025_data = """
        <table class="wikitable">
          <tr><th>Pos.</th><th>Rider</th><th>Pts</th></tr>
          <tr><th>1</th><td>Jorge Martín</td><td>508</td></tr>
        </table>
        """

        call_count = 0
        def fake_urlopen(req, timeout=10):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "2026" in url:
                mock.read.return_value = fixture_2026_empty.encode("utf-8")
            else:
                mock.read.return_value = fixture_2025_data.encode("utf-8")
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = fetch_wikipedia_standings("MOTOGP", [
                "https://en.wikipedia.org/wiki/2026_MotoGP_World_Championship",
                "https://en.wikipedia.org/wiki/2025_MotoGP_World_Championship"
            ])

            # Se bloqueó el fallback a 2025: devuelve []
            self.assertEqual(len(results), 0,
                             "En pretemporada debe devolverse lista vacía sin recurrir a datos de 2025.")

    # -------------------------------------------------------------------------
    # 8. Nombres: Conservar 'Rallye Monte-Carlo' sin 'Rally dee'
    # -------------------------------------------------------------------------
    def test_08_name_cleaning_preserves_rallye_monte_carlo(self):
        """
        clean_gp_name conserva la grafía oficial de 'Rallye'.
        """
        self.assertEqual(clean_gp_name("Rallye Automobile Monte-Carlo", "WRC"), "Rallye Automobile Monte-Carlo")
        self.assertNotIn("Rally dee", clean_gp_name("Rallye Automobile Monte-Carlo", "WRC"))
        self.assertEqual(clean_gp_name("Rallye Monte-Carlo", "WRC"), "Rallye Monte-Carlo")

    # -------------------------------------------------------------------------
    # 9. Puntuaciones: Ceros legítimos aceptados
    # -------------------------------------------------------------------------
    def test_09_legitimate_zeros_accepted(self):
        """
        Ceros legítimos ('0', '-') se convierten correctamente a 0.0.
        """
        fixture_html = load_fixture("points_cells.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WRC", ["http://dummy-points.local"])

            driver_zero = next((r for r in results if "Piloto Con Cero" in r["name"]), None)
            self.assertIsNotNone(driver_zero)
            self.assertEqual(driver_zero["points"], 0.0)

            driver_dash = next((r for r in results if "Piloto Con Guion" in r["name"]), None)
            self.assertIsNotNone(driver_dash)
            self.assertEqual(driver_dash["points"], 0.0)

    # -------------------------------------------------------------------------
    # 10. Puntuaciones: Celdas con números múltiples (victorias/notas)
    # -------------------------------------------------------------------------
    def test_10_cells_with_multiple_numbers_parsed_correctly(self):
        """
        '450 (3)' extrae 450.0 descartando las victorias en paréntesis.
        '500 [1]' extrae 500.0 descartando la nota entre corchetes.
        """
        fixture_html = load_fixture("points_cells.html")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WRC", ["http://dummy-points.local"])

            driver_wins = next((r for r in results if "Victorias" in r["name"]), None)
            self.assertIsNotNone(driver_wins)
            self.assertEqual(driver_wins["points"], 450.0)

            driver_note = next((r for r in results if "Nota Al Pie" in r["name"]), None)
            self.assertIsNotNone(driver_note)
            self.assertEqual(driver_note["points"], 500.0)

    # -------------------------------------------------------------------------
    # 11. Columna de puntos que no es la última celda de la fila
    # -------------------------------------------------------------------------
    def test_11_points_column_not_last(self):
        """
        Identifica el índice de la columna mediante los encabezados correspondientes
        y extrae los puntos de la columna correcta, aunque exista una columna posterior
        como 'Gap', 'Behind' o 'Diff'.
        """
        table_html = """
        <table class="wikitable">
          <tr>
            <th scope="col">Pos</th>
            <th scope="col">Driver</th>
            <th scope="col">Points</th>
            <th scope="col">Gap</th>
          </tr>
          <tr>
            <th scope="row">1</th>
            <td>Max Verstappen</td>
            <td>400</td>
            <td>Leader</td>
          </tr>
          <tr>
            <th scope="row">2</th>
            <td>Lando Norris</td>
            <td>350</td>
            <td>-50</td>
          </tr>
        </table>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1.local"])

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["name"], "Max Verstappen")
            self.assertEqual(results[0]["points"], 400.0)
            self.assertEqual(results[1]["name"], "Lando Norris")
            self.assertEqual(results[1]["points"], 350.0)

    # -------------------------------------------------------------------------
    # 12. Puntuación ilegible en fila de piloto válido rechaza el lote completo
    # -------------------------------------------------------------------------
    def test_12_corrupt_points_on_valid_driver_rejects_batch(self):
        """
        Una fila que representa un piloto legítimo y contiene puntos imposibles de
        interpretar no debe eliminarse silenciosamente mediante continue.
        Debe registrar el error y rechazar el lote completo (devolviendo []),
        conservando los datos anteriores.
        """
        table_corrupt = """
        <table class="wikitable">
          <tr>
            <th scope="col">Pos</th>
            <th scope="col">Driver</th>
            <th scope="col">Points</th>
          </tr>
          <tr>
            <th scope="row">1</th>
            <td>Charles Leclerc</td>
            <td>200</td>
          </tr>
          <tr>
            <th scope="row">2</th>
            <td>Lewis Hamilton</td>
            <td>CORRUPTED_POINTS_DATA</td>
          </tr>
          <tr>
            <th scope="row">3</th>
            <td>George Russell</td>
            <td>180</td>
          </tr>
        </table>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_corrupt.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-corrupt.local"])

            self.assertEqual(len(results), 0,
                             "El lote completo debe ser rechazado si un piloto válido tiene puntos corruptos.")

    # -------------------------------------------------------------------------
    # 13. Posiciones oficiales: Empates 1, 1, 1 preservados
    # -------------------------------------------------------------------------
    def test_13_official_positions_ties_1_1_1(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Preserva las posiciones oficiales en empates legítimos (1, 1, 1).
        """
        table_ties = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><th>1</th><td>Piloto Alfa</td><td>100</td></tr>
          <tr><th>1</th><td>Piloto Beta</td><td>100</td></tr>
          <tr><th>1</th><td>Piloto Gamma</td><td>100</td></tr>
        </table>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_ties.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-ties.local"])

            self.assertEqual(len(results), 3)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["position"], 1)
            self.assertEqual(results[2]["position"], 1)

    # -------------------------------------------------------------------------
    # 14. Posiciones oficiales: Posición corrupta rechaza el lote completo
    # -------------------------------------------------------------------------
    def test_14_corrupt_position_rejects_batch(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Si una tabla dispone de columna de posición pero su valor no puede
        interpretarse (es inválido, ambiguo o corrupto), no debe asignarse default_pos.
        El lote debe ser rechazado completamente.
        """
        table_corrupt_pos = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><th>1</th><td>Piloto Valido</td><td>100</td></tr>
          <tr><th>???</th><td>Piloto Con Posicion Corrupta</td><td>90</td></tr>
          <tr><th>2</th><td>Otro Piloto</td><td>80</td></tr>
        </table>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_corrupt_pos.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-badpos.local"])

            self.assertEqual(len(results), 0,
                             "El lote completo debe ser rechazado si una posición oficial es ilegible.")

    # -------------------------------------------------------------------------
    # 15. Estructura compleja: Descarte de tablas con rowspan
    # -------------------------------------------------------------------------
    def test_15_complex_table_with_rowspan_is_rejected(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Tablas con rowspan en celdas de datos desplazan columnas en filas subsecuentes.
        El parser debe detectar la estructura compleja y rechazar la tabla.
        """
        table_rowspan = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td rowspan="2">1</td><td>Piloto 1</td><td>100</td></tr>
          <tr><td>Piloto 2</td><td>100</td></tr>
        </table>
        """
        self.assertTrue(has_complex_table_structure(table_rowspan))

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_rowspan.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-rowspan.local"])
            self.assertEqual(len(results), 0, "Tablas con rowspan deben ser rechazadas.")

    # -------------------------------------------------------------------------
    # 16. Estructura compleja: Descarte de tablas con colspan en datos
    # -------------------------------------------------------------------------
    def test_16_complex_table_with_colspan_is_rejected(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Tablas con colspan en celdas td desplazan las columnas subsiguientes.
        Deben ser detectadas y rechazadas.
        """
        table_colspan = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td colspan="2">Piloto y Posicion</td><td>100</td></tr>
        </table>
        """
        self.assertTrue(has_complex_table_structure(table_colspan))

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_colspan.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-colspan.local"])
            self.assertEqual(len(results), 0, "Tablas con colspan en datos deben ser rechazadas.")

    # -------------------------------------------------------------------------
    # 17. Encabezados multinivel válidos son aceptados y resueltos
    # -------------------------------------------------------------------------
    def test_17_valid_multilevel_grouped_headers_are_accepted(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Tablas con encabezados multinivel agrupados con colspan en th ya no son rechazadas
        indiscriminadamente y se resuelven de forma estructurada.
        """
        table_grouped = """
        <table class="wikitable">
          <tr><th colspan="2">Datos Generales</th><th>Puntos</th></tr>
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td>1</td><td>Piloto Simple</td><td>100</td></tr>
        </table>
        """
        self.assertFalse(has_complex_table_structure(table_grouped))

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_grouped.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-grouped.local"])
            self.assertEqual(len(results), 1, "Tablas con encabezados multinivel válidos deben ser aceptadas.")
            self.assertEqual(results[0]["name"], "Piloto Simple")
            self.assertEqual(results[0]["points"], 100.0)
            self.assertEqual(results[0]["position"], 1)

    # -------------------------------------------------------------------------
    # 18. Tabla simple válida es aceptada sin falsos positivos de complejidad
    # -------------------------------------------------------------------------
    def test_18_simple_valid_table_is_accepted(self):
        """
        CIERRE TÉCNICO 3A.3b.3:
        Una tabla estándar sin desplazamientos de columnas es procesada limpiamente.
        """
        table_simple = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><th>1</th><td>Piloto Normal</td><td>85</td></tr>
          <tr><th>2</th><td>Segundo Piloto</td><td>70</td></tr>
        </table>
        """
        self.assertFalse(has_complex_table_structure(table_simple))

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_simple.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-simple.local"])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["name"], "Piloto Normal")
            self.assertEqual(results[0]["points"], 85.0)
            self.assertEqual(results[1]["name"], "Segundo Piloto")
            self.assertEqual(results[1]["points"], 70.0)

    # -------------------------------------------------------------------------
    # 19. Estructura compleja con rowspan de dos dígitos (rowspan="10")
    # -------------------------------------------------------------------------
    def test_19_complex_table_with_rowspan_10_is_rejected(self):
        """
        FASE 3A.3b.4 (Comprobación previa A):
        Verifica que atributos de dos dígitos como rowspan="10" se interpreten
        numéricamente como span > 1 y la tabla sea rechazada.
        """
        table_rowspan_10 = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td rowspan="10">1</td><td>Piloto Dominante</td><td>250</td></tr>
          <tr><td>Piloto 2</td><td>180</td></tr>
        </table>
        """
        self.assertTrue(has_complex_table_structure(table_rowspan_10),
                        "rowspan='10' debe ser detectado como estructura compleja")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_rowspan_10.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-rowspan10.local"])
            self.assertEqual(len(results), 0, "Tablas con rowspan='10' deben ser descartadas.")

    # -------------------------------------------------------------------------
    # 20. Estructura compleja con colspan de dos dígitos (colspan="12")
    # -------------------------------------------------------------------------
    def test_20_complex_table_with_colspan_12_is_rejected(self):
        """
        FASE 3A.3b.4 (Comprobación previa A):
        Verifica que atributos de dos dígitos como colspan="12" en td o th
        se interpreten numéricamente y la tabla sea rechazada.
        """
        table_colspan_12 = """
        <table class="wikitable">
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td colspan="12">Nota general de resultados</td></tr>
          <tr><td>1</td><td>Piloto 1</td><td>100</td></tr>
        </table>
        """
        self.assertTrue(has_complex_table_structure(table_colspan_12),
                        "colspan='12' debe ser detectado como estructura compleja")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = table_colspan_12.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-colspan12.local"])
            self.assertEqual(len(results), 0, "Tablas con colspan='12' deben ser descartadas.")

    # -------------------------------------------------------------------------
    # 21. WEC: Verificación de contexto ajeno a la tabla (tabla posterior sin caption)
    # -------------------------------------------------------------------------
    def test_21_wec_table_does_not_leak_context_across_table_boundaries(self):
        """
        FASE 3A.3b.4 (Comprobación previa B):
        Verifica que una tabla genérica sin caption posterior a la sección Hypercar
        no herede indebidamente el contexto de Hypercar a través de delimitadores </table>.
        """
        html_content = """
        <h2>FIA Hypercar World Endurance Drivers' Championship</h2>
        <table class="wikitable">
          <!-- Primera tabla legítima de Hypercar -->
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td>1</td><td>Brendon Hartley</td><td>100</td></tr>
        </table>
        <p>Texto intermedio o análisis de equipo.</p>
        <table class="wikitable">
          <!-- Segunda tabla sin caption que no debe asociarse a Hypercar Drivers -->
          <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          <tr><td>1</td><td>Piloto No Hypercar</td><td>50</td></tr>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html_content.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WEC", ["http://dummy-wec-leak.local"])
            # Debe extraer ÚNICAMENTE la primera tabla (Brendon Hartley), NO la segunda
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["name"], "Brendon Hartley")

    # -------------------------------------------------------------------------
    # 22. WRC: Encabezados multinivel con rondas agrupadas
    # -------------------------------------------------------------------------
    def test_22_wrc_multilevel_headers(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Verifica que la estructura estándar de WRC con encabezado de 2 niveles
        (Pos, Driver, Rounds agrupados con colspan, Points) se procese limpiamente.
        """
        fixture_html = load_fixture("wrc_multilevel_headers.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WRC", ["http://dummy-wrc-multi.local"])
            self.assertEqual(len(results), 3, "Debe extraer 3 pilotos de WRC")
            self.assertEqual(results[0]["name"], "Elfyn Evans")
            self.assertEqual(results[0]["points"], 243.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Oliver Solberg")
            self.assertEqual(results[1]["points"], 238.0)
            self.assertEqual(results[2]["name"], "Sami Pajari")
            self.assertEqual(results[2]["points"], 237.0)

    # -------------------------------------------------------------------------
    # 23. WEC: Encabezados multinivel con preservación de empates oficiales
    # -------------------------------------------------------------------------
    def test_23_wec_multilevel_headers_preserves_ties(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Verifica que WEC Hypercar con encabezado de 2 niveles extraiga correctamente
        los pilotos preservando las posiciones de empate oficial (1, 1, 1 y 2, 2).
        """
        fixture_html = load_fixture("wec_multilevel_headers.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WEC", ["http://dummy-wec-multi.local"])
            self.assertEqual(len(results), 5, "Debe extraer 5 pilotos de WEC")
            self.assertEqual(results[0]["name"], "Sébastien Buemi")
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[0]["points"], 115.0)
            self.assertEqual(results[1]["name"], "Brendon Hartley")
            self.assertEqual(results[1]["position"], 1)
            self.assertEqual(results[1]["points"], 115.0)
            self.assertEqual(results[2]["name"], "Ryō Hirakawa")
            self.assertEqual(results[2]["position"], 1)
            self.assertEqual(results[2]["points"], 115.0)
            self.assertEqual(results[3]["name"], "Kamui Kobayashi")
            self.assertEqual(results[3]["position"], 2)
            self.assertEqual(results[3]["points"], 97.0)
            self.assertEqual(results[4]["name"], "Mike Conway")
            self.assertEqual(results[4]["position"], 2)
            self.assertEqual(results[4]["points"], 97.0)

    # -------------------------------------------------------------------------
    # 24. MotoGP: Encabezados multinivel con columnas Sprint y Race
    # -------------------------------------------------------------------------
    def test_24_motogp_multilevel_headers(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Verifica que MotoGP con encabezados agrupados de carreras (Sprint/Race)
        se procese limpiamente sin colisión de columnas de puntos.
        """
        fixture_html = load_fixture("motogp_multilevel_headers.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-multi.local"])
            self.assertEqual(len(results), 3, "Debe extraer 3 pilotos de MotoGP")
            self.assertEqual(results[0]["name"], "Marc Márquez")
            self.assertEqual(results[0]["points"], 425.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Jorge Martín")
            self.assertEqual(results[1]["points"], 410.0)
            self.assertEqual(results[2]["name"], "Marco Bezzecchi")
            self.assertEqual(results[2]["points"], 380.0)

    # -------------------------------------------------------------------------
    # 25. NASCAR: Encabezados multinivel con Points antes de Playoff
    # -------------------------------------------------------------------------
    def test_25_nascar_multilevel_headers_points_first(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Verifica que NASCAR Cup con encabezado de 2 niveles, posición como 'Rank'
        y columna 'Points' ubicada antes de las columnas de Playoff se procese correctamente.
        """
        fixture_html = load_fixture("nascar_multilevel_headers.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-multi.local"])
            self.assertEqual(len(results), 3, "Debe extraer 3 pilotos de NASCAR")
            self.assertEqual(results[0]["name"], "Kyle Larson")
            self.assertEqual(results[0]["points"], 2501.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Christopher Bell")
            self.assertEqual(results[1]["points"], 2452.0)
            self.assertEqual(results[2]["name"], "Denny Hamlin")
            self.assertEqual(results[2]["points"], 2445.0)

    # -------------------------------------------------------------------------
    # 26. IndyCar: Encabezados multinivel con notas al pie en puntos
    # -------------------------------------------------------------------------
    def test_26_indycar_multilevel_headers_palou_footnote(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Verifica que IndyCar con encabezado de 2 niveles y nota al pie en puntos
        (631<sup>a</sup>) preserve los 631.0 puntos de Álex Palou.
        """
        fixture_html = load_fixture("indycar_multilevel_headers.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("INDYCAR", ["http://dummy-indycar-multi.local"])
            self.assertEqual(len(results), 3, "Debe extraer 3 pilotos de IndyCar")
            self.assertEqual(results[0]["name"], "Álex Palou")
            self.assertEqual(results[0]["points"], 631.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Kyle Kirkwood")
            self.assertEqual(results[1]["points"], 545.0)
            self.assertEqual(results[2]["name"], "Christian Lundgaard")
            self.assertEqual(results[2]["points"], 535.0)

    # -------------------------------------------------------------------------
    # 27. Tabla ambigua sin columna de piloto identificable es rechazada
    # -------------------------------------------------------------------------
    def test_27_ambiguous_headers_rejected(self):
        """
        FASE 3A.3b.4 (Reparación de parser multinivel):
        Verifica que una tabla con encabezados ambiguos (sin columna de piloto)
        sea rechazada limpiamente sin fabricar datos ni lanzar excepciones.
        """
        fixture_html = load_fixture("ambiguous_table_headers.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("WRC", ["http://dummy-ambiguous.local"])
            self.assertEqual(len(results), 0, "Tablas ambiguas deben ser rechazadas")

    # -------------------------------------------------------------------------
    # 28. Fila incompleta de participante legítimo rechaza el lote completo
    # -------------------------------------------------------------------------
    def test_28_incomplete_participant_row_rejects_batch(self):
        """
        FASE 3A.3b.4 (Cierre técnico):
        Verifica que una fila que representa a un participante válido pero contiene
        columnas incompletas (por ejemplo, fila truncada sin celda de puntos)
        rechace el lote completo en lugar de ignorarla silenciosamente con continue,
        evitando pérdida silenciosa de participantes.
        """
        html = """
        <table class="wikitable">
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Max Verstappen</td><td>25</td></tr>
            <tr><td>2</td><td>Lando Norris</td></tr> <!-- Incompleta: celda de puntos ausente -->
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-incomplete.local"])
            self.assertEqual(len(results), 0, "El lote DEBE ser rechazado completamente para no perder a Norris en silencio")

    # -------------------------------------------------------------------------
    # 29. Fila con columnas desplazadas o nombre inválido rechaza el lote
    # -------------------------------------------------------------------------
    def test_29_displaced_columns_rejects_batch(self):
        """
        FASE 3A.3b.4 (Cierre técnico):
        Verifica que una fila con posición oficial válida pero nombre vacío o
        numérico (indicativo de columnas desplazadas) rechace el lote completo.
        """
        html = """
        <table class="wikitable">
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Charles Leclerc</td><td>25</td></tr>
            <tr><td>2</td><td></td><td>18</td></tr> <!-- Nombre ausente en posición clasificada -->
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-displaced.local"])
            self.assertEqual(len(results), 0, "El lote DEBE ser rechazado por desplazamiento de columnas")

    # -------------------------------------------------------------------------
    # 30. Fila válida con término genérico ('official') en equipo no es descartada
    # -------------------------------------------------------------------------
    def test_30_driver_with_generic_word_official_in_team_not_ignored(self):
        """
        FASE 3A.3b.4 (Ajuste previo localizado):
        Verifica que una fila válida de competidor no sea descartada accidentalmente
        por contener una palabra genérica como 'official' en el nombre del equipo,
        garantizando que is_ignorable_row no produzca falsos positivos en datos reales.
        """
        html = """
        <table class="wikitable">
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Team</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Carlos Sainz</td><td>Official Ferrari Partner Team</td><td>25</td></tr>
            <tr><td>2</td><td>George Russell</td><td>Mercedes AMG</td><td>18</td></tr>
            <tr><td colspan="4">Source: Official FIA Standings</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("F1", ["http://dummy-f1-official-team.local"])
            self.assertEqual(len(results), 2, "Debe extraer a los 2 pilotos sin descartar a Carlos Sainz")
            self.assertEqual(results[0]["name"], "Carlos Sainz")
            self.assertEqual(results[0]["points"], 25.0)
            self.assertEqual(results[1]["name"], "George Russell")
            self.assertEqual(results[1]["points"], 18.0)

    # -------------------------------------------------------------------------
    # 31. IndyCar: Tooltip de Wikipedia con style y title con penalización
    # -------------------------------------------------------------------------
    def test_31_indycar_palou_tooltip_extracted_correctly(self):
        """
        FASE 3A.3b.4 (Bloqueo 1 - IndyCar Tooltip):
        Verifica que Álex Palou con plantilla {{tooltip}} de Wikipedia (con etiqueta
        <style> y atributo title='Penalized 5 points...') sea interpretado correctamente
        con su valor visible de 631.0 puntos, sin confundir el '5' de la penalización.
        """
        fixture_html = load_fixture("indycar_palou_tooltip.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("INDYCAR", ["http://dummy-indycar-tooltip.local"])
            self.assertEqual(len(results), 3, "Debe extraer los 3 pilotos de IndyCar")
            self.assertEqual(results[0]["name"], "Álex Palou")
            self.assertEqual(results[0]["points"], 631.0, "Los puntos visibles deben ser exactamente 631.0")
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Kyle Kirkwood")
            self.assertEqual(results[1]["points"], 545.0)
            self.assertEqual(results[2]["name"], "Christian Lundgaard")
            self.assertEqual(results[2]["points"], 535.0)

    # -------------------------------------------------------------------------
    # 32. MotoGP: Celdas de carrera compartidas (colspan=2 en SPR/RAC) y múltiples pies
    # -------------------------------------------------------------------------
    def test_32_motogp_shared_race_cells_extracted_correctly(self):
        """
        FASE 3A.3b.4 (Bloqueo 2 - MotoGP celdas compartidas):
        Verifica que una tabla con colspan=2 en columnas de resultados (ej. INJ que
        unifica Sprint y Carrera) y múltiples filas de pie/leyenda no sea rechazada
        ni desplace la columna de puntos.
        """
        fixture_html = load_fixture("motogp_shared_race_cells.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-shared.local"])
            self.assertEqual(len(results), 3, "Debe extraer los 3 pilotos de MotoGP")
            self.assertEqual(results[0]["name"], "Marc Márquez")
            self.assertEqual(results[0]["points"], 425.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Jorge Martín")
            self.assertEqual(results[1]["points"], 410.0)
            self.assertEqual(results[1]["position"], 2)
            self.assertEqual(results[2]["name"], "Miguel Oliveira")
            self.assertEqual(results[2]["points"], 80.0, "La columna de puntos no debe sufrir desplazamiento")
            self.assertEqual(results[2]["position"], 3)

    # -------------------------------------------------------------------------
    # 33. NASCAR: Divisores de sección de playoffs y distinción de puntos
    # -------------------------------------------------------------------------
    def test_33_nascar_playoff_sections_extracted_correctly(self):
        """
        FASE 3A.3b.4 (Bloqueo 3 - NASCAR Playoffs):
        Verifica que una tabla de NASCAR Cup con divisores de sección en datos
        (Championship 4, Eliminated in Round of 8) se procese limpiamente,
        ignorando los divisores y seleccionando los puntos totales correctamente.
        """
        fixture_html = load_fixture("nascar_playoffs_sections.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-playoffs.local"])
            self.assertEqual(len(results), 3, "Debe extraer los 3 pilotos de NASCAR ignorando divisores")
            self.assertEqual(results[0]["name"], "Kyle Larson")
            self.assertEqual(results[0]["points"], 2501.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Christopher Bell")
            self.assertEqual(results[1]["points"], 2452.0)
            self.assertEqual(results[1]["position"], 2)
            self.assertEqual(results[2]["name"], "Denny Hamlin")
            self.assertEqual(results[2]["points"], 2400.0)
            self.assertEqual(results[2]["position"], 5)

    # -------------------------------------------------------------------------
    # 34. NASCAR Escenario A: Piloto elegible con puntos numéricos válidos
    # -------------------------------------------------------------------------
    def test_34_nascar_scenario_a_eligible_driver_with_valid_points(self):
        """
        FASE 3A.3b.4 (NASCAR Elegibilidad - Escenario A):
        Un participante elegible con puntos numéricos válidos (ej. William Byron con 2050 pts)
        debe ser incluido normalmente en la clasificación.
        """
        html = """
        <table class="wikitable sortable">
          <caption>NASCAR Cup Series Drivers' Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td><a href="/wiki/William_Byron">William Byron</a></td><td>2050</td></tr>
            <tr><td>2</td><td><a href="/wiki/Kyle_Larson">Kyle Larson</a></td><td>2040</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-a.local"])
            self.assertEqual(len(results), 2, "Debe extraer ambos pilotos elegibles")
            self.assertEqual(results[0]["name"], "William Byron")
            self.assertEqual(results[0]["points"], 2050.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Kyle Larson")
            self.assertEqual(results[1]["points"], 2040.0)
            self.assertEqual(results[1]["position"], 2)

    # -------------------------------------------------------------------------
    # 35. NASCAR Escenario B: Piloto con marca inequívoca de inelegibilidad excluido
    # -------------------------------------------------------------------------
    def test_35_nascar_scenario_b_ineligible_driver_excluded_with_unambiguous_marker(self):
        """
        FASE 3A.3b.4 (NASCAR Elegibilidad - Escenario B):
        Un participante con marca oficial e inequívoca de inelegibilidad (ej. Corey Heim†,
        marcado con <sup>†</sup> o (i) y celda de puntos vacía o con guión) debe ser excluido
        de la clasificación puntuable de Cup sin rechazar el lote de los pilotos elegibles.
        Usa el fixture oficial de regresión nascar_ineligible_corey_heim.html.
        """
        fixture_html = load_fixture("nascar_ineligible_corey_heim.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-b.local"])
            self.assertEqual(len(results), 3, "Debe extraer únicamente los 3 pilotos elegibles, omitiendo a Corey Heim")
            self.assertEqual(results[0]["name"], "William Byron")
            self.assertEqual(results[0]["points"], 2050.0)
            self.assertEqual(results[1]["name"], "Kyle Larson")
            self.assertEqual(results[1]["points"], 2040.0)
            self.assertEqual(results[2]["name"], "Christopher Bell")
            self.assertEqual(results[2]["points"], 2035.0)
            # Verificar que Corey Heim no está presente en los resultados
            names = [r["name"] for r in results]
            self.assertNotIn("Corey Heim", names)
            self.assertNotIn("Corey Heim†", names)

    # -------------------------------------------------------------------------
    # 36. NASCAR Escenario C: Piloto elegible con puntos vacíos rechaza el lote
    # -------------------------------------------------------------------------
    def test_36_nascar_scenario_c_eligible_driver_empty_points_rejects_batch(self):
        """
        FASE 3A.3b.4 (NASCAR Elegibilidad - Escenario C):
        Un participante elegible (sin marca de inelegibilidad) con celda de puntos vacía
        o ilegible no debe ser forzado a 0.0 ni ignorado silenciosamente: debe provocar
        el rechazo estricto del lote completo para evitar tablas corruptas.
        """
        html = """
        <table class="wikitable sortable">
          <caption>NASCAR Cup Series Drivers' Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td><a href="/wiki/William_Byron">William Byron</a></td><td>2050</td></tr>
            <tr><td>2</td><td><a href="/wiki/John_Doe">John Doe</a></td><td></td></tr> <!-- Sin marca de inelegibilidad -->
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-c.local"])
            self.assertEqual(len(results), 0, "El lote completo debe ser rechazado ante puntos vacíos en piloto elegible")

    # -------------------------------------------------------------------------
    # 37. NASCAR Escenario D: Piloto con cero puntos expresamente indicado se conserva
    # -------------------------------------------------------------------------
    def test_37_nascar_scenario_d_explicit_zero_points_preserved(self):
        """
        FASE 3A.3b.4 (NASCAR Elegibilidad - Escenario D):
        Un participante elegible con cero puntos expresamente consignados por la fuente
        (ej. '0' o '0.0') debe ser incluido en la clasificación conservando points = 0.0.
        """
        html = """
        <table class="wikitable sortable">
          <caption>NASCAR Cup Series Drivers' Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td><a href="/wiki/William_Byron">William Byron</a></td><td>2050</td></tr>
            <tr><td>2</td><td><a href="/wiki/Rookie_Driver">Rookie Driver</a></td><td>0</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-d.local"])
            self.assertEqual(len(results), 2, "Debe extraer ambos pilotos")
            self.assertEqual(results[0]["name"], "William Byron")
            self.assertEqual(results[0]["points"], 2050.0)
            self.assertEqual(results[1]["name"], "Rookie Driver")
            self.assertEqual(results[1]["points"], 0.0, "Los cero puntos explícitos deben ser conservados como 0.0")

    # -------------------------------------------------------------------------
    # 38. MotoGP: Colspan múltiple en rondas de carrera (lesión) y múltiples pies
    # -------------------------------------------------------------------------
    def test_38_motogp_multi_race_colspan_preserved(self):
        """
        FASE 3A.3b.4 (MotoGP Estructura real):
        Verifica que una tabla con celdas de carrera que abarcan múltiples rondas
        (ej. colspan=4 por lesión consecutiva en SPR/RAC) y múltiples filas de pie/leyenda
        no sea rechazada por has_complex_table_structure() y preserve la alineación exacta
        de la columna de Puntos.
        Usa el fixture oficial de regresión motogp_multi_race_colspan.html.
        """
        fixture_html = load_fixture("motogp_multi_race_colspan.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-colspan.local"])
            self.assertEqual(len(results), 3, "Debe extraer los 3 pilotos de MotoGP")
            self.assertEqual(results[0]["name"], "Marc Márquez")
            self.assertEqual(results[0]["points"], 74.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Jorge Martín")
            self.assertEqual(results[1]["points"], 58.0)
            self.assertEqual(results[1]["position"], 2)
            self.assertEqual(results[2]["name"], "Miguel Oliveira")
            self.assertEqual(results[2]["points"], 0.0, "La columna de puntos no debe sufrir desplazamiento")
            self.assertEqual(results[2]["position"], 3)

    # -------------------------------------------------------------------------
    # 39. MotoGP: Piloto con símbolo † no es excluido por lógica NASCAR
    # -------------------------------------------------------------------------
    def test_39_motogp_driver_with_dagger_not_excluded_by_nascar_logic(self):
        """
        CORRECCIÓN PUNTUAL 3A.3b.4:
        Verifica que un piloto de MotoGP que contenga el símbolo † en su nombre
        (ej. por notas históricas o marcas no relacionadas con NASCAR) NO sea excluido
        por la lógica de inelegibilidad de NASCAR, conservando sus puntos y posición.
        """
        html = """
        <table class="wikitable sortable">
          <caption>MotoGP World Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Rider</th><th>Bike</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Marc Márquez<sup>†</sup></td><td>Ducati</td><td>74</td></tr>
            <tr><td>2</td><td>Jorge Martín</td><td>Aprilia</td><td>58</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-dagger.local"])
            self.assertEqual(len(results), 2, "Ningún piloto de MotoGP debe ser excluido")
            self.assertEqual(results[0]["name"], "Marc Márquez", "El símbolo † debe ser limpiado del nombre")
            self.assertEqual(results[0]["points"], 74.0)
            self.assertEqual(results[0]["position"], 1)

    # -------------------------------------------------------------------------
    # 40. IndyCar: Piloto con nota (i) y puntos válidos se conserva
    # -------------------------------------------------------------------------
    def test_40_indycar_driver_with_note_i_and_valid_points_preserved(self):
        """
        CORRECCIÓN PUNTUAL 3A.3b.4:
        Verifica que un piloto de IndyCar con anotación (i) pero con puntuación
        numérica válida no sea excluido ni afectado por reglas de inelegibilidad.
        """
        html = """
        <table class="wikitable sortable">
          <caption>IndyCar Series Standings</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Álex Palou (i)</td><td>631</td></tr>
            <tr><td>2</td><td>Kyle Kirkwood</td><td>545</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("INDYCAR", ["http://dummy-indycar-i.local"])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["name"], "Álex Palou")
            self.assertEqual(results[0]["points"], 631.0)

    # -------------------------------------------------------------------------
    # 41. NASCAR: Piloto con nota genérica y puntos válidos se conserva
    # -------------------------------------------------------------------------
    def test_41_nascar_driver_with_generic_note_and_valid_points_preserved(self):
        """
        CORRECCIÓN PUNTUAL 3A.3b.4:
        Verifica que en NASCAR un piloto con una nota o marca auxiliar (ej. (i), *, o [1])
        pero con puntos numéricos válidos (2050 pts) NO sea excluido por dicha nota.
        """
        html = """
        <table class="wikitable sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron (i)</td><td>2050</td></tr>
            <tr><td>2</td><td>Kyle Larson*</td><td>2040</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-generic.local"])
            self.assertEqual(len(results), 2, "Pilotos con puntos válidos no deben ser excluidos por notas auxiliares")
            self.assertEqual(results[0]["name"], "William Byron")
            self.assertEqual(results[0]["points"], 2050.0)
            self.assertEqual(results[1]["name"], "Kyle Larson")
            self.assertEqual(results[1]["points"], 2040.0)

    # -------------------------------------------------------------------------
    # 42. NASCAR: Piloto con inelegibilidad respaldada por leyenda explícita y puntos vacíos se excluye
    # -------------------------------------------------------------------------
    def test_42_nascar_driver_with_verified_ineligibility_legend_excluded(self):
        """
        CORRECCIÓN PUNTUAL 3A.3b.4:
        Verifica que un piloto NASCAR con marca †, puntos vacíos y una leyenda oficial
        explícita al pie ('† – Ineligible for series driver points') sea excluido
        limpiamente sin romper el lote de los pilotos puntuables elegibles.
        """
        html = """
        <table class="sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron</td><td>2050</td></tr>
            <tr><td>2</td><td>Corey Heim<sup>†</sup></td><td></td></tr>
            <tr><td>3</td><td>Christopher Bell</td><td>2035</td></tr>
            <tr><td colspan="3">† – Ineligible for series driver points</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-verified.local"])
            self.assertEqual(len(results), 2, "Corey Heim debe ser excluido con evidencia verificada")
            names = [r["name"] for r in results]
            self.assertIn("William Byron", names)
            self.assertIn("Christopher Bell", names)
            self.assertNotIn("Corey Heim", names)

    # -------------------------------------------------------------------------
    # 43. NASCAR: Piloto con marca ambigua y puntos vacíos bloquea el lote
    # -------------------------------------------------------------------------
    def test_43_nascar_driver_with_ambiguous_marker_and_empty_points_rejects_batch(self):
        """
        CORRECCIÓN PUNTUAL 3A.3b.4:
        Verifica que si un piloto tiene una marca (ej. †) y puntos vacíos, pero la tabla
        NO contiene una leyenda que confirme inelegibilidad para puntos (marca ambigua),
        el parser NO lo excluya silenciosamente y RECHACE el lote completo por seguridad.
        """
        html = """
        <table class="sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron</td><td>2050</td></tr>
            <tr><td>2</td><td>John Doe<sup>†</sup></td><td></td></tr>
            <tr><td colspan="3">† – Most laps led</td></tr> <!-- Marca no relacionada con inelegibilidad -->
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-ambiguous.local"])
            self.assertEqual(len(results), 0, "Marca ambigua sin leyenda de inelegibilidad debe rechazar el lote")

    # -------------------------------------------------------------------------
    # 44. Puntuaciones: Guion sin significado verificado no se convierte automáticamente en cero
    # -------------------------------------------------------------------------
    def test_44_unverified_dash_not_converted_to_zero(self):
        """
        CORRECCIÓN PUNTUAL 3A.3b.4:
        Verifica que un guion medio/largo ('–', '—') sin significado verificado
        retorne None en parse_points_cell() y no se convierta artificialmente en 0.0.
        Asimismo, una tabla con celdas '–' sin leyenda que defina su significado
        como cero debe ser rechazada para evitar fabricar puntuaciones.
        """
        # 1. Comprobación directa en función parse_points_cell
        self.assertIsNone(parse_points_cell("–"), "Guion medio '–' sin verificar debe retornar None")
        self.assertIsNone(parse_points_cell("—"), "Guion largo '—' sin verificar debe retornar None")
        self.assertIsNone(parse_points_cell("<td>–</td>"), "Celda HTML con '–' sin verificar debe retornar None")

        # 2. Comprobación a nivel de tabla completa
        html = """
        <table class="wikitable">
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Piloto Uno</td><td>50</td></tr>
            <tr><td>2</td><td>Piloto Con Guion No Verificado</td><td>–</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-dash.local"])
            self.assertEqual(len(results), 0, "Guion sin verificar no debe convertirse en 0.0 y debe rechazar el lote")

    # -------------------------------------------------------------------------
    # 45. NASCAR: Piloto con daga † no es excluido si la leyenda solo explica (i)
    # -------------------------------------------------------------------------
    def test_45_nascar_dagger_driver_not_excluded_when_legend_only_explains_i(self):
        """
        FASE 3A.3b.4 (Verificación previa A):
        Verifica que la marca de inelegibilidad del piloto deba corresponder exactamente
        con la marca explicada en la leyenda. Si un piloto está marcado con † pero la leyenda
        al pie solo explica (i) como inelegible, no debe permitirse su exclusión y, al tener
        puntos vacíos, se debe rechazar el lote completo.
        """
        html = """
        <table class="sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron</td><td>2050</td></tr>
            <tr><td>2</td><td>Corey Heim<sup>†</sup></td><td></td></tr> <!-- Marcado con daga -->
            <tr><td colspan="3">(i) – Ineligible for driver points</td></tr> <!-- Leyenda solo para (i) -->
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-mismatch.local"])
            self.assertEqual(len(results), 0, "No debe excluir a Corey Heim por una leyenda que explica otra marca ((i))")

    # -------------------------------------------------------------------------
    # 46. Puntuaciones: Guion ASCII '-' sin verificar no se convierte en cero
    # -------------------------------------------------------------------------
    def test_46_ascii_hyphen_not_converted_to_zero_without_verified_legend(self):
        """
        FASE 3A.3b.4 (Verificación previa B):
        Verifica que el guion ASCII '-' sin un significado explícitamente verificado
        retorne None en parse_points_cell() y no se convierta artificialmente en 0.0.
        Los ceros numéricos expresos ('0', '0.0') siguen convirtiéndose siempre a 0.0.
        Asimismo, una tabla con celdas '-' sin leyenda explicativa debe ser rechazada.
        """
        # 1. Ceros numéricos expresos se conservan siempre como 0.0
        self.assertEqual(parse_points_cell("0"), 0.0)
        self.assertEqual(parse_points_cell("0.0"), 0.0)
        self.assertEqual(parse_points_cell("0,0"), 0.0)

        # 2. Guion ASCII '-' sin verificar retorna None
        self.assertIsNone(parse_points_cell("-"))
        self.assertIsNone(parse_points_cell("<td>-</td>"))

        # 3. Guion ASCII '-' con verificación explícita sí retorna 0.0
        self.assertEqual(parse_points_cell("-", dash_verified_as_zero=True), 0.0)

        # 4. A nivel de tabla completa: sin leyenda explícita, rechaza el lote
        html = """
        <table class="wikitable">
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Pts</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>Piloto Uno</td><td>50</td></tr>
            <tr><td>2</td><td>Piloto Con Guion ASCII Sin Verificar</td><td>-</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-ascii-dash.local"])
            self.assertEqual(len(results), 0, "Guion ASCII sin verificar debe rechazar el lote")

    # -------------------------------------------------------------------------
    # 47. MotoGP: Clasificación de pilotos realista con puntos alineados
    # -------------------------------------------------------------------------
    def test_47_motogp_realistic_riders_standings_extracted(self):
        """
        FASE 3A.3b.4 (Resolución dirigida - MotoGP):
        Verifica que la clasificación realista de pilotos de MotoGP con rondas,
        sprints y carreras (SPR/RAC), bajas por lesión (INJ) y puntuaciones reales
        (Márquez 431, Martín 371, Bezzecchi 357) sea aceptada por has_complex_table_structure()
        y extraída con perfecta alineación de columnas de posición, piloto y puntos.
        """
        html = """
        <h2><span id="Riders'_standings">Riders' standings</span></h2>
        <table class="wikitable sortable">
          <caption>MotoGP World Championship Riders' Standings</caption>
          <thead>
            <tr>
              <th rowspan="2">Pos.</th>
              <th rowspan="2">Rider</th>
              <th rowspan="2">Bike</th>
              <th colspan="2">QAT</th>
              <th colspan="2">POR</th>
              <th rowspan="2">Pts</th>
            </tr>
            <tr>
              <th>SPR</th><th>RAC</th>
              <th>SPR</th><th>RAC</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th>1</th>
              <td>Marc Márquez</td>
              <td>Ducati</td>
              <td>12</td><td>25</td>
              <td>12</td><td>25</td>
              <td>431</td>
            </tr>
            <tr>
              <th>2</th>
              <td>Jorge Martín</td>
              <td>Aprilia</td>
              <td>9</td><td>20</td>
              <td>9</td><td>20</td>
              <td>371</td>
            </tr>
            <tr>
              <th>3</th>
              <td>Marco Bezzecchi</td>
              <td>Aprilia</td>
              <td>7</td><td>16</td>
              <td>7</td><td>16</td>
              <td>357</td>
            </tr>
            <tr>
              <th>4</th>
              <td>Miguel Oliveira</td>
              <td>Aprilia</td>
              <td colspan="4">INJ</td>
              <td>0</td>
            </tr>
            <tr class="sortbottom"><td colspan="8">Source: FIM MotoGP Official Standings</td></tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-real.local"])
            self.assertEqual(len(results), 4, "Debe extraer los 4 pilotos de MotoGP")
            self.assertEqual(results[0]["name"], "Marc Márquez")
            self.assertEqual(results[0]["points"], 431.0)
            self.assertEqual(results[0]["position"], 1)
            self.assertEqual(results[1]["name"], "Jorge Martín")
            self.assertEqual(results[1]["points"], 371.0)
            self.assertEqual(results[1]["position"], 2)
            self.assertEqual(results[2]["name"], "Marco Bezzecchi")
            self.assertEqual(results[2]["points"], 357.0)
            self.assertEqual(results[2]["position"], 3)
            self.assertEqual(results[3]["name"], "Miguel Oliveira")
            self.assertEqual(results[3]["points"], 0.0)

    # -------------------------------------------------------------------------
    # 48. MotoGP: Lista de inscritos (Entry list) rechazada limpiamente
    # -------------------------------------------------------------------------
    def test_48_motogp_entry_list_rejected(self):
        """
        FASE 3A.3b.4 (Resolución dirigida - MotoGP):
        Verifica que la tabla de equipos e inscritos de MotoGP (Entry list), que contiene
        rowspan en Team/Constructor y carece de columna de puntos (Pts), sea rechazada
        y nunca confundida con una clasificación de pilotos.
        """
        html = """
        <h2><span id="Teams_and_riders">Teams and riders</span></h2>
        <table class="wikitable">
          <thead>
            <tr><th>Team</th><th>Constructor</th><th>No.</th><th>Rider</th><th>Rounds</th></tr>
          </thead>
          <tbody>
            <tr>
              <td rowspan="2">Ducati Lenovo Team</td>
              <td rowspan="2">Ducati</td>
              <td>93</td><td>Marc Márquez</td><td>1</td>
            </tr>
            <tr>
              <td>63</td><td>Francesco Bagnaia</td><td>1</td>
            </tr>
          </tbody>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-entry.local"])
            self.assertEqual(len(results), 0, "La lista de inscritos debe ser rechazada")

    # -------------------------------------------------------------------------
    # 49. NASCAR: Leyenda externa inmediatamente posterior a la tabla permite exclusión
    # -------------------------------------------------------------------------
    def test_49_nascar_external_legend_immediately_after_table(self):
        """
        FASE 3A.3b.4 (Resolución dirigida - NASCAR):
        Verifica que si la leyenda oficial explicativa de inelegibilidad
        († – Ineligible for series driver points) se ubica en un párrafo o bloque
        inmediatamente posterior a </table> (fuera de table_body pero en el contexto
        de la misma sección), el extractor reconozca la evidencia e ignore a Corey Heim.
        """
        html = """
        <h2><span id="Drivers'_championship">Drivers' championship</span></h2>
        <table class="sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron</td><td>2050</td></tr>
            <tr><td>2</td><td>Corey Heim<sup>†</sup></td><td></td></tr>
            <tr><td>3</td><td>Kyle Larson</td><td>2040</td></tr>
          </tbody>
        </table>
        <div class="refbegin">
          <p>† – Ineligible for series driver points</p>
        </div>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-ext.local"])
            self.assertEqual(len(results), 2, "Debe excluir a Corey Heim con leyenda externa inmediata")
            names = [r["name"] for r in results]
            self.assertIn("William Byron", names)
            self.assertIn("Kyle Larson", names)
            self.assertNotIn("Corey Heim", names)

    # -------------------------------------------------------------------------
    # 50. NASCAR: Leyenda de otra sección NO debe utilizarse
    # -------------------------------------------------------------------------
    def test_50_nascar_legend_from_different_section_not_used(self):
        """
        FASE 3A.3b.4 (Resolución dirigida - NASCAR):
        Verifica que una leyenda de inelegibilidad perteneciente a otra sección posterior
        (separada por un encabezado <h2> o <h3>) NO sea invadida ni aceptada para la tabla
        de pilotos, bloqueando el lote ante la falta de evidencia local de la tabla.
        """
        html = """
        <h2><span id="Drivers'_championship">Drivers' championship</span></h2>
        <table class="sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron</td><td>2050</td></tr>
            <tr><td>2</td><td>Corey Heim<sup>†</sup></td><td></td></tr>
          </tbody>
        </table>
        <!-- La sección finaliza aquí. La leyenda está en otra sección completamente distinta -->
        <h2><span id="Manufacturers'_championship">Manufacturers' championship</span></h2>
        <p>† – Ineligible for series driver points</p>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-other-sec.local"])
            self.assertEqual(len(results), 0, "Leyenda de otra sección no debe usarse y debe rechazar el lote")

    # -------------------------------------------------------------------------
    # 51. NASCAR: Marca ambigua y puntos vacíos con nota no relevante bloquea el lote
    # -------------------------------------------------------------------------
    def test_51_nascar_ambiguous_marker_and_empty_points_blocks_batch(self):
        """
        FASE 3A.3b.4 (Resolución dirigida - NASCAR):
        Verifica que si un piloto tiene puntos vacíos y una marca en el nombre, pero la
        nota externa explicativa no guarda relación con inelegibilidad de puntos
        (* – Pole position winner), el lote completo sea bloqueado de inmediato.
        """
        html = """
        <h2><span id="Drivers'_championship">Drivers' championship</span></h2>
        <table class="sortable">
          <caption>NASCAR Cup Series Championship</caption>
          <thead>
            <tr><th>Pos</th><th>Driver</th><th>Points</th></tr>
          </thead>
          <tbody>
            <tr><td>1</td><td>William Byron</td><td>2050</td></tr>
            <tr><td>2</td><td>John Doe*</td><td></td></tr>
          </tbody>
        </table>
        <p>* – Pole position winner</p>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-pole.local"])
            self.assertEqual(len(results), 0, "Nota ambigua no relevante debe rechazar el lote")

    # -------------------------------------------------------------------------
    # 52. MotoGP (FV-06): Tabla anidada real y 4 pilotos con rowspan=2 sin duplicados
    # -------------------------------------------------------------------------
    def test_52_motogp_nested_table_and_four_multiteam_rowspan_riders(self):
        """
        FV-06 (MotoGP HTML real):
        Verifica que el extractor:
        1. Desenrolle tablas contenedoras de maquetación sin truncar en el primer </table>.
        2. Ignore la tabla de inscritos (Teams and riders), la escala de puntos, la leyenda
           lateral (Race key) y la tabla de equipos (Teams' standings).
        3. Acepte los 4 pilotos con dos equipos (rowspan="2" alineado en Pos., Rider y Pts:
           Iker Lecuona, Augusto Fernández, Takaaki Nakagami y Lorenzo Savadori) sin
           generar duplicados y manteniendo los puntos exactamente alineados.
        """
        fixture_html = load_fixture("motogp_nested_multiteam_standings.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-fv06-nested.local"])

            # Deben extraerse exactamente 8 pilotos únicos (sin duplicar los 4 con rowspan=2)
            self.assertEqual(len(results), 8, "Deben extraerse exactamente 8 pilotos únicos sin duplicaciones")
            expected = [
                (1, "Marc Márquez", 439.0),
                (2, "Jorge Martín", 396.0),
                (3, "Marco Bezzecchi", 394.0),
                (21, "Iker Lecuona", 25.0),
                (24, "Augusto Fernández", 6.0),
                (27, "Takaaki Nakagami", 3.0),
                (31, "Lorenzo Savadori", 0.0),
                (32, "Michele Pirro", 0.0),
            ]
            actual = [(r["position"], r["name"], r["points"]) for r in results]
            self.assertEqual(actual, expected)
            self.assertEqual(len({r["name"] for r in results}), 8, "No debe existir ningún piloto duplicado")

    # -------------------------------------------------------------------------
    # 53. MotoGP (FV-06): Rechazo de colspan ambiguo, rowspan desalineado y no deduplicación por nombre
    # -------------------------------------------------------------------------
    def test_53_motogp_ambiguous_colspan_and_unaligned_rowspan_still_rejected(self):
        """
        FV-06 (MotoGP guardas de integridad):
        1. Una tabla anidada con fusión horizontal ambigua (colspan=2 en Pos/Rider) sigue rechazándose.
        2. Un rowspan=2 en Pos sin rowspan equivalente en Rider y Pts sigue rechazándose.
        3. Dos filas independientes (sin rowspan compartido) con el mismo nombre NO se deduplican
           silenciosamente por nombre en el extractor (para que el validador detecte la corrupción).
        """
        nested_bad_colspan = """
        <h2>Riders' standings</h2>
        <table><tr><td>
          <table class="wikitable">
            <tr><th>Pos.</th><th>Rider</th><th>Pts</th></tr>
            <tr><td colspan="2">1 Marc Márquez</td><td>439</td></tr>
          </table>
        </td></tr></table>
        """
        unaligned_rowspan = """
        <h2>Riders' standings</h2>
        <table class="wikitable">
          <tr><th>Pos.</th><th>Rider</th><th>Pts</th></tr>
          <tr><th rowspan="2">1</th><td>Marc Márquez</td><td>439</td></tr>
          <tr><td>Jorge Martín</td><td>396</td></tr>
        </table>
        """
        duplicate_independent_rows = """
        <h2>Riders' standings</h2>
        <table class="wikitable">
          <tr><th>Pos.</th><th>Rider</th><th>Pts</th></tr>
          <tr><th>1</th><td>Marc Márquez</td><td>439</td></tr>
          <tr><th>2</th><td>Marc Márquez</td><td>396</td></tr>
        </table>
        """
        for bad_html in (nested_bad_colspan, unaligned_rowspan):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = bad_html.encode("utf-8")
                mock_urlopen.return_value = mock_resp
                self.assertEqual(
                    fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-bad.local"]),
                    [],
                    "Estructuras con colspan ambiguo o rowspan desalineado deben ser rechazadas",
                )

        # Dos filas separadas con el mismo nombre no son continuación de rowspan: no ocultar el duplicado
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = duplicate_independent_rows.encode("utf-8")
            mock_urlopen.return_value = mock_resp
            dup_res = fetch_wikipedia_standings("MOTOGP", ["http://dummy-motogp-dup.local"])
            self.assertEqual(len(dup_res), 2, "No se debe deduplicar indiscriminadamente por nombre")

    # -------------------------------------------------------------------------
    # 54. NASCAR (FV-06): Subsección 'Ineligible for driver points' excluye a Corey Heim
    # -------------------------------------------------------------------------
    def test_54_nascar_ineligible_subsection_excludes_heim_and_preserves_eligible(self):
        """
        FV-06 (NASCAR HTML real):
        Verifica que el encabezado interno de ancho completo '<th colspan="6">Ineligible for driver points</th>'
        (seguido de subencabezados repetidos y leyendas de relevo † ajenas a inelegibilidad) omita
        únicamente a los pilotos de dicha subsección (Corey Heim, Harrison Burton, Myatt Snider) sin
        convertir vacíos en 0.0 ni usar nombres, preservando íntegramente los pilotos puntuables previos.
        """
        fixture_html = load_fixture("nascar_ineligible_subsection.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-fv06-subsec.local"])

            expected = [
                (1, "Kyle Larson", 304.0),
                (2, "Christopher Bell", 287.0),
                (41, "B. J. McLeod", 8.0),
            ]
            actual = [(r["position"], r["name"], r["points"]) for r in results]
            self.assertEqual(actual, expected)
            extracted_names = {r["name"] for r in results}
            self.assertNotIn("Corey Heim", extracted_names)
            self.assertNotIn("Harrison Burton", extracted_names)
            self.assertNotIn("Myatt Snider", extracted_names)

    # -------------------------------------------------------------------------
    # 55. NASCAR (FV-06): Inconsistencia numérica dentro de subsección inelegible rechaza lote
    # -------------------------------------------------------------------------
    def test_55_nascar_ineligible_subsection_rejects_numeric_points_or_position(self):
        """
        FV-06 (NASCAR validación de consistencia en subsección):
        Si dentro de la subsección 'Ineligible for driver points' aparece un registro con
        puntos numéricos (incluso 0) o con posición numérica asignada, el lote completo debe rechazarse.
        """
        html_with_numeric_points = """
        <h2>Drivers' championship</h2>
        <table class="wikitable sortable">
          <tr><th>Pos.</th><th>Driver</th><th>Pts.</th></tr>
          <tr><th>1</th><td>Kyle Larson</td><td>304</td></tr>
          <tr><th colspan="3">Ineligible for driver points</th></tr>
          <tr><th></th><td>Corey Heim</td><td>15</td></tr>
        </table>
        """
        html_with_numeric_pos = """
        <h2>Drivers' championship</h2>
        <table class="wikitable sortable">
          <tr><th>Pos.</th><th>Driver</th><th>Pts.</th></tr>
          <tr><th>1</th><td>Kyle Larson</td><td>304</td></tr>
          <tr><th colspan="3">Ineligible for driver points</th></tr>
          <tr><th>42</th><td>Corey Heim</td><td></td></tr>
        </table>
        """
        for inconsistent_html in (html_with_numeric_points, html_with_numeric_pos):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = inconsistent_html.encode("utf-8")
                mock_urlopen.return_value = mock_resp
                self.assertEqual(
                    fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-inconsistent.local"]),
                    [],
                    "Registros con posición o puntos numéricos dentro de inelegibles deben rechazar el lote",
                )

    # -------------------------------------------------------------------------
    # 56. Aislamiento (FV-06): Subsección inelegible no afecta otras categorías ni otras tablas
    # -------------------------------------------------------------------------
    def test_56_ineligible_subsection_isolated_to_current_nascar_table_only(self):
        """
        FV-06 (Aislamiento por categoría y tabla):
        1. En WRC, MOTOGP e INDYCAR, una fila '<th colspan="3">Ineligible for driver points</th>'
           seguida de un piloto sin puntos NO permite excluirlo y bloquea el lote.
        2. En NASCAR, el estado de subsección de una tabla previa descartada NO se filtra a la
           siguiente tabla del documento.
        """
        non_nascar_html = """
        <h2>Drivers' championship</h2>
        <table class="wikitable">
          <tr><th>Pos.</th><th>Driver</th><th>Pts.</th></tr>
          <tr><th>1</th><td>Álex Palou</td><td>631</td></tr>
          <tr><th colspan="3">Ineligible for driver points</th></tr>
          <tr><th></th><td>Piloto Invitado</td><td></td></tr>
        </table>
        """
        for cat in ("INDYCAR", "WRC", "MOTOGP"):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = non_nascar_html.encode("utf-8")
                mock_urlopen.return_value = mock_resp
                self.assertEqual(
                    fetch_wikipedia_standings(cat, [f"http://dummy-{cat.lower()}-no-leak.local"]),
                    [],
                    f"La subsección de inelegibles de NASCAR no debe aplicarse a {cat}",
                )

        # Dos tablas en NASCAR: la 1.ª activa Ineligible y falla por inconsistencia; la 2.ª no tiene
        # subsección inelegible y un piloto con puntos vacíos en la 2.ª debe seguir rechazándose
        two_nascar_tables = """
        <h2>Drivers' championship</h2>
        <table class="sortable">
          <tr><th>Pos.</th><th>Driver</th><th>Pts.</th></tr>
          <tr><th>1</th><td>Kyle Larson</td><td>304</td></tr>
          <tr><th colspan="3">Ineligible for driver points</th></tr>
          <tr><th>42</th><td>Inconsistente En Tabla 1</td><td>10</td></tr>
        </table>
        <table class="sortable">
          <caption>NASCAR Cup Series Drivers' Championship</caption>
          <tr><th>Pos.</th><th>Driver</th><th>Pts.</th></tr>
          <tr><th>1</th><td>Kyle Larson</td><td>304</td></tr>
          <tr><th></th><td>Piloto Sin Subseccion En Tabla 2</td><td></td></tr>
        </table>
        """
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = two_nascar_tables.encode("utf-8")
            mock_urlopen.return_value = mock_resp
            self.assertEqual(
                fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-table-leak.local"]),
                [],
                "El estado de subsección inelegible no puede filtrarse de una tabla a otra",
            )

    # -------------------------------------------------------------------------
    # 57. NASCAR (FV-07): Separador vertical <th rowspan="17"></th> en cabecera no desplaza Pts. hacia Stages
    # -------------------------------------------------------------------------
    def test_57_nascar_header_rowspan_17_separator_preserves_championship_points(self):
        """
        FV-07 (Propagación de rowspan entre header_rows y data_rows):
        Reproduce el defecto real de NASCAR 2026 donde la cabecera inicial declara
        '<th rowspan="17"></th>' entre carreras regulares y del Chase, seguido de 'Pts.' y 'Stages'.
        Debe extraer los puntos totales de 'Pts.' (2501, 2452, 2445, 2407...) en P1-P16
        en lugar de 'Stages' (304, 287, 295, 317...), mantener intactos P17-P41 (774, 8, 8)
        y excluir a Corey Heim bajo 'Ineligible for driver points'.
        """
        fixture_html = load_fixture("nascar_header_rowspan17_separator.html")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = fixture_html.encode("utf-8")
            mock_urlopen.return_value = mock_resp

            results = fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-fv07-rowspan17.local"])

            self.assertEqual(len(results), 19, "Deben extraerse los 19 pilotos puntuables sin duplicados ni pérdidas")
            self.assertEqual(len({r["name"] for r in results}), 19)
            by_pos = {r["position"]: (r["name"], r["points"]) for r in results}
            self.assertEqual(by_pos[1], ("Kyle Larson", 2501.0))
            self.assertEqual(by_pos[2], ("Christopher Bell", 2452.0))
            self.assertEqual(by_pos[3], ("Denny Hamlin", 2445.0))
            self.assertEqual(by_pos[4], ("Ryan Blaney", 2407.0))
            self.assertEqual(by_pos[5], ("Tyler Reddick", 2397.0))
            self.assertEqual(by_pos[6], ("Chase Briscoe", 2386.0))
            self.assertEqual(by_pos[7], ("Joey Logano", 2386.0))
            self.assertEqual(by_pos[16], ("Chris Buescher", 2212.0))
            self.assertEqual(by_pos[17], ("Shane van Gisbergen", 774.0))
            self.assertEqual(by_pos[40], ("Katherine Legge", 8.0))
            self.assertEqual(by_pos[41], ("B. J. McLeod", 8.0))
            self.assertNotIn("Corey Heim", {r["name"] for r in results})

    # -------------------------------------------------------------------------
    # 58. Grid 2D (FV-07): Rechazo de solapamientos de celdas y spans imposibles
    # -------------------------------------------------------------------------
    def test_58_parse_table_grid_rejects_overlapping_and_impossible_spans(self):
        """
        FV-07 (Integridad estructural de parse_table_grid):
        Verifica que solapamientos 2D (colspan invadiendo un rowspan activo), rowspans que
        exceden el total de filas de la tabla o filas con más celdas que el encabezado sean
        marcados como estructuras ambiguas/complejas (is_complex=True) y rechazados.
        """
        # 1. Solapamiento 2D: fila 1 tiene rowspan=2 en col 2 (DAY), fila 2 tiene colspan=2 en col 1 (Driver) invadiendo col 2
        overlapping_html = """
        <table class="wikitable">
          <tr><th>Pos.</th><th>Driver</th><th>DAY</th><th>Pts.</th></tr>
          <tr><td>1</td><td>Kyle Larson</td><td rowspan="2">1</td><td>2501</td></tr>
          <tr><td>2</td><td colspan="2">Christopher Bell</td><td>2452</td></tr>
        </table>
        """
        # 2. Span vertical imposible en cabecera: rowspan="10" cuando solo hay 2 filas de datos
        impossible_header_rowspan = """
        <table class="wikitable">
          <tr><th>Pos.</th><th>Driver</th><th rowspan="10"></th><th>Pts.</th></tr>
          <tr><td>1</td><td>Kyle Larson</td><td>2501</td></tr>
          <tr><td>2</td><td>Christopher Bell</td><td>2452</td></tr>
        </table>
        """
        # 3. Desbordamiento horizontal de columnas en fila de datos (más celdas que num_cols)
        overflow_cols_html = """
        <table class="wikitable">
          <tr><th>Pos.</th><th>Driver</th><th>Pts.</th></tr>
          <tr><td>1</td><td>Kyle Larson</td><td>Extra</td><td>2501</td></tr>
        </table>
        """
        for bad_table in (overlapping_html, impossible_header_rowspan, overflow_cols_html):
            _, _, is_complex = parse_table_grid(bad_table)
            self.assertTrue(is_complex, f"parse_table_grid debe marcar como compleja: {bad_table[:80]}")
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = bad_table.encode("utf-8")
                mock_urlopen.return_value = mock_resp
                self.assertEqual(
                    fetch_wikipedia_standings("NASCAR", ["http://dummy-nascar-badgrid.local"]),
                    [],
                    "Estructuras con solapamiento o spans imposibles deben rechazarse",
                )

    # -------------------------------------------------------------------------
    # 59. Wikimedia HTTP 200 (FV-09): User-Agent transparente, timeout=15s y 0 llamadas Telegram
    # -------------------------------------------------------------------------
    def test_59_wikimedia_http_200_user_agent_timeout_and_no_telegram(self):
        """
        FV-09 (Casos 1, 9 y 10):
        Verifica que ante HTTP 200:
        - Se extraen las posiciones válidas de WRC sin copilotos.
        - La petición usa un User-Agent transparente con la URL real del proyecto
          (https://github.com/mmnlvg020-code/gridbox-data), sin suplantar navegadores
          (Mozilla/5.0, Chrome, Safari, AppleWebKit) ni inventar correos electrónicos.
        - Se pasa explícitamente timeout=15.0 segundos a urllib.request.urlopen.
        - No se invoca send_telegram_message durante la extracción.
        """
        fixture_bytes = load_fixture("wrc_driver_vs_codriver.html").encode("utf-8")
        captured_calls = []

        def fake_urlopen(req, timeout=None):
            captured_calls.append((req, timeout))
            resp = MagicMock()
            resp.read.return_value = fixture_bytes
            return resp

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = fetch_wikipedia_standings(
                "WRC",
                ["https://en.wikipedia.org/wiki/2026_World_Rally_Championship"],
            )

        self.assertEqual(len(results), 2, "Debe extraer los 2 pilotos de WRC con HTTP 200")
        self.assertEqual(results[0]["name"], "Elfyn Evans")
        self.assertEqual(len(captured_calls), 1)

        req_obj, used_timeout = captured_calls[0]
        ua = req_obj.get_header("User-agent") or ""
        self.assertIn("https://github.com/mmnlvg020-code/gridbox-data", ua)
        for forbidden in ("mozilla/", "chrome/", "safari/", "applewebkit", "@"):
            self.assertNotIn(forbidden, ua.lower(), f"User-Agent no debe contener '{forbidden}': {ua}")
        self.assertEqual(used_timeout, 15.0, "El timeout explícito debe ser de 15 segundos")
        mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 60. Wikimedia HTTP 429 con Retry-After (FV-09): Respeto de espera y tope sin bucle infinito
    # -------------------------------------------------------------------------
    def test_60_http_429_with_retry_after_respected_and_bounded(self):
        """
        FV-09 (Casos 2, 7 y 10):
        1. Si HTTP 429 incluye Retry-After breve (ej. '3'), espera exactamente 3.0s y realiza
           como máximo 1 único reintento controlado.
        2. Si el segundo intento vuelve a devolver HTTP 429, detiene inmediatamente la fuente
           sin reintentos ilimitados ni cascada hacia URLs secundarias.
        3. Si Retry-After excede el techo prudente (ej. '120'), no bloquea ni reintenta (1 sola llamada).
        """
        fixture_bytes = load_fixture("wrc_driver_vs_codriver.html").encode("utf-8")
        url = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship"
        alt_url = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship_backup"

        # Subcaso A: 429 con Retry-After=3 seguido de 200 OK
        resp_ok = MagicMock()
        resp_ok.read.return_value = fixture_bytes
        err_429_short = urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "3"}, io.BytesIO(b""))

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=[err_429_short, resp_ok]) as mock_urlopen:
            results = fetch_wikipedia_standings("WRC", [url])

        self.assertEqual(len(results), 2, "Tras respetar Retry-After=3s debe recuperar WRC en el único reintento")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(3.0)
        mock_tg.assert_not_called()

        # Subcaso B: 429 persistente con Retry-After=2 -> máximo 2 llamadas totales, sin bucle ni cascada a alt_url
        def always_429(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {"Retry-After": "2"}, io.BytesIO(b"")
            )

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=always_429) as mock_urlopen:
            results_persistent = fetch_wikipedia_standings("WRC", [url, alt_url])

        self.assertEqual(results_persistent, [])
        self.assertEqual(mock_urlopen.call_count, 2, "Jamás debe exceder 1 reintento ni golpear URLs adicionales tras 429")
        mock_sleep.assert_called_once_with(2.0)
        mock_tg.assert_not_called()

        # Subcaso C: 429 con Retry-After=120 (> techo máximo) -> 0 reintentos, detención inmediata
        err_429_long = urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "120"}, io.BytesIO(b""))
        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=[err_429_long]) as mock_urlopen:
            results_long = fetch_wikipedia_standings("WRC", [url, alt_url])

        self.assertEqual(results_long, [])
        self.assertEqual(mock_urlopen.call_count, 1, "Retry-After alto debe detener la fuente sin reintentar")
        mock_sleep.assert_not_called()
        mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 61. Wikimedia HTTP 429 sin Retry-After (FV-09): Detención inmediata ante incertidumbre
    # -------------------------------------------------------------------------
    def test_61_http_429_without_retry_after_stops_source_without_retries(self):
        """
        FV-09 (Casos 3, 7 y 10):
        Ante HTTP 429 sin cabecera Retry-After (ventana de enfriamiento desconocida),
        no realiza ningún reintento ni continúa probando otras URLs de la fuente.
        """
        url1 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship"
        url2 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship_mirror"
        err_429_no_hdr = urllib.error.HTTPError(url1, 429, "Too Many Requests", {}, io.BytesIO(b""))

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=[err_429_no_hdr]) as mock_urlopen:
            results = fetch_wikipedia_standings("WRC", [url1, url2])

        self.assertEqual(results, [])
        self.assertEqual(mock_urlopen.call_count, 1, "429 sin Retry-After debe detenerse tras 1 única solicitud")
        mock_sleep.assert_not_called()
        mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 62. Timeout de conexión (FV-09): Timeout explícito de 15s y parada sin reintentos
    # -------------------------------------------------------------------------
    def test_62_connection_timeout_stops_cleanly_without_retries(self):
        """
        FV-09 (Casos 4, 7, 9 y 10):
        Ante socket.timeout / TimeoutError o URLError por timeout, verifica que se pasó
        timeout=15.0s, detiene la fuente sin reintentos y devuelve [] sin inventar datos.
        """
        url1 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship"
        url2 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship_mirror"

        for timeout_exc in (socket.timeout("timed out"), urllib.error.URLError(socket.timeout("timed out"))):
            seen_timeouts = []

            def timeout_urlopen(req, timeout=None, exc=timeout_exc):
                seen_timeouts.append(timeout)
                raise exc

            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("time.sleep") as mock_sleep, \
                 patch("urllib.request.urlopen", side_effect=timeout_urlopen) as mock_urlopen:
                results = fetch_wikipedia_standings("WRC", [url1, url2])

            self.assertEqual(results, [])
            self.assertEqual(mock_urlopen.call_count, 1, "Un timeout no debe disparar reintentos ni URLs extra")
            self.assertEqual(seen_timeouts, [15.0])
            mock_sleep.assert_not_called()
            mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 63. Error HTTP 403 (FV-09): Rechazo de acceso sin reintentos
    # -------------------------------------------------------------------------
    def test_63_http_403_forbidden_never_retries_and_stops_source(self):
        """
        FV-09 (Casos 5, 7 y 10):
        Ante HTTP 403 Forbidden, no reintenta jamás ni consulta URLs secundarias.
        """
        url1 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship"
        url2 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship_mirror"
        err_403 = urllib.error.HTTPError(url1, 403, "Forbidden", {}, io.BytesIO(b""))

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=[err_403]) as mock_urlopen:
            results = fetch_wikipedia_standings("WRC", [url1, url2])

        self.assertEqual(results, [])
        self.assertEqual(mock_urlopen.call_count, 1, "HTTP 403 debe abortar inmediatamente sin reintentar")
        mock_sleep.assert_not_called()
        mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 64. Error temporal HTTP 503/502/500 (FV-09): Reintento único acotado y parada limpia
    # -------------------------------------------------------------------------
    def test_64_http_503_temporary_error_bounded_retry(self):
        """
        FV-09 (Casos 6, 7 y 10):
        1. Ante HTTP 503 transitorio puntual, espera de forma conservadora (respetando Retry-After
           si está presente o pausa breve por defecto) y recupera en 1 único reintento.
        2. Si HTTP 503/502/500 persiste, se detiene en 2 llamadas totales sin bucle infinito.
        """
        fixture_bytes = load_fixture("wrc_driver_vs_codriver.html").encode("utf-8")
        url1 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship"
        url2 = "https://en.wikipedia.org/wiki/2026_World_Rally_Championship_mirror"

        # 503 puntual -> 200 OK
        resp_ok = MagicMock()
        resp_ok.read.return_value = fixture_bytes
        err_503_once = urllib.error.HTTPError(url1, 503, "Service Unavailable", {"Retry-After": "2"}, io.BytesIO(b""))

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=[err_503_once, resp_ok]) as mock_urlopen:
            results = fetch_wikipedia_standings("WRC", [url1])

        self.assertEqual(len(results), 2)
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(2.0)
        mock_tg.assert_not_called()

        # 503 persistente sin Retry-After -> máximo 2 intentos y parada total
        def always_503(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b""))

        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("time.sleep") as mock_sleep, \
             patch("urllib.request.urlopen", side_effect=always_503) as mock_urlopen:
            results_fail = fetch_wikipedia_standings("WRC", [url1, url2])

        self.assertEqual(results_fail, [])
        self.assertEqual(mock_urlopen.call_count, 2, "HTTP 503 persistente nunca debe superar 1 reintento")
        self.assertEqual(mock_sleep.call_count, 1)
        mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 65. Preservación de datos existentes cuando falla una solicitud (FV-09)
    # -------------------------------------------------------------------------
    def test_65_existing_standings_preserved_when_any_request_fails(self):
        """
        FV-09 (Casos 8 y 10):
        Si existe un archivo previo válido de standings y falla la solicitud de una categoría
        (ej. WRC recibe HTTP 429 mientras otras responden HTTP 200), el sincronizador de archivo
        preserva intactos los datos previos sin sobrescribirlos con un lote incompleto ni inventar
        datos históricos, y sin llamar a Telegram.
        """
        import update_data

        sync_fn = getattr(update_data, "sync_standings_to_file", None)
        self.assertIsNotNone(sync_fn, "update_data debe exponer sync_standings_to_file para proteger datos previos")

        existing_payload = {
            "WRC": [{"position": 1, "name": "Elfyn Evans", "points": 88.0, "wins": 0, "category": "WRC", "isDriver": True, "teamName": "", "nationality": ""}],
            "INDYCAR": [{"position": 1, "name": "Álex Palou", "points": 631.0, "wins": 0, "category": "INDYCAR", "isDriver": True, "teamName": "", "nationality": ""}],
        }
        indy_bytes = load_fixture("indycar_palou_tooltip.html").encode("utf-8")

        def selective_urlopen(req, timeout=None):
            if "World_Rally" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b""))
            resp = MagicMock()
            resp.read.return_value = indy_bytes
            return resp

        sources = {
            "INDYCAR": ["https://en.wikipedia.org/wiki/2026_IndyCar_Series"],
            "WRC": ["https://en.wikipedia.org/wiki/2026_World_Rally_Championship"],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = os.path.join(tmp_dir, "standings.json")
            with open(target_path, "w", encoding="utf-8") as fh:
                json.dump(existing_payload, fh, ensure_ascii=False, indent=2)
            before_sha = hashlib.sha256(open(target_path, "rb").read()).hexdigest()

            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("time.sleep"), \
                 patch("urllib.request.urlopen", side_effect=selective_urlopen):
                ok = sync_fn(target_path, wiki_sources=sources, inter_request_delay=0.0)

            after_bytes = open(target_path, "rb").read()
            after_sha = hashlib.sha256(after_bytes).hexdigest()

        self.assertFalse(ok, "Debe retornar False cuando falla alguna categoría")
        self.assertEqual(before_sha, after_sha, "El archivo existente debe preservarse intacto byte a byte")
        mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # Helpers auxiliares para pruebas BACK-03B (T1-T8)
    # -------------------------------------------------------------------------
    def _build_back03_sources_and_urlopen(self, *, corrupt_nascar=False, wrong_ics_year=False, fail_wrc_wiki=False, fail_nascar_ics=False):
        """Prepara fuentes simuladas de las 5 categorías obligatorias 100 % offline."""
        lmgt3_and_hypercar = (
            "<h2>FIA Endurance Trophy for LMGT3 Drivers</h2>\n"
            "<table class=\"wikitable\"><tr><th>Pos.</th><th>Driver</th><th>Points</th></tr>"
            "<tr><td>1</td><td>Piloto LMGT3</td><td>100</td></tr></table>\n"
            "<h2>FIA Hypercar World Endurance Drivers' Championship</h2>\n"
            + load_fixture("wec_multilevel_headers.html")
        )
        nascar_html = load_fixture("nascar_header_rowspan17_separator.html")
        if corrupt_nascar:
            nascar_html = (
                nascar_html.replace(">2501<", ">304<")
                .replace(">2452<", ">287<")
                .replace(">2445<", ">295<")
                .replace(">2407<", ">317<")
            )

        wiki_payloads = {
            "WRC": load_fixture("wrc_driver_vs_codriver.html").encode("utf-8"),
            "WEC": lmgt3_and_hypercar.encode("utf-8"),
            "MOTOGP": load_fixture("motogp_nested_multiteam_standings.html").encode("utf-8"),
            "NASCAR": nascar_html.encode("utf-8"),
            "INDYCAR": load_fixture("indycar_palou_tooltip.html").encode("utf-8"),
        }
        ics_year = 2025 if wrong_ics_year else 2026

        import update_data
        url_to_ics = {u: c for c, u in update_data.ICS_URLS.items()}
        url_to_wiki = {urls[0]: c for c, urls in update_data.WIKI_STANDINGS.items()}

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url in url_to_ics:
                cat = url_to_ics[url]
                if fail_nascar_ics and cat == "NASCAR":
                    raise urllib.error.URLError("simulated ICS failure")
                ics = (
                    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
                    f"SUMMARY:{cat} - Grand Prix Round 1\r\n"
                    f"DTSTART:{ics_year}0510T140000Z\r\n"
                    "LOCATION:Official Circuit, Spain\r\n"
                    "END:VEVENT\r\nEND:VCALENDAR\r\n"
                ).encode("utf-8")
                resp = MagicMock()
                resp.status = 200
                resp.read.return_value = ics
                return resp
            if url in url_to_wiki:
                cat = url_to_wiki[url]
                if fail_wrc_wiki and cat == "WRC":
                    raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b""))
                resp = MagicMock()
                resp.status = 200
                resp.read.return_value = wiki_payloads[cat]
                return resp
            raise AssertionError(f"URL inesperada en prueba offline: {url}")

        return fake_urlopen

    # -------------------------------------------------------------------------
    # 66. BACK-03B (T1 y T2): Fallo de disco e interrupción en archivos temporales
    # -------------------------------------------------------------------------
    def test_66_disk_error_or_interrupt_on_temp_preserves_production_files(self):
        """
        Verifica que un fallo de escritura (ENOSPC) o interrupción (KeyboardInterrupt)
        al generar los temporales deja intactos races.json y standings.json sin residuos .tmp/.bak.
        """
        import update_data

        sync_all = getattr(update_data, "sync_all_data", None)
        self.assertIsNotNone(sync_all, "update_data debe exponer sync_all_data para actualización atómica")

        fake_urlopen = self._build_back03_sources_and_urlopen()
        with tempfile.TemporaryDirectory() as tmp_dir:
            races_p = os.path.join(tmp_dir, "races.json")
            standings_p = os.path.join(tmp_dir, "standings.json")
            with open(races_p, "w", encoding="utf-8") as fh:
                fh.write('[{"seed": "races_prev"}]')
            with open(standings_p, "w", encoding="utf-8") as fh:
                fh.write('{"seed": "standings_prev"}')
            sha_r_before = hashlib.sha256(open(races_p, "rb").read()).hexdigest()
            sha_s_before = hashlib.sha256(open(standings_p, "rb").read()).hexdigest()

            # Subcaso T1: OSError(ENOSPC) en el segundo json.dump (standings temporal)
            real_dump = json.dump
            dump_calls = {"n": 0}

            def failing_second_dump(obj, fp, *args, **kwargs):
                dump_calls["n"] += 1
                if dump_calls["n"] == 2:
                    raise OSError(28, "No space left on device")
                return real_dump(obj, fp, *args, **kwargs)

            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("json.dump", side_effect=failing_second_dump):
                ok_disk = sync_all(races_p, standings_p, expected_season=2026, inter_request_delay=0.0)

            self.assertFalse(ok_disk, "Un fallo de disco en temporales debe retornar False")
            self.assertEqual(hashlib.sha256(open(races_p, "rb").read()).hexdigest(), sha_r_before)
            self.assertEqual(hashlib.sha256(open(standings_p, "rb").read()).hexdigest(), sha_s_before)
            self.assertEqual(sorted(os.listdir(tmp_dir)), ["races.json", "standings.json"])
            mock_tg.assert_not_called()

            # Subcaso T2: KeyboardInterrupt durante la serialización del segundo temporal
            dump_calls["n"] = 0

            def interrupted_dump(obj, fp, *args, **kwargs):
                dump_calls["n"] += 1
                if dump_calls["n"] == 2:
                    raise KeyboardInterrupt("simulated user interrupt")
                return real_dump(obj, fp, *args, **kwargs)

            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("json.dump", side_effect=interrupted_dump):
                with self.assertRaises(KeyboardInterrupt):
                    sync_all(races_p, standings_p, expected_season=2026, inter_request_delay=0.0)

            self.assertEqual(hashlib.sha256(open(races_p, "rb").read()).hexdigest(), sha_r_before)
            self.assertEqual(hashlib.sha256(open(standings_p, "rb").read()).hexdigest(), sha_s_before)
            self.assertEqual(sorted(os.listdir(tmp_dir)), ["races.json", "standings.json"])
            mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 67. BACK-03B (T3 y T4): Política Todo-o-nada en fallo parcial y validación previa
    # -------------------------------------------------------------------------
    def test_67_partial_extraction_or_validation_failure_blocks_both_files(self):
        """
        Verifica Decisión 3A (Todo o nada) y validación conjunta previa sobre temporales:
        - Si falla 1 categoría de Wikipedia (aunque las 5 ICS aprueben), NO toca races.json ni standings.json.
        - Si falla 1 categoría de ICS (aunque las 5 Wiki estén disponibles), NO toca ninguno de los dos.
        - Si falla validate_pipeline (ej. Stage Points en NASCAR 2026 o temporada 2025), NO promueve temporales.
        """
        import update_data

        sync_all = getattr(update_data, "sync_all_data", None)
        self.assertIsNotNone(sync_all, "update_data debe exponer sync_all_data")

        scenarios = [
            ("fallo_wrc_wiki", dict(fail_wrc_wiki=True)),
            ("fallo_nascar_ics", dict(fail_nascar_ics=True)),
            ("nascar_stage_points_corruptos", dict(corrupt_nascar=True)),
            ("ics_temporada_2025", dict(wrong_ics_year=True)),
        ]
        for label, opts in scenarios:
            with self.subTest(scenario=label):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    races_p = os.path.join(tmp_dir, "races.json")
                    standings_p = os.path.join(tmp_dir, "standings.json")
                    with open(races_p, "w", encoding="utf-8") as fh:
                        fh.write('[{"baseline": 1}]')
                    with open(standings_p, "w", encoding="utf-8") as fh:
                        fh.write('{"baseline": 2}')
                    sha_r = hashlib.sha256(open(races_p, "rb").read()).hexdigest()
                    sha_s = hashlib.sha256(open(standings_p, "rb").read()).hexdigest()

                    fake_open = self._build_back03_sources_and_urlopen(**opts)
                    with patch("update_data.send_telegram_message") as mock_tg, \
                         patch("urllib.request.urlopen", side_effect=fake_open):
                        ok = sync_all(races_p, standings_p, expected_season=2026, inter_request_delay=0.0)

                    self.assertFalse(ok, f"Escenario {label} debe bloquearse y devolver False")
                    self.assertEqual(hashlib.sha256(open(races_p, "rb").read()).hexdigest(), sha_r)
                    self.assertEqual(hashlib.sha256(open(standings_p, "rb").read()).hexdigest(), sha_s)
                    self.assertEqual(sorted(os.listdir(tmp_dir)), ["races.json", "standings.json"])
                    mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 68. BACK-03B (T5): Fallo en el segundo os.replace() y rollback compensatorio
    # -------------------------------------------------------------------------
    def test_68_second_os_replace_failure_restores_first_file_via_rollback(self):
        """
        Verifica que si el primer os.replace() actualiza races.json pero el segundo os.replace()
        falla con PermissionError sobre standings.json, el rollback compensatorio restaura
        races.json a su contenido exacto previo y limpia archivos .tmp/.bak.
        """
        import update_data

        sync_all = getattr(update_data, "sync_all_data", None)
        self.assertIsNotNone(sync_all, "update_data debe exponer sync_all_data")

        fake_urlopen = self._build_back03_sources_and_urlopen()
        real_replace = os.replace

        with tempfile.TemporaryDirectory() as tmp_dir:
            races_p = os.path.join(tmp_dir, "races.json")
            standings_p = os.path.join(tmp_dir, "standings.json")
            original_races_bytes = b'[{"version": "v1_prev_races"}]\n'
            original_standings_bytes = b'{"version": "v1_prev_standings"}\n'
            with open(races_p, "wb") as fh:
                fh.write(original_races_bytes)
            with open(standings_p, "wb") as fh:
                fh.write(original_standings_bytes)

            sha_r_before = hashlib.sha256(original_races_bytes).hexdigest()
            sha_s_before = hashlib.sha256(original_standings_bytes).hexdigest()
            replace_calls = {"count": 0, "saw_mutated_races": False}

            def fail_on_second_replace(src, dst):
                replace_calls["count"] += 1
                if replace_calls["count"] == 1:
                    # Primera promoción (races.json): ejecutar reemplazo real
                    return real_replace(src, dst)
                if replace_calls["count"] == 2:
                    # Comprobar que races.json ya había cambiado antes del fallo del segundo archivo
                    current_sha = hashlib.sha256(open(races_p, "rb").read()).hexdigest()
                    replace_calls["saw_mutated_races"] = (current_sha != sha_r_before)
                    raise PermissionError("simulated lock on standings.json")
                # Tercera llamada: rollback compensatorio restaurando races.json desde .bak
                return real_replace(src, dst)

            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("os.replace", side_effect=fail_on_second_replace):
                ok = sync_all(races_p, standings_p, expected_season=2026, inter_request_delay=0.0)

            self.assertFalse(ok, "Debe retornar False tras fallo en el segundo os.replace()")
            self.assertTrue(replace_calls["saw_mutated_races"], "El primer os.replace() debió ejecutarse realmente")
            self.assertEqual(
                hashlib.sha256(open(races_p, "rb").read()).hexdigest(),
                sha_r_before,
                "El rollback compensatorio debe restaurar races.json byte a byte",
            )
            self.assertEqual(
                hashlib.sha256(open(standings_p, "rb").read()).hexdigest(),
                sha_s_before,
                "standings.json debe permanecer intacto",
            )
            self.assertEqual(sorted(os.listdir(tmp_dir)), ["races.json", "standings.json"])
            mock_tg.assert_not_called()

    # -------------------------------------------------------------------------
    # 69. BACK-03B (T6 y T7): Éxito atómico completo, códigos CLI 0/1 y 0 falsos Telegram
    # -------------------------------------------------------------------------
    def test_69_full_atomic_success_exit_codes_and_no_premature_telegram(self):
        """
        Verifica éxito completo sobre directorio aislado, códigos de salida de main() (0 en éxito,
        1 en fallo) y ausencia total de llamadas a Telegram durante la sincronización de datos.
        """
        import update_data
        import validate_data

        ok_urlopen = self._build_back03_sources_and_urlopen()
        fail_urlopen = self._build_back03_sources_and_urlopen(fail_wrc_wiki=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            races_p = os.path.join(tmp_dir, "races.json")
            standings_p = os.path.join(tmp_dir, "standings.json")

            # 1. Éxito completo -> exit_code == 0, ambos JSON válidos en 2026, 0 llamadas Telegram
            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=ok_urlopen):
                rc_ok = update_data.main(["--races-out", races_p, "--standings-out", standings_p, "--delay", "0"])

            self.assertEqual(rc_ok, 0, "main() debe retornar código 0 en actualización íntegra validada")
            mock_tg.assert_not_called()
            valid, _ = validate_data.validate_pipeline(races_p, standings_p, expected_season=2026)
            self.assertTrue(valid)
            self.assertEqual(sorted(os.listdir(tmp_dir)), ["races.json", "standings.json"])

            sha_r_valid = hashlib.sha256(open(races_p, "rb").read()).hexdigest()
            sha_s_valid = hashlib.sha256(open(standings_p, "rb").read()).hexdigest()

            # 2. Fallo parcial posterior -> exit_code == 1, archivos previos intactos, 0 llamadas Telegram
            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fail_urlopen):
                rc_fail = update_data.main(["--races-out", races_p, "--standings-out", standings_p, "--delay", "0"])

            self.assertEqual(rc_fail, 1, "main() debe retornar código 1 cuando falla una categoría")
            mock_tg.assert_not_called()
            self.assertEqual(hashlib.sha256(open(races_p, "rb").read()).hexdigest(), sha_r_valid)
            self.assertEqual(hashlib.sha256(open(standings_p, "rb").read()).hexdigest(), sha_s_valid)

    # -------------------------------------------------------------------------
    # 70. BACK-03B (T8): Notificador final veraz, silencio en NO_CHANGES y redacción de secretos
    # -------------------------------------------------------------------------
    def test_70_telegram_outcome_truthfulness_timeout_and_secret_redaction(self):
        """
        Verifica Decisión 2A y seguridad de secretos:
        1. Sin credenciales: no abre red ni falla ruidosamente.
        2. NO_CHANGES: 0 llamadas HTTP a Telegram (silencio estricto).
        3. PUBLISHED: 1 llamada con timeout=15.0s confirmando publicación remota y commit.
        4. FAILED: 1 llamada con timeout=15.0s reportando etapa fallida sin afirmar éxito.
        5. Si urlopen lanza excepción conteniendo el token o chat_id, se redactan ('[REDACTED]') en logs.
        """
        import contextlib
        import update_data

        dispatch_fn = getattr(update_data, "dispatch_workflow_notification", None)
        self.assertIsNotNone(dispatch_fn, "update_data debe exponer dispatch_workflow_notification")

        # 1. Sin credenciales -> 0 llamadas de red
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}, clear=False), \
             patch("urllib.request.urlopen") as m_open:
            sent = update_data.send_telegram_message("mensaje prueba")
            self.assertFalse(sent)
            m_open.assert_not_called()

        fake_env = {
            "TELEGRAM_BOT_TOKEN": "999888:TOP_SECRET_BOT_TOKEN_ABC",
            "TELEGRAM_CHAT_ID": "-100777666555",
        }

        # 2. NO_CHANGES -> silencio estricto (0 envíos incluso con credenciales configuradas)
        with patch.dict(os.environ, fake_env, clear=False), \
             patch("urllib.request.urlopen") as m_open:
            res_noop = dispatch_fn("NO_CHANGES")
            self.assertFalse(res_noop)
            m_open.assert_not_called()

        # 3. PUBLISHED -> 1 único envío con timeout explícito de 15.0s
        seen_tg = []

        def ok_tg_urlopen(req, timeout=None):
            body = urllib.parse.parse_qs(req.data.decode("utf-8"))
            seen_tg.append((req.full_url, body.get("text", [""])[0], timeout))
            resp = MagicMock()
            resp.status = 200
            return resp

        with patch.dict(os.environ, fake_env, clear=False), \
             patch("urllib.request.urlopen", side_effect=ok_tg_urlopen):
            res_pub = dispatch_fn("PUBLISHED", commit_sha="a1b2c3d", run_url="https://github.com/example/run/1")

        self.assertTrue(res_pub)
        self.assertEqual(len(seen_tg), 1)
        self.assertEqual(seen_tg[0][2], 15.0)
        self.assertIn("Publicación remota confirmada", seen_tg[0][1])
        self.assertIn("a1b2c3d", seen_tg[0][1])

        # 4. FAILED -> 1 único envío con etapa exacta, nunca afirma publicación
        seen_tg.clear()
        with patch.dict(os.environ, fake_env, clear=False), \
             patch("urllib.request.urlopen", side_effect=ok_tg_urlopen):
            res_fail = dispatch_fn("FAILED", failed_step="validate (failure)", run_url="https://github.com/example/run/2")

        self.assertTrue(res_fail)
        self.assertEqual(len(seen_tg), 1)
        self.assertEqual(seen_tg[0][2], 15.0)
        self.assertIn("validate (failure)", seen_tg[0][1])
        self.assertNotIn("Publicación remota confirmada", seen_tg[0][1])

        # 5. Redacción obligatoria de secretos en logs ante fallo HTTP en la API de Telegram
        def leaking_urlopen(req, timeout=None):
            raise urllib.error.URLError(
                f"Failed POST to {req.full_url} for chat_id=-100777666555"
            )

        buf = io.StringIO()
        with patch.dict(os.environ, fake_env, clear=False), \
             patch("urllib.request.urlopen", side_effect=leaking_urlopen), \
             contextlib.redirect_stdout(buf), \
             contextlib.redirect_stderr(buf):
            sent_err = dispatch_fn("FAILED", failed_step="publish (failure)")

        logged = buf.getvalue()
        self.assertFalse(sent_err)
        self.assertNotIn("TOP_SECRET_BOT_TOKEN_ABC", logged, "El token NUNCA debe aparecer en logs")
        self.assertNotIn("-100777666555", logged, "El chat_id NUNCA debe aparecer en logs")
        self.assertIn("[REDACTED]", logged)

    # -------------------------------------------------------------------------
    # 71. BACK-03B.1 (Defecto 1): Preservación del respaldo .bak ante ROLLBACK_FAILED
    # -------------------------------------------------------------------------
    def test_71_rollback_failure_preserves_backup_and_reports_rollback_failed(self):
        """
        Verifica la corrección del Defecto 1 (BACK-03B.1):
        - Subcaso A: 1.º os.replace() exitoso, 2.º os.replace() falla y 3.º os.replace() (rollback)
          también falla -> preserva intacto el archivo .bak original, reporta ROLLBACK_FAILED con la
          ruta del respaldo, nunca afirma restauración ni éxito, retorna exit code 1 y 0 llamadas Telegram.
        - Subcaso B: races.json no existía previamente, falla el 2.º os.replace() y falla también la
          eliminación compensatoria os.unlink(races.json) -> reporta ROLLBACK_FAILED y exit code 1.
        - Subcaso C: cuando el rollback sí se completa, reporta explícitamente ROLLBACK_OK y limpia .bak.
        """
        import contextlib
        import update_data

        fake_urlopen = self._build_back03_sources_and_urlopen()
        real_replace = os.replace
        real_unlink = os.unlink

        # Subcaso A: Triple fallo (promoción #2 falla + restauración desde .bak falla)
        with tempfile.TemporaryDirectory() as tmp_dir:
            races_p = os.path.join(tmp_dir, "races.json")
            standings_p = os.path.join(tmp_dir, "standings.json")
            original_races_bytes = b'[{"version": "v1_precious_backup"}]\n'
            original_standings_bytes = b'{"version": "v1_standings_intact"}\n'
            with open(races_p, "wb") as fh:
                fh.write(original_races_bytes)
            with open(standings_p, "wb") as fh:
                fh.write(original_standings_bytes)

            sha_r_before = hashlib.sha256(original_races_bytes).hexdigest()
            sha_s_before = hashlib.sha256(original_standings_bytes).hexdigest()
            calls = {"n": 0}

            def fail_second_and_third_replace(src, dst):
                calls["n"] += 1
                if calls["n"] == 1:
                    return real_replace(src, dst)
                if calls["n"] == 2:
                    raise PermissionError("lock on standings.json")
                raise PermissionError("lock on races.json during rollback")

            buf = io.StringIO()
            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("os.replace", side_effect=fail_second_and_third_replace), \
                 contextlib.redirect_stdout(buf), \
                 contextlib.redirect_stderr(buf):
                rc = update_data.main(["--races-out", races_p, "--standings-out", standings_p, "--delay", "0"])

            out = buf.getvalue()
            self.assertEqual(rc, 1, "Debe retornar código de error 1 ante ROLLBACK_FAILED")
            mock_tg.assert_not_called()
            self.assertEqual(calls["n"], 3)

            files_left = os.listdir(tmp_dir)
            bak_files = [f for f in files_left if f.endswith(".bak")]
            tmp_files = [f for f in files_left if f.endswith(".tmp")]
            self.assertEqual(len(bak_files), 1, f"El respaldo .bak DEBE conservarse intacto; archivos actuales: {files_left}")
            self.assertEqual(tmp_files, [], "Los temporales .tmp no promovidos deben limpiarse")

            preserved_bak_path = os.path.join(tmp_dir, bak_files[0])
            with open(preserved_bak_path, "rb") as bfh:
                bak_sha = hashlib.sha256(bfh.read()).hexdigest()
            with open(standings_p, "rb") as sfh:
                st_sha = hashlib.sha256(sfh.read()).hexdigest()
            self.assertEqual(
                bak_sha,
                sha_r_before,
                "El respaldo .bak conservado debe ser idéntico byte a byte al races.json original",
            )
            self.assertEqual(st_sha, sha_s_before)
            self.assertIn("ROLLBACK_FAILED", out)
            self.assertIn(preserved_bak_path, out, "El log debe indicar la ruta exacta del respaldo para recuperación manual")
            self.assertNotIn("ROLLBACK_OK", out)
            self.assertNotIn("rollback compensatorio aplicado", out.lower())
            self.assertNotIn("actualizado y validado con éxito", out.lower())

        # Subcaso B: races.json no existía previamente y falla la eliminación compensatoria os.unlink(races_p)
        with tempfile.TemporaryDirectory() as tmp_dir:
            races_p = os.path.join(tmp_dir, "races.json")
            standings_p = os.path.join(tmp_dir, "standings.json")
            r_calls = {"n": 0}

            def fail_second_replace_new(src, dst):
                r_calls["n"] += 1
                if r_calls["n"] == 1:
                    return real_replace(src, dst)
                raise PermissionError("lock on new standings.json")

            def fail_unlink_on_races(path):
                if os.path.abspath(path) == os.path.abspath(races_p):
                    raise PermissionError("cannot unlink newly created races.json")
                return real_unlink(path)

            buf_b = io.StringIO()
            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("os.replace", side_effect=fail_second_replace_new), \
                 patch("os.unlink", side_effect=fail_unlink_on_races), \
                 contextlib.redirect_stdout(buf_b), \
                 contextlib.redirect_stderr(buf_b):
                rc_b = update_data.main(["--races-out", races_p, "--standings-out", standings_p, "--delay", "0"])

            out_b = buf_b.getvalue()
            self.assertEqual(rc_b, 1)
            mock_tg.assert_not_called()
            self.assertIn("ROLLBACK_FAILED", out_b)
            self.assertNotIn("ROLLBACK_OK", out_b)
            self.assertNotIn("rollback compensatorio aplicado", out_b.lower())

        # Subcaso C: Rollback exitoso -> debe informar ROLLBACK_OK y limpiar .bak
        with tempfile.TemporaryDirectory() as tmp_dir:
            races_p = os.path.join(tmp_dir, "races.json")
            standings_p = os.path.join(tmp_dir, "standings.json")
            with open(races_p, "wb") as fh:
                fh.write(b'[{"seed": 1}]')
            with open(standings_p, "wb") as fh:
                fh.write(b'{"seed": 2}')
            ok_rb_calls = {"n": 0}

            def fail_only_second(src, dst):
                ok_rb_calls["n"] += 1
                if ok_rb_calls["n"] == 2:
                    raise PermissionError("lock on standings.json")
                return real_replace(src, dst)

            buf_c = io.StringIO()
            with patch("update_data.send_telegram_message") as mock_tg, \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("os.replace", side_effect=fail_only_second), \
                 contextlib.redirect_stdout(buf_c), \
                 contextlib.redirect_stderr(buf_c):
                rc_c = update_data.main(["--races-out", races_p, "--standings-out", standings_p, "--delay", "0"])

            out_c = buf_c.getvalue()
            self.assertEqual(rc_c, 1)
            mock_tg.assert_not_called()
            self.assertIn("ROLLBACK_OK", out_c)
            self.assertNotIn("ROLLBACK_FAILED", out_c)
            self.assertEqual(sorted(os.listdir(tmp_dir)), ["races.json", "standings.json"])

    # -------------------------------------------------------------------------
    # 72. BACK-03B.1 (Defecto 2): Rechazo CLI de --notify-status PUBLISHED manual
    # -------------------------------------------------------------------------
    def test_72_cli_rejects_manual_notify_status_published(self):
        """
        Verifica la corrección del Defecto 2 (BACK-03B.1):
        - La CLI productiva rechaza '--notify-status PUBLISHED' con salida de error (!= 0) y 0 envíos Telegram.
        - Mantiene '--notify-from-ci' resolviendo exclusivamente el estado comprobado del entorno CI.
        """
        import contextlib
        import subprocess
        import sys
        import update_data

        # 1. Prueba directa sobre main(): argparse debe rechazar --notify-status con SystemExit != 0
        err_buf = io.StringIO()
        with patch("update_data.send_telegram_message") as mock_tg, \
             contextlib.redirect_stdout(err_buf), \
             contextlib.redirect_stderr(err_buf):
            with self.assertRaises(SystemExit) as ctx:
                update_data.main(["--notify-status", "PUBLISHED"])
        self.assertNotEqual(ctx.exception.code, 0)
        mock_tg.assert_not_called()

        # 2. Prueba real de subproceso sobre la CLI productiva (sin credenciales para impedir efectos externos)
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_data.py")
        clean_env = dict(os.environ)
        clean_env["TELEGRAM_BOT_TOKEN"] = ""
        clean_env["TELEGRAM_CHAT_ID"] = ""
        proc = subprocess.run(
            [sys.executable, script_path, "--notify-status", "PUBLISHED"],
            env=clean_env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0, "La CLI debe rechazar '--notify-status PUBLISHED'")
        self.assertIn("unrecognized arguments", (proc.stderr or "").lower())

        # 3. '--notify-from-ci' se mantiene operativo con estado CI comprobado
        ci_env = {
            "JOB_STATUS": "success",
            "STEP_PUBLISH": "success",
            "PUBLISH_RESULT": "PUBLISHED",
            "COMMIT_SHA": "deadbee",
            "RUN_URL": "https://github.com/example/run/99",
        }
        with patch.dict(os.environ, ci_env, clear=False), \
             patch("update_data.send_telegram_message", return_value=True) as mock_tg:
            rc_ci = update_data.main(["--notify-from-ci"])
        self.assertEqual(rc_ci, 0)
        mock_tg.assert_called_once()
        self.assertIn("deadbee", mock_tg.call_args[0][0])

    # -------------------------------------------------------------------------
    # 73. PREDEPLOY-01: parse_ics_calendar siempre emite el campo "locality" como str válido
    # -------------------------------------------------------------------------
    def test_73_parse_ics_calendar_always_emits_string_locality(self):
        """
        Verifica el contrato Python -> Gson/Android corregido en PREDEPLOY-01:
        - parse_ics_calendar() incluye explícitamente "locality": "" (str) en cada evento 2026,
          tanto en eventos con hora UTC como de día completo y con o sin país en LOCATION.
        - Se conservan intactos todos los demás campos esperados por validate_data.py y Android.
        """
        import json
        import update_data
        from validate_data import REQUIRED_CATEGORIES, validate_races_data

        sample_ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "SUMMARY:WRC - Rallye Monte-Carlo\r\n"
            "DTSTART:20260122T180000Z\r\n"
            "LOCATION:Monaco\r\n"
            "END:VEVENT\r\n"
            "BEGIN:VEVENT\r\n"
            "SUMMARY:WRC - Rally Sweden\r\n"
            "DTSTART;VALUE=DATE:20260212\r\n"
            "LOCATION:Umeå\\, Sweden\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ).encode("utf-8")

        expected_keys = {
            "id",
            "category",
            "name",
            "circuit",
            "country",
            "countryCode",
            "dateTimestamp",
            "round",
            "season",
            "isCompleted",
            "circuitId",
            "locality",
        }

        def fake_ics_urlopen(req, timeout=None):
            resp = MagicMock()
            resp.read.return_value = sample_ics
            return resp

        all_races = []
        with patch("update_data.send_telegram_message") as mock_tg, \
             patch("urllib.request.urlopen", side_effect=fake_ics_urlopen):
            for cat in REQUIRED_CATEGORIES:
                cat_races = update_data.parse_ics_calendar(cat, f"https://calendar.example.invalid/{cat.lower()}.ics")
                self.assertEqual(len(cat_races), 2)
                for item in cat_races:
                    self.assertEqual(set(item.keys()), expected_keys)
                    self.assertIn("locality", item)
                    self.assertIsInstance(item["locality"], str)
                    self.assertEqual(item["locality"], "")
                all_races.extend(cat_races)

        mock_tg.assert_not_called()
        roundtrip_races = json.loads(json.dumps(all_races, ensure_ascii=False))
        for item in roundtrip_races:
            self.assertIn("locality", item)
            self.assertIsInstance(item["locality"], str)
            self.assertEqual(item["locality"], "")

        val_res = validate_races_data(roundtrip_races, expected_season=2026)
        self.assertTrue(val_res.is_valid, f"El JSON emitido por parse_ics_calendar debe ser válido: {val_res.errors}")

if __name__ == "__main__":
    unittest.main()
