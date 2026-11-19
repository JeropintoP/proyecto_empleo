import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { Api } from '../../nucleo/api';
import { Coincidencia, Estado, EstadoActualizacion, FiltrosTablero, Resumen } from '../../nucleo/modelos';
import { mensajeError } from '../../nucleo/sesion';

const PESTANAS: { valor: Estado | ''; etiqueta: string }[] = [
  { valor: '', etiqueta: 'Activas' },
  { valor: 'nueva', etiqueta: 'Nuevas' },
  { valor: 'guardada', etiqueta: 'Guardadas' },
  { valor: 'postulada', etiqueta: 'Postuladas' },
  { valor: 'descartada', etiqueta: 'Descartadas' },
];
const TAMANO = 30;

// Nombre y sitio de cada fuente. Himalayas y Jobicy exigen credito visible con enlace.
const PORTALES: Record<string, { nombre: string; sitio: string }> = {
  linkedin: { nombre: 'LinkedIn', sitio: 'https://www.linkedin.com/jobs/' },
  computrabajo: { nombre: 'Computrabajo', sitio: 'https://co.computrabajo.com/' },
  elempleo: { nombre: 'Elempleo', sitio: 'https://www.elempleo.com/' },
  getonbrd: { nombre: 'Get on Board', sitio: 'https://www.getonbrd.com/' },
  jooble: { nombre: 'Jooble', sitio: 'https://co.jooble.org/' },
  careerjet: { nombre: 'Careerjet', sitio: 'https://www.careerjet.com.co/' },
  himalayas: { nombre: 'Himalayas', sitio: 'https://himalayas.app/' },
  jobicy: { nombre: 'Jobicy', sitio: 'https://jobicy.com/' },
};

@Component({
  selector: 'app-tablero',
  imports: [FormsModule, DatePipe, DecimalPipe],
  templateUrl: './tablero.html',
  styleUrl: './tablero.css',
})
export class Tablero implements OnInit, OnDestroy {
  private readonly api = inject(Api);

  readonly pestanas = PESTANAS;
  readonly filtros = signal<FiltrosTablero>({ estado: '', min_puntaje: 0, buscar: '', orden: 'puntaje' });
  readonly items = signal<Coincidencia[]>([]);
  readonly total = signal(0);
  readonly resumen = signal<Resumen | null>(null);
  readonly cargando = signal(false);
  readonly error = signal('');
  readonly abierta = signal<string | null>(null);
  readonly hayMas = computed(() => this.items().length < this.total());
  readonly actualizacion = signal<EstadoActualizacion | null>(null);
  readonly avisoActualizacion = signal('');
  readonly actualizando = computed(() => this.actualizacion()?.en_curso ?? false);

  private sondeo?: ReturnType<typeof setInterval>;

  private temporizador?: ReturnType<typeof setTimeout>;

  ngOnInit(): void {
    this.cargar();
    this.consultarActualizacion();
  }

  ngOnDestroy(): void {
    clearInterval(this.sondeo);
  }

  async consultarActualizacion(): Promise<void> {
    try {
      const estado = await firstValueFrom(this.api.estadoActualizacion());
      const termino = this.actualizando() && !estado.en_curso;
      this.actualizacion.set(estado);
      if (estado.en_curso) this.iniciarSondeo();
      if (termino) {
        clearInterval(this.sondeo);
        this.sondeo = undefined;
        const nuevas = estado.fuentes.reduce((s, f) => s + (f.vacantes_nuevas ?? 0), 0);
        this.avisoActualizacion.set(nuevas === 0 ? 'Actualización terminada: no hay vacantes nuevas.'
          : `Actualización terminada: ${nuevas} ${nuevas === 1 ? 'vacante nueva' : 'vacantes nuevas'}.`);
        this.cargar();
      }
    } catch {
      /* el estado es informativo: si falla, el tablero sigue funcionando */
    }
  }

  async actualizarAhora(): Promise<void> {
    this.avisoActualizacion.set('');
    this.error.set('');
    try {
      this.actualizacion.set(await firstValueFrom(this.api.actualizarAhora()));
      this.iniciarSondeo();
    } catch (e) {
      this.error.set(mensajeError(e));
    }
  }

  private iniciarSondeo(): void {
    this.sondeo ??= setInterval(() => this.consultarActualizacion(), 4000);
  }

  textoFuente(f: { estado: string | null; configurada: boolean; vacantes_nuevas: number | null; error: string | null }): string {
    if (!f.configurada) return 'sin configurar';
    if (f.estado === 'en_curso') return 'actualizando…';
    if (f.estado === 'error') return 'con error';
    if (f.estado === 'ok') return f.vacantes_nuevas === 1 ? '1 nueva' : `${f.vacantes_nuevas ?? 0} nuevas`;
    return 'sin datos';
  }

  conteo(estado: Estado | ''): number | null {
    const r = this.resumen();
    if (!r) return null;
    if (estado === '') {
      return Object.entries(r.por_estado)
        .filter(([e]) => e !== 'descartada')
        .reduce((s, [, n]) => s + (n ?? 0), 0);
    }
    return r.por_estado[estado] ?? 0;
  }

  cambiarFiltro(cambio: Partial<FiltrosTablero>, demora = 0): void {
    this.filtros.update((f) => ({ ...f, ...cambio }));
    clearTimeout(this.temporizador);
    this.temporizador = setTimeout(() => this.cargar(), demora);
  }

  async cargar(mas = false): Promise<void> {
    this.cargando.set(true);
    this.error.set('');
    try {
      const desde = mas ? this.items().length : 0;
      const [pagina, resumen] = await Promise.all([
        firstValueFrom(this.api.coincidencias(this.filtros(), TAMANO, desde)),
        mas ? Promise.resolve(this.resumen()) : firstValueFrom(this.api.resumen()),
      ]);
      this.items.set(mas ? [...this.items(), ...pagina.items] : pagina.items);
      this.total.set(pagina.total);
      this.resumen.set(resumen);
    } catch (e) {
      this.error.set(mensajeError(e));
    } finally {
      this.cargando.set(false);
    }
  }

  alternar(c: Coincidencia): void {
    this.abierta.set(this.abierta() === c.id ? null : c.id);
    if (c.estado === 'nueva') this.marcar(c, 'vista', false);
  }

  abrirOferta(c: Coincidencia): void {
    if (c.estado === 'nueva') this.marcar(c, 'vista', false);
  }

  async marcar(c: Coincidencia, estado: Estado, recargar = true): Promise<void> {
    try {
      const r = await firstValueFrom(this.api.cambiar(c.id, { estado }));
      this.items.update((lista) => lista.map((x) => (x.id === c.id ? { ...x, estado: r.estado } : x)));
      const filtro = this.filtros().estado;
      const sale = (filtro && filtro !== estado) || (!filtro && estado === 'descartada');
      if (recargar && sale) {
        this.items.update((lista) => lista.filter((x) => x.id !== c.id));
        this.total.update((t) => t - 1);
      }
      this.resumen.set(await firstValueFrom(this.api.resumen()));
    } catch (e) {
      this.error.set(mensajeError(e));
    }
  }

  async guardarNotas(c: Coincidencia, notas: string): Promise<void> {
    if ((c.notas ?? '') === notas) return;
    try {
      await firstValueFrom(this.api.cambiar(c.id, { notas }));
      this.items.update((lista) => lista.map((x) => (x.id === c.id ? { ...x, notas } : x)));
    } catch (e) {
      this.error.set(mensajeError(e));
    }
  }

  modalidad(m: string | null): string {
    return ({ presencial: 'Presencial', remoto: 'Remoto', hibrido: 'Híbrido' } as Record<string, string>)[m ?? ''] ?? '';
  }

  portal(p: string | null): string {
    return PORTALES[p ?? '']?.nombre ?? p ?? '';
  }

  sitioPortal(p: string | null): string | null {
    return PORTALES[p ?? '']?.sitio ?? null;
  }

  nivel(p: number | null): string {
    if (p === null) return 'pendiente';
    if (p >= 80) return 'alto';
    if (p >= 50) return 'medio';
    return 'bajo';
  }
}
