# Proyecto Empleo

Buscador personal de vacantes con perfilamiento por IA. Uso privado (Jerónimo Pinto y Helen Visbal).

## Estructura

```
proyecto_empleo/
├── index.html            # Página pública (GitHub Pages)
├── docker-compose.yml    # db + api + worker + web
├── .env.example          # Plantilla de variables (copiar como .env)
├── db/init/              # Esquema SQL (se ejecuta al crear la base)
└── backend/
    ├── app/              # API (FastAPI)
    │   ├── config.py     # Variables de entorno
    │   ├── db.py         # Conexión PostgreSQL
    │   ├── seguridad.py  # Claves (bcrypt) y tokens (JWT)
    │   ├── cli.py        # Administración (crear usuarios)
    │   └── rutas/        # Endpoints
    │   └── ia/           # Perfil, busquedas y puntaje con IA (Groq / Gemini)
    └── worker/           # Tarea diaria 6:00 a.m. (America/Bogota)
        └── correo/       # Lectores de alertas (LinkedIn, Computrabajo)
frontend/                 # Angular + Nginx (proxy /api)
```

## Puesta en marcha

```bash
cp .env.example .env          # completar claves
docker compose up -d --build
curl http://127.0.0.1:8000/api/salud
# Aplicacion web: http://localhost
```

Desarrollo del frontend (recarga en caliente, usa la API en :8000):

```bash
cd frontend && npm install && npx ng serve   # http://localhost:4200
```

Pruebas del backend:

```bash
cd backend && DATABASE_URL=postgresql+psycopg://... python -m pytest
```

Crear usuarios (la clave se pide por consola):

```bash
docker compose exec api python -m app.cli crear-usuario --nombre "Jerónimo Pinto" --email <correo> --alias jeronimopinto
docker compose exec api python -m app.cli crear-usuario --nombre "Helen Visbal"   --email <correo> --alias helenvisbal
```

Correr la actualización sin esperar a las 6 a.m.:

```bash
docker compose exec worker python -m worker.main --ahora
```

## Seguridad

El repositorio es público: **nunca** subir `.env` ni pegar claves en el código.
