-- =====================================================================
-- 009 - Experiencia separada por relacion con la carrera
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/009_experiencia_separada.sql
--
-- experiencias:            [{"cargo","empresa","desde","hasta","anios","tipo":"empleo|practica","relacionada":bool}]
-- anios_experiencia_area:  suma de las experiencias relacionadas con la(s) carrera(s)
-- carreras:                profesiones que aparecen en la hoja de vida (incluidas las en curso)
-- anios_experiencia (ya existia) sigue siendo la experiencia laboral total.
-- =====================================================================
ALTER TABLE perfiles ADD COLUMN IF NOT EXISTS experiencias           JSONB        NOT NULL DEFAULT '[]';
ALTER TABLE perfiles ADD COLUMN IF NOT EXISTS anios_experiencia_area NUMERIC(4,1);
ALTER TABLE perfiles ADD COLUMN IF NOT EXISTS carreras               TEXT[]       NOT NULL DEFAULT '{}';
