-- =====================================================================
-- 005 - Fuentes Himalayas y Jobicy (APIs publicas, sin key, en ingles)
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/005_himalayas_jobicy.sql
-- =====================================================================

-- Version en ingles de cada busqueda (la genera la IA); la usan las fuentes internacionales
ALTER TABLE busquedas ADD COLUMN IF NOT EXISTS palabras_clave_en VARCHAR(200);

INSERT INTO fuentes (codigo, nombre, tipo) VALUES
    ('himalayas', 'Himalayas', 'api'),
    ('jobicy',    'Jobicy',    'api')
ON CONFLICT (codigo) DO NOTHING;

-- Himalayas: N busquedas por corrida (rotando). Jobicy: siempre 1 sola consulta al dia
-- (sus terminos piden no consultar mas de una vez por hora); se filtra localmente.
INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('consultas_diarias_himalayas', '4', 'Busquedas que se envian a Himalayas por corrida')
ON CONFLICT (clave) DO NOTHING;

-- Los perfiles existentes reciben sus busquedas tambien para estas fuentes
INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion, generada_por_ia)
SELECT DISTINCT b.perfil_id, f.id, b.palabras_clave, b.ubicacion, b.generada_por_ia
  FROM busquedas b
  CROSS JOIN (SELECT id FROM fuentes WHERE codigo IN ('himalayas', 'jobicy')) f
 WHERE b.fuente_id = (SELECT id FROM fuentes WHERE codigo = 'jooble')
   AND NOT EXISTS (SELECT 1 FROM busquedas x
                    WHERE x.perfil_id = b.perfil_id AND x.fuente_id = f.id
                      AND x.palabras_clave = b.palabras_clave);
