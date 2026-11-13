"""Herramientas de administracion.

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("crear-usuario")
    p.add_argument("--nombre", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--alias", required=True, help="Parte despues del + en el correo de alertas")

    args = parser.parse_args()
    if args.comando == "crear-usuario":
        crear_usuario(args.nombre, args.email, args.alias)


if __name__ == "__main__":
    main()
