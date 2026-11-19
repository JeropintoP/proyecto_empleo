-- =====================================================================
-- 004 - Fuente Get on Board (API publica, sin key)
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/004_getonbrd.sql
-- =====================================================================

INSERT INTO fuentes (codigo, nombre, tipo) VALUES ('getonbrd', 'Get on Board', 'api')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('consultas_diarias_getonbrd', '6', 'Busquedas que se envian a Get on Board por corrida')
ON CONFLICT (clave) DO NOTHING;

-- Los perfiles que ya tenian busquedas generadas las reciben tambien para Get on Board
INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion, generada_por_ia)
SELECT DISTINCT b.perfil_id, g.id, b.palabras_clave, b.ubicacion, b.generada_por_ia
  FROM busquedas b
  CROSS JOIN (SELECT id FROM fuentes WHERE codigo = 'getonbrd') g
 WHERE b.fuente_id = (SELECT id FROM fuentes WHERE codigo = 'jooble')
   AND NOT EXISTS (SELECT 1 FROM busquedas x
                    WHERE x.perfil_id = b.perfil_id AND x.fuente_id = g.id
                      AND x.palabras_clave = b.palabras_clave);
