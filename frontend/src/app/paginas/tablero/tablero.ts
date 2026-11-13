import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { Api } from '../../nucleo/api';
import { Coincidencia, Estado, FiltrosTablero, Resumen } from '../../nucleo/modelos';
import { mensajeError } from '../../nucleo/sesion';

const PESTANAS: { valor: Estado | ''; etiqueta: string }[] = [
  { valor: '', etiqueta: 'Activas' },
  { valor: 'nueva', etiqueta: 'Nuevas' },
  { valor: 'guardada', etiqueta: 'Guardadas' },
  { valor: 'postulada', etiqueta: 'Postuladas' },
  { valor: 'descartada', etiqueta: 'Descartadas' },
];
const TAMANO = 30;

@Component({
  selector: 'app-tablero',
  imports: [FormsModule, DatePipe, DecimalPipe],
  templateUrl: './tablero.html',
  styleUrl: './tablero.css',
})
export class Tablero implements OnInit {
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

  private temporizador?: ReturnType<typeof setTimeout>;

  ngOnInit(): void {
    this.cargar();
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

  nivel(p: number | null): string {
    if (p === null) return 'pendiente';
    if (p >= 80) return 'alto';
    if (p >= 50) return 'medio';
    return 'bajo';
  }
}
