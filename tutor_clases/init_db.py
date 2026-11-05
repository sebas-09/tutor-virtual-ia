"""Crea las tablas ORM en una base MySQL vacía de demostración."""
import argparse

from db import engine
from models import Base

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crear-esquema", action="store_true",
                        help="Autoriza crear tablas faltantes en tutor_demo.")
    args = parser.parse_args()
    if not args.crear_esquema:
        parser.error("Es necesario --crear-esquema.")
    if engine.url.database != "tutor_demo":
        parser.error("Esta herramienta solo admite la base tutor_demo.")
    Base.metadata.create_all(engine)
    print("Esquema de demostración creado; no se importaron datos originales.")

if __name__ == "__main__":
    main()
