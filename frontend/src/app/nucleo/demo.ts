/**
 * Modo demostracion: responde la API con datos de ejemplo en memoria.
 * Solo se activa al compilar con `ng build --configuration demo`; en produccion no se incluye.
 */
import { HttpInterceptorFn, HttpResponse } from '@angular/common/http';
import { delay, of, throwError } from 'rxjs';

import { Coincidencia, Empresa, Estado, NuevaEmpresa, Perfil } from './modelos';

function dias(n: number): string {
  return new Date(Date.now() - n * 86_400_000).toISOString();
}

function vacante(
  id: number, puntaje: number | null, titulo: string, empresa: string, ubicacion: string,
  extra: Partial<Coincidencia> = {},
): Coincidencia {
  return {
    id: `demo-${id}`, puntaje, titulo, empresa, ubicacion,
    explicacion: null, fortalezas: [], brechas: [], estado: 'nueva', notas: null,
    veces_mostrada: 1, creada_en: dias(id % 4), reaparecio_en: null, postulada_en: null,
    modalidad: null, salario_texto: null, url: 'https://co.computrabajo.com/', fecha_publicacion: dias(id % 4 + 1),
    abierta: true, portal: 'computrabajo', ...extra,
  };
}

// Los datos se crean en la primera peticion: asi el modulo no tiene efectos al importarse
// y el compilador lo elimina por completo de la version de produccion.
function crearDatos(): { vacantes: Coincidencia[]; perfil: Perfil } {
  const vacantes: Coincidencia[] = [
  vacante(1, 88, 'Software Integration and Automation Specialist', 'La Casa 1, LLC', 'Bogotá, D.C.', {
    explicacion: 'Integraciones y automatización con Python: muy alineado con su trabajo actual.',
    fortalezas: ['Integraciones entre sistemas', 'Python', 'Experiencia en sector salud'],
    brechas: ['Ubicación en Bogotá', 'Piden 3 años de experiencia'],
    salario_texto: '$ 4.500.000,00 (Mensual)',
  }),
  vacante(2, 84, 'Administrador de Sistemas Bilingüe', 'PeakU', 'Medellín, Antioquia', {
    explicacion: 'Administración de sistemas con inglés: encaja con su experiencia técnica y su B2.',
    fortalezas: ['Inglés B2', 'Bases de datos', 'Soporte de infraestructura'],
    brechas: ['Ubicación en Medellín'], veces_mostrada: 2, reaparecio_en: dias(0),
    salario_texto: '$ 5.000.000,00 (Mensual)',
  }),
  vacante(3, 81, 'Administrador de Bases de Datos Junior', 'Grupo Energético del Caribe', 'Cartagena, Bolívar', {
    explicacion: 'Cargo de DBA en Cartagena: coincide con su experiencia en PostgreSQL y DB2.',
    fortalezas: ['PostgreSQL avanzado', 'DB2', 'Misma ciudad'], brechas: ['Certificación deseable'],
    modalidad: 'hibrido', portal: 'linkedin', url: 'https://www.linkedin.com/jobs/',
    estado: 'guardada',
  }),
  vacante(4, 78, 'Ingeniero de Sistemas / Medio Tiempo', 'GRUPO EXPRO', 'Bogotá, D.C.', {
    explicacion: 'Cargo afín a su formación, aunque es medio tiempo y en Bogotá.',
    fortalezas: ['Ingeniería de sistemas'], brechas: ['Medio tiempo', 'Ubicación en Bogotá'],
    salario_texto: '$ 2.500.000,00 (Mensual)',
  }),
  vacante(5, 72, 'Analista de Datos', 'Clínica Regional del Norte', 'Barranquilla, Atlántico', {
    explicacion: 'Análisis de datos clínicos con SQL; buen punto de entrada al área de datos.',
    fortalezas: ['SQL', 'Conocimiento del sector salud'], brechas: ['Power BI avanzado'],
    modalidad: 'presencial', estado: 'postulada', postulada_en: dias(1),
    notas: 'Envié la hoja de vida por la página. Contacto: talento humano.',
  }),
  vacante(6, 66, 'Ingeniero de Soporte TI', 'IAFIS Group', 'Remoto', {
    explicacion: 'Soporte técnico de aplicaciones, modalidad remota.',
    fortalezas: ['Atención a usuarios', 'Resolución de incidentes'], brechas: ['Turnos rotativos'],
    modalidad: 'remoto', portal: 'linkedin', url: 'https://www.linkedin.com/jobs/',
  }),
  vacante(7, 55, 'Técnico en sistemas', 'Global SAS', 'Bogotá, D.C.', {
    explicacion: 'Rol técnico de sistemas, de nivel menor al de su experiencia.',
    fortalezas: ['Soporte técnico'], brechas: ['Nivel técnico, no profesional'],
    salario_texto: '$ 1.800.000,00 (Mensual)',
  }),
  vacante(8, 41, 'Project Engineering IV', 'Wood', 'Cartagena de Indias', {
    explicacion: 'Ingeniería de proyectos industriales, poco relacionada con TI.',
    fortalezas: ['Misma ciudad'], brechas: ['Área industrial/mecánica'],
    modalidad: 'presencial', portal: 'linkedin', url: 'https://www.linkedin.com/jobs/',
  }),
  vacante(9, 8, 'Mechanical Tech Prof V', 'Wood', 'Cartagena de Indias', {
    explicacion: 'Perfil mecánico: otra profesión.', brechas: ['Requiere ingeniería mecánica'],
    modalidad: 'presencial', portal: 'linkedin', url: 'https://www.linkedin.com/jobs/',
  }),
  vacante(10, null, 'Desarrollador Backend Python', 'Tecnología Costera S.A.S.', 'Cartagena, Bolívar', {
    modalidad: 'hibrido',
  }),
  vacante(11, 0, 'Vendedor de mostrador', 'Almacenes del Centro', 'Cartagena, Bolívar', {
    explicacion: 'Contiene una palabra excluida: vendedor', estado: 'descartada',
  }),
];

  const perfil: Perfil = {
  resumen: 'Ingeniero de sistemas con experiencia en bases de datos (PostgreSQL, DB2), integraciones entre sistemas y liderazgo de proyectos TIC en el sector salud.',
  cargos_objetivo: ['Administrador de bases de datos', 'Ingeniero de integración', 'Analista de datos', 'Ingeniero de soporte TI'],
  habilidades: [
    { nombre: 'PostgreSQL', nivel: 'avanzado' }, { nombre: 'SQL / DB2', nivel: 'avanzado' },
    { nombre: 'Integración de sistemas', nivel: 'intermedio' }, { nombre: 'Python', nivel: 'intermedio' },
    { nombre: 'Excel avanzado', nivel: 'avanzado' }, { nombre: 'Docker', nivel: 'basico' },
  ],
  anios_experiencia: 1.8,
  nivel_educativo: 'Profesional en Ingeniería de Sistemas y Computación',
  idiomas: [{ idioma: 'Inglés', nivel: 'B2' }],
  ciudades: ['Cartagena'], modalidad: 'indiferente', salario_minimo: 3_000_000,
  palabras_excluir: ['vendedor', 'call center'],
  cv_archivo: 'hoja_de_vida.pdf', modelo_ia: 'groq/openai/gpt-oss-120b', actualizado_en: dias(2),
  busquedas: [
    { palabras_clave: 'administrador de bases de datos', ubicacion: 'Cartagena' },
    { palabras_clave: 'DBA', ubicacion: 'Colombia' },
    { palabras_clave: 'ingeniero de integración', ubicacion: 'Colombia' },
    { palabras_clave: 'analista de datos', ubicacion: 'Cartagena' },
    { palabras_clave: 'soporte TI', ubicacion: 'Cartagena' },
  ],
  };
  return { vacantes, perfil };
}

let datos: ReturnType<typeof crearDatos> | null = null;
let actualizacionHasta = 0;   // simula una actualizacion de ~6 segundos
let nuevasUltima = 0;

function estadoActualizacion() {
  const enCurso = Date.now() < actualizacionHasta;
  const fin = new Date(enCurso ? Date.now() - 86_400_000 : actualizacionHasta || Date.now() - 3_600_000).toISOString();
  return {
    en_curso: enCurso,
    fuentes: [
      { codigo: 'jooble', nombre: 'Jooble', estado: null, fin: null, vacantes_nuevas: null, error: null, configurada: false },
      { codigo: 'careerjet', nombre: 'Careerjet', estado: null, fin: null, vacantes_nuevas: null, error: null, configurada: false },
      { codigo: 'correo', nombre: 'Alertas por correo', estado: enCurso ? 'en_curso' : 'ok', fin,
        vacantes_nuevas: enCurso ? null : nuevasUltima, error: null, configurada: true },
    ],
  };
}

let empresasDemo: Empresa[] = [
  { id: 1, nombre: 'Astivik', tipo: 'pagina', identificador: 'https://astivik.buk.co/trabaja-con-nosotros', pais: null,
    activa: true, solo_para_mi: false, ultima_revision: dias(0), ultimo_total: 3, ultimo_error: null },
  { id: 2, nombre: 'Konecranes', tipo: 'smartrecruiters', identificador: 'konecranes', pais: 'co',
    activa: true, solo_para_mi: false, ultima_revision: dias(0), ultimo_total: 1, ultimo_error: null },
];

const responder = <T>(cuerpo: T, ms = 250) => of(new HttpResponse({ status: 200, body: cuerpo })).pipe(delay(ms));
const normalizar = (t: string) => t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

export const interceptorDemo: HttpInterceptorFn = (req, next) => {
  datos ??= crearDatos();
  const VACANTES = datos.vacantes;
  const url = req.url;
  const params = req.params;

  if (url.endsWith('/api/auth/login')) {
    return responder({ token: 'demo', nombre: 'Jerónimo Pinto' }, 400);
  }

  if (url.endsWith('/api/resumen')) {
    const por_estado: Partial<Record<Estado, number>> = {};
    for (const v of VACANTES) por_estado[v.estado] = (por_estado[v.estado] ?? 0) + 1;
    return responder({
      por_estado, sin_evaluar: VACANTES.filter((v) => v.puntaje === null).length,
      ultima_actualizacion: new Date(new Date().setHours(6, 0, 0, 0)).toISOString(),
    });
  }

  if (url.endsWith('/api/coincidencias') && req.method === 'GET') {
    const estado = params.get('estado');
    const min = Number(params.get('min_puntaje') ?? 0);
    const q = normalizar(params.get('buscar') ?? '');
    let lista = VACANTES.filter((v) => (estado ? v.estado === estado : v.estado !== 'descartada'))
      .filter((v) => (v.puntaje ?? 0) >= min)
      .filter((v) => !q || normalizar(`${v.titulo} ${v.empresa} ${v.ubicacion}`).includes(q));
    lista = params.get('orden') === 'fecha'
      ? [...lista].sort((a, b) => b.creada_en.localeCompare(a.creada_en))
      : [...lista].sort((a, b) => (b.puntaje ?? -1) - (a.puntaje ?? -1));
    return responder({ total: lista.length, items: lista.map((v) => ({ ...v })) });
  }

  const cambio = url.match(/\/api\/coincidencias\/([^/]+)$/);
  if (cambio && req.method === 'PATCH') {
    const v = VACANTES.find((x) => x.id === cambio[1]);
    if (!v) return throwError(() => ({ status: 404 }));
    const body = req.body as { estado?: Estado; notas?: string };
    if (body.estado) {
      v.estado = body.estado;
      if (body.estado === 'postulada') v.postulada_en = new Date().toISOString();
    }
    if (body.notas !== undefined) v.notas = body.notas;
    return responder({ estado: v.estado, notas: v.notas }, 150);
  }

  if (url.endsWith('/api/yo/clave')) {
    return responder(null, 400);
  }

  if (url.endsWith('/api/actualizar')) {
    if (req.method === 'POST' && Date.now() >= actualizacionHasta) {
      actualizacionHasta = Date.now() + 6000;
      nuevasUltima = 1;
      setTimeout(() => {
        VACANTES.unshift(vacante(20 + VACANTES.length, 86, 'Ingeniero de Datos Junior', 'Puerto Digital S.A.S.',
          'Cartagena, Bolívar', {
            explicacion: 'SQL, integraciones y Python en Cartagena: muy alineado con su perfil.',
            fortalezas: ['SQL', 'Integraciones', 'Misma ciudad'], brechas: ['Airflow deseable'],
            modalidad: 'hibrido', creada_en: new Date().toISOString(),
          }));
      }, 5500);
    }
    return responder(estadoActualizacion());
  }

  if (url.endsWith('/api/empresas/probar')) {
    const d = req.body as NuevaEmpresa;
    return responder({ total: 2, error: null, vacantes: [
      { titulo: 'Ingeniero mecánico de mantenimiento', url: '#', ubicacion: 'Cartagena' },
      { titulo: `Inspector de calidad - ${d.nombre}`, url: '#', ubicacion: null },
    ] }, 900);
  }

  const empresa = url.match(/\/api\/empresas\/(\d+)$/);
  if (empresa) {
    const id = Number(empresa[1]);
    if (req.method === 'DELETE') {
      empresasDemo = empresasDemo.filter((e) => e.id !== id);
      return responder(null, 150);
    }
    const e = empresasDemo.find((x) => x.id === id);
    if (e) e.activa = (req.body as { activa: boolean }).activa;
    return responder(e, 150);
  }

  if (url.endsWith('/api/empresas')) {
    if (req.method === 'POST') {
      const d = req.body as NuevaEmpresa;
      const nueva: Empresa = { ...d, id: Date.now(), activa: true, ultima_revision: null, ultimo_total: null, ultimo_error: null };
      empresasDemo.push(nueva);
      return responder(nueva, 300);
    }
    return responder(empresasDemo.map((e) => ({ ...e })));
  }

  if (url.endsWith('/api/perfil/cv')) {
    const archivo = (req.body as FormData).get('archivo');
    datos.perfil = { ...datos.perfil, actualizado_en: new Date().toISOString(),
      cv_archivo: archivo instanceof File ? archivo.name : datos.perfil.cv_archivo };
    return responder(datos.perfil, 2500);
  }

  if (url.endsWith('/api/perfil')) {
    if (req.method === 'PUT') {
      datos.perfil = { ...datos.perfil, ...(req.body as Partial<Perfil>), actualizado_en: new Date().toISOString() };
    }
    return responder(datos.perfil);
  }

  return next(req);
};
