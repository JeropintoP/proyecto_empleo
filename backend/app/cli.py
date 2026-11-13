"""Herramientas de administracion.

Cargar CV, generar perfil y busquedas con IA:
    docker compose exec api python -m app.cli cargar-cv --alias jeronimopinto --archivo /srv/cv/cv.pdf

Ajustar preferencias:
    docker compose exec api python -m app.cli preferencias --alias jeronimopinto \
        --ciudades "Cartagena" "Barranquilla" --modalidad indiferente --salario 3000000 \
        --excluir "vendedor" "call center"

Crear usuario (pide la clave por consola, no queda en el historial):
    docker compose exec api python -m app.cli crear-usuario \
        --nombre "Jeronimo Pinto" --email correo@ejemplo.com --alias jeronimopinto
"""
import argparse
import getpass

from sqlalchemy import text

from app.db import sesion_transaccion
from app.seguridad import hash_clave


def crear_usuario(nombre: str, email: str, alias: str) -> None:
    clave = getpass.getpass("Clave: ")
    if len(clave) < 10:
        raise SystemExit("La clave debe tener al menos 10 caracteres.")
    if clave != getpass.getpass("Repita la clave: "):
        raise SystemExit("Las claves no coinciden.")

    with sesion_transaccion() as s:
        usuario_id = s.execute(
            text("""
                INSERT INTO usuarios (nombre, email, password_hash, alias_correo)
                VALUES (:nombre, lower(:email), :hash, lower(:alias))
                RETURNING id
            """),
            {"nombre": nombre, "email": email, "hash": hash_clave(clave), "alias": alias},
        ).scalar_one()
        s.execute(text("INSERT INTO perfiles (usuario_id) VALUES (:id)"), {"id": usuario_id})

    print(f"Usuario creado: {nombre} <{email}>  alias: {alias}  id: {usuario_id}")


def _usuario_por_alias(alias: str) -> str:
    with sesion_transaccion() as s:
        uid = s.execute(text("SELECT id FROM usuarios WHERE alias_correo = :a"), {"a": alias}).scalar()
    if uid is None:
        raise SystemExit(f"No existe usuario con alias {alias}")
    return uid


def cargar_cv(alias: str, archivo: str) -> None:
    from app.ia.cv import texto_de_cv
    from app.ia.servicios import generar_busquedas, perfilar_cv

    uid = _usuario_por_alias(alias)
    texto = texto_de_cv(archivo)
    print(f"Texto extraido: {len(texto)} caracteres. Analizando con IA...")
    perfil = perfilar_cv(uid, texto, archivo)
    print("\nResumen:", perfil.get("resumen"))
    print("Cargos objetivo:", ", ".join(perfil.get("cargos_objetivo") or []))
    print("Habilidades:", ", ".join(h.get("nombre", "") for h in perfil.get("habilidades") or []))
    print("Experiencia (anios):", perfil.get("anios_experiencia"))
    busquedas = generar_busquedas(uid)
    print("\nBusquedas generadas:")
    for b in busquedas:
        print(f"  - {b['palabras_clave']}  ({b.get('ubicacion')})")


def preferencias(alias: str, ciudades, modalidad, salario, excluir) -> None:
    uid = _usuario_por_alias(alias)
    campos, valores = [], {"u": uid}
    if ciudades is not None:
        campos.append("ciudades = :ciudades"); valores["ciudades"] = ciudades
    if modalidad is not None:
        campos.append("modalidad = CAST(:modalidad AS modalidad_trabajo)"); valores["modalidad"] = modalidad
    if salario is not None:
        campos.append("salario_minimo = :salario"); valores["salario"] = salario
    if excluir is not None:
        campos.append("palabras_excluir = :excluir"); valores["excluir"] = excluir
    if not campos:
        raise SystemExit("Nada que actualizar")
    with sesion_transaccion() as s:
        s.execute(text(f"UPDATE perfiles SET {', '.join(campos)}, actualizado_en = now() WHERE usuario_id = :u"),
                  valores)
    print("Preferencias actualizadas.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("crear-usuario")
    p.add_argument("--nombre", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--alias", required=True, help="Parte despues del + en el correo de alertas")

    p = sub.add_parser("cargar-cv")
    p.add_argument("--alias", required=True)
    p.add_argument("--archivo", required=True, help="Ruta al CV (PDF, DOCX o TXT)")

    p = sub.add_parser("preferencias")
    p.add_argument("--alias", required=True)
    p.add_argument("--ciudades", nargs="*")
    p.add_argument("--modalidad", choices=["presencial", "remoto", "hibrido", "indiferente"])
    p.add_argument("--salario", type=int, help="Salario minimo mensual en COP")
    p.add_argument("--excluir", nargs="*", help="Palabras que descartan una vacante")

    args = parser.parse_args()
    if args.comando == "crear-usuario":
        crear_usuario(args.nombre, args.email, args.alias)
    elif args.comando == "cargar-cv":
        cargar_cv(args.alias, args.archivo)
    elif args.comando == "preferencias":
        preferencias(args.alias, args.ciudades, args.modalidad, args.salario, args.excluir)


if __name__ == "__main__":
    main()
