import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from studio import backlog
from studio.errors import StudioError


def tema(slug, titulo, estado="pendiente", **extra):
    base = {"slug": slug, "titulo": titulo, "estado": estado}
    base.update(extra)
    return base


def datos(*temas):
    return backlog.validate({"version": backlog.BACKLOG_VERSION, "temas": list(temas)})


class TestSiguiente(unittest.TestCase):
    def test_devuelve_el_primero_pendiente(self):
        d = datos(
            tema("a", "Alfa", "usado", modo="corto", historias=["a"]),
            tema("b", "Beta"),
            tema("c", "Gamma"),
        )
        self.assertEqual(backlog.siguiente(d)["slug"], "b")

    def test_el_orden_del_archivo_es_la_prioridad(self):
        d = datos(tema("z", "Zeta"), tema("a", "Alfa"))
        self.assertEqual(backlog.siguiente(d)["slug"], "z")

    def test_backlog_agotado_dice_como_rellenarlo(self):
        d = datos(tema("a", "Alfa", "usado", modo="corto", historias=["a"]))
        with self.assertRaises(StudioError) as ctx:
            backlog.siguiente(d)
        self.assertIn("story_topics.py --generar", str(ctx.exception))

    def test_backlog_vacio_tambien(self):
        with self.assertRaises(StudioError):
            backlog.siguiente(backlog.empty())


class TestMarcarUsado(unittest.TestCase):
    """Un tema se consume UNA vez, y solo cuando el guion ya existe."""

    def test_registra_modo_historias_y_fecha(self):
        d = datos(tema("a", "Alfa"))
        backlog.marcar_usado(d, "a", "extendido", ["a-1", "a-2", "a-3"], fecha="2026-08-11")
        entrada = backlog.buscar(d, "a")
        self.assertEqual(entrada["estado"], "usado")
        self.assertEqual(entrada["modo"], "extendido")
        self.assertEqual(entrada["historias"], ["a-1", "a-2", "a-3"])
        self.assertEqual(entrada["fecha"], "2026-08-11")

    def test_pone_fecha_de_hoy_si_no_se_pasa(self):
        d = datos(tema("a", "Alfa"))
        backlog.marcar_usado(d, "a", "corto", ["a"])
        self.assertTrue(backlog.buscar(d, "a")["fecha"])

    def test_rechaza_un_modo_inventado(self):
        d = datos(tema("a", "Alfa"))
        with self.assertRaises(StudioError):
            backlog.marcar_usado(d, "a", "mediano", ["a"])

    def test_rechaza_marcar_sin_historias(self):
        d = datos(tema("a", "Alfa"))
        with self.assertRaises(StudioError):
            backlog.marcar_usado(d, "a", "corto", [])

    def test_no_se_puede_consumir_dos_veces(self):
        d = datos(tema("a", "Alfa"))
        backlog.marcar_usado(d, "a", "corto", ["a"])
        with self.assertRaises(StudioError) as ctx:
            backlog.marcar_usado(d, "a", "extendido", ["a-1"])
        # el error tiene que decir cuándo y cómo se usó, no solo que falló
        self.assertIn("corto", str(ctx.exception))

    def test_un_slug_desconocido_falla(self):
        with self.assertRaises(StudioError):
            backlog.marcar_usado(datos(), "fantasma", "corto", ["x"])


class TestValidacion(unittest.TestCase):
    def test_rechaza_slugs_duplicados(self):
        with self.assertRaises(StudioError):
            datos(tema("a", "Alfa"), tema("a", "Otro"))

    def test_rechaza_slug_con_separadores(self):
        # el slug nombra un directorio bajo storage/
        with self.assertRaises(StudioError):
            datos(tema("../fuera", "Alfa"))

    def test_rechaza_usado_sin_modo(self):
        with self.assertRaises(StudioError):
            datos(tema("a", "Alfa", "usado"))

    def test_rechaza_categoria_inventada(self):
        with self.assertRaises(StudioError):
            datos(tema("a", "Alfa", categoria="receta-de-cocina"))

    def test_rellena_los_campos_opcionales(self):
        d = datos(tema("a", "Alfa"))
        entrada = d["temas"][0]
        self.assertEqual(entrada["historias"], [])
        self.assertIsNone(entrada["modo"])
        self.assertEqual(entrada["epoca"], "")

    def test_rechaza_version_desconocida(self):
        with self.assertRaises(StudioError):
            backlog.validate({"version": 99, "temas": []})


class TestDuplicados(unittest.TestCase):
    """Conservador a propósito: pocos falsos positivos, la curación hace el resto."""

    def test_atrapa_una_reformulacion(self):
        choque = backlog.es_duplicado(
            "El caballo al que Calígula nombró cónsul",
            ["Calígula nombró cónsul a su caballo"],
        )
        self.assertIsNotNone(choque)

    def test_ignora_los_acentos(self):
        self.assertIsNotNone(
            backlog.es_duplicado("La caida de Tenochtitlan roja", ["La caída de Tenochtitlán roja"])
        )

    def test_una_sola_palabra_compartida_no_basta(self):
        # 'Roma' aparece en decenas de temas legítimamente distintos
        self.assertIsNone(
            backlog.es_duplicado("Los baños de Roma", ["La caída de Roma"])
        )

    def test_temas_sin_relacion_no_chocan(self):
        self.assertIsNone(
            backlog.es_duplicado("La plaga del baile de 1518", ["El paraguas búlgaro"])
        )

    def test_parecidos_avisa_de_la_entidad_compartida(self):
        avisos = backlog.parecidos("Los baños de Tenochtitlan", ["La caída de Tenochtitlan"])
        self.assertEqual(len(avisos), 1)
        self.assertIn("tenochtitlan", avisos[0][1])

    def test_parecidos_no_repite_lo_que_ya_es_duplicado(self):
        # si es_duplicado ya lo marcó, no tiene sentido avisarlo otra vez
        self.assertEqual(
            backlog.parecidos(
                "Calígula nombró cónsul a su caballo",
                ["Calígula nombró cónsul a su caballo"],
            ),
            [],
        )


class TestExclusiones(unittest.TestCase):
    def test_incluye_pendientes_usados_y_las_historias_ya_escritas(self):
        d = datos(
            tema("a", "Alfa", "usado", modo="corto", historias=["a"]),
            tema("b", "Beta"),
        )
        lista = backlog.exclusiones(d, ["La caída de Tenochtitlan"])
        self.assertIn("Alfa", lista)
        self.assertIn("Beta", lista)
        self.assertIn("La caída de Tenochtitlan", lista)

    def test_no_repite_titulos(self):
        d = datos(tema("a", "Alfa"))
        self.assertEqual(backlog.exclusiones(d, ["Alfa"]).count("Alfa"), 1)


class TestSlug(unittest.TestCase):
    def test_quita_acentos_y_palabras_vacias(self):
        self.assertEqual(backlog.slug_de("La caída de Tenochtitlán"), "caida-tenochtitlan")

    def test_se_queda_con_las_primeras_palabras(self):
        slug = backlog.slug_de("Uno dos tres cuatro cinco seis siete")
        self.assertEqual(slug.count("-"), 3)

    def test_evita_colisiones(self):
        self.assertEqual(
            backlog.slug_de("La caída de Tenochtitlán", ocupados={"caida-tenochtitlan"}),
            "caida-tenochtitlan-2",
        )

    def test_un_titulo_sin_palabras_utiles_no_revienta(self):
        self.assertTrue(backlog.slug_de("de la el"))


class TestReparto(unittest.TestCase):
    def test_cuenta_solo_los_usados(self):
        d = datos(
            tema("a", "Alfa", "usado", modo="corto", historias=["a"],
                 categoria="personaje-historico", epoca="Roma antigua"),
            tema("b", "Beta", "usado", modo="corto", historias=["b"],
                 categoria="personaje-historico", epoca="Roma antigua"),
            tema("c", "Gamma", categoria="evento-mitologico", epoca="Grecia"),
        )
        r = backlog.reparto(d)
        self.assertEqual(r["categoria"]["personaje-historico"], 2)
        self.assertEqual(r["epoca"]["Roma antigua"], 2)
        self.assertNotIn("Grecia", r["epoca"])


class TestDisco(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ruta = os.path.join(self.dir, "backlog.json")

    def test_un_archivo_inexistente_es_un_backlog_vacio(self):
        self.assertEqual(backlog.load(self.ruta)["temas"], [])

    def test_ida_y_vuelta(self):
        d = datos(tema("a", "Alfa", categoria="evento-historico", epoca="Edad Media"))
        backlog.save(self.ruta, d)
        recuperado = backlog.load(self.ruta)
        self.assertEqual(recuperado["temas"][0]["epoca"], "Edad Media")

    def test_json_corrupto_lo_dice_con_la_linea(self):
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write("{roto")
        with self.assertRaises(StudioError) as ctx:
            backlog.load(self.ruta)
        self.assertIn("línea", str(ctx.exception))


class TestIntercalar(unittest.TestCase):
    """El modelo devuelve los temas agrupados por categoría; hay que repartirlos."""

    def entrada(self):
        return (
            [tema(f"h{i}", f"Histórico {i}", categoria="personaje-historico") for i in range(4)]
            + [tema(f"e{i}", f"Evento {i}", categoria="evento-historico") for i in range(3)]
            + [tema(f"m{i}", f"Mito {i}", categoria="personaje-mitologico") for i in range(2)]
        )

    def test_no_deja_dos_seguidos_de_la_misma_categoria(self):
        salida = backlog.intercalar(self.entrada())
        categorias = [t["categoria"] for t in salida]
        seguidos = [
            (a, b) for a, b in zip(categorias, categorias[1:]) if a == b
        ]
        # solo puede repetirse al final, cuando ya se agotaron las demás
        self.assertLessEqual(len(seguidos), 1, f"demasiados seguidos: {categorias}")

    def test_no_pierde_ni_duplica_temas(self):
        entrada = self.entrada()
        salida = backlog.intercalar(entrada)
        self.assertEqual(sorted(t["slug"] for t in salida),
                         sorted(t["slug"] for t in entrada))

    def test_conserva_el_orden_dentro_de_cada_categoria(self):
        salida = backlog.intercalar(self.entrada())
        miticos = [t["slug"] for t in salida if t["categoria"] == "personaje-mitologico"]
        self.assertEqual(miticos, ["m0", "m1"])

    def test_una_sola_categoria_se_devuelve_intacta(self):
        entrada = [tema("a", "Alfa", categoria="evento-historico"),
                   tema("b", "Beta", categoria="evento-historico")]
        self.assertEqual([t["slug"] for t in backlog.intercalar(entrada)], ["a", "b"])

    def test_lista_vacia(self):
        self.assertEqual(backlog.intercalar([]), [])


class TestMotivo(unittest.TestCase):
    """El eje que importa es la moraleja, no la taxonomía.

    'El rey Midas' y 'El monstruo Fafnir' son los dos personajes mitológicos
    —misma categoría— y cuentan la misma historia: la codicia por el oro
    arruinando a alguien. Repartir por categoría los dejaría seguidos.
    """

    def test_el_motivo_manda_sobre_la_categoria(self):
        self.assertEqual(
            backlog.clave_reparto({"motivo": "codicia", "categoria": "personaje-mitologico"}),
            "codicia",
        )

    def test_sin_motivo_se_cae_a_la_categoria(self):
        self.assertEqual(
            backlog.clave_reparto({"categoria": "evento-historico"}), "evento-historico"
        )

    def test_separa_dos_de_la_misma_categoria_con_motivos_distintos(self):
        entrada = [
            tema("midas", "Midas", categoria="personaje-mitologico", motivo="codicia"),
            tema("fafnir", "Fafnir", categoria="personaje-mitologico", motivo="codicia"),
            tema("loki", "Loki", categoria="personaje-mitologico", motivo="engano"),
        ]
        orden = [t["slug"] for t in backlog.intercalar(entrada)]
        # el de engaño tiene que quedar entre los dos de codicia
        self.assertEqual(orden.index("loki"), 1)

    def test_racimos_ordena_por_repeticion(self):
        entrada = [
            tema("a", "A", motivo="engano"), tema("b", "B", motivo="engano"),
            tema("c", "C", motivo="engano"), tema("d", "D", motivo="codicia"),
            tema("e", "E", motivo="codicia"), tema("f", "F", motivo="venganza"),
        ]
        agrupados = backlog.racimos(entrada)
        self.assertEqual([m for m, _ in agrupados], ["engano", "codicia"])
        self.assertEqual(len(agrupados[0][1]), 3)

    def test_racimos_ignora_los_que_no_se_repiten(self):
        entrada = [tema("a", "A", motivo="engano"), tema("b", "B", motivo="codicia")]
        self.assertEqual(backlog.racimos(entrada), [])

    def test_racimos_ignora_los_temas_sin_motivo(self):
        entrada = [tema("a", "A"), tema("b", "B"), tema("c", "C")]
        self.assertEqual(backlog.racimos(entrada), [])

    def test_el_reparto_cuenta_tambien_el_motivo(self):
        d = datos(
            tema("a", "A", "usado", modo="corto", historias=["a"], motivo="codicia"),
            tema("b", "B", "usado", modo="corto", historias=["b"], motivo="codicia"),
        )
        self.assertEqual(backlog.reparto(d)["motivo"]["codicia"], 2)


class TestAnadir(unittest.TestCase):
    def test_salta_los_slugs_que_ya_estaban(self):
        d = datos(tema("a", "Alfa"))
        nuevos = backlog.anadir(d, [tema("a", "Alfa otra vez"), tema("b", "Beta")])
        self.assertEqual([t["slug"] for t in nuevos], ["b"])
        self.assertEqual(len(d["temas"]), 2)


if __name__ == "__main__":
    unittest.main()
