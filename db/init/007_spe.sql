-- =====================================================================
-- 007 - Fuente Servicio Publico de Empleo (buscadordeempleo.gov.co, publica, sin key)
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/007_spe.sql
-- =====================================================================

INSERT INTO fuentes (codigo, nombre, tipo) VALUES ('spe', 'Servicio Público de Empleo', 'api')
ON CONFLICT (codigo) DO NOTHING;

INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('spe_departamentos', 'BOLÍVAR', 'Departamentos que se leen del SPE, separados por coma, en mayusculas y con tilde (p. ej. BOLÍVAR,ATLÁNTICO)'),
    ('spe_max_paginas',   '20',      'Paginas de 50 vacantes que se leen como maximo por departamento y corrida')
ON CONFLICT (clave) DO NOTHING;

-- Los perfiles que ya tenian busquedas generadas las reciben tambien para el SPE
INSERT INTO busquedas (perfil_id, fuente_id, palabras_clave, ubicacion, generada_por_ia)
SELECT DISTINCT b.perfil_id, g.id, b.palabras_clave, b.ubicacion, b.generada_por_ia
  FROM busquedas b
  CROSS JOIN (SELECT id FROM fuentes WHERE codigo = 'spe') g
 WHERE b.fuente_id = (SELECT id FROM fuentes WHERE codigo = 'jooble')
   AND NOT EXISTS (SELECT 1 FROM busquedas x
                    WHERE x.perfil_id = b.perfil_id AND x.fuente_id = g.id
                      AND x.palabras_clave = b.palabras_clave);
