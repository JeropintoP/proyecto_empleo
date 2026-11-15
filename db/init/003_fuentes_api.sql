-- =====================================================================
-- 003 - Fuentes por API (Jooble, Careerjet): rotacion de busquedas y cupos diarios
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/003_fuentes_api.sql
-- =====================================================================

ALTER TABLE busquedas ADD COLUMN IF NOT EXISTS ultima_consulta TIMESTAMPTZ;
ALTER TABLE busquedas ADD COLUMN IF NOT EXISTS ultimo_resultado INTEGER;

-- Cupos conservadores: cada corrida consulta las N busquedas que hace mas tiempo no se ejecutan.
-- Jooble limita las consultas por key; con 2 usuarios y ~6 busquedas cada uno, 4 al dia
-- recorre todas en unos 3 dias.
INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('consultas_diarias_jooble',    '4', 'Busquedas que se envian a Jooble por corrida'),
    ('consultas_diarias_careerjet', '8', 'Busquedas que se envian a Careerjet por corrida')
ON CONFLICT (clave) DO NOTHING;
