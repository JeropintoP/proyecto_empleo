import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { Api } from '../../nucleo/api';
import { Modalidad, Perfil as DatosPerfil } from '../../nucleo/modelos';
import { mensajeError } from '../../nucleo/sesion';

const lista = (texto: string): string[] =>
  texto.split(',').map((x) => x.trim()).filter(Boolean);

@Component({
  selector: 'app-perfil',
  imports: [FormsModule, DatePipe],
  templateUrl: './perfil.html',
  styleUrl: './perfil.css',
})
export class Perfil implements OnInit {
  private readonly api = inject(Api);

  readonly perfil = signal<DatosPerfil | null>(null);
  readonly error = signal('');
  readonly aviso = signal('');
  readonly subiendo = signal(false);
  readonly guardando = signal(false);
  readonly cambiandoClave = signal(false);
  readonly errorClave = signal('');
  readonly avisoClave = signal('');

  claveActual = '';
  claveNueva = '';
  claveConfirmacion = '';

  // Formulario de preferencias
  cargos = '';
  ciudades = '';
  readonly excluir = signal<string[]>([]);
  nuevaExclusion = '';
  modalidad: Modalidad = 'indiferente';
  salario: number | null = null;

  async ngOnInit(): Promise<void> {
    try {
      this.llenar(await firstValueFrom(this.api.perfil()));
    } catch (e) {
      this.error.set(mensajeError(e));
    }
  }

  private llenar(p: DatosPerfil): void {
    this.perfil.set(p);
    this.cargos = p.cargos_objetivo.join(', ');
    this.ciudades = p.ciudades.join(', ');
    this.excluir.set([...p.palabras_excluir]);
    this.modalidad = p.modalidad;
    this.salario = p.salario_minimo;
  }

  async subir(evento: Event): Promise<void> {
    const entrada = evento.target as HTMLInputElement;
    const archivo = entrada.files?.[0];
    entrada.value = '';
    if (!archivo) return;
    this.subiendo.set(true);
    this.error.set('');
    this.aviso.set('');
    try {
      this.llenar(await firstValueFrom(this.api.subirCv(archivo)));
      this.aviso.set('Hoja de vida analizada. Revise los cargos y ajuste lo que haga falta.');
    } catch (e) {
      this.error.set(mensajeError(e));
    } finally {
      this.subiendo.set(false);
    }
  }

  async guardar(): Promise<void> {
    this.guardando.set(true);
    this.error.set('');
    this.aviso.set('');
    try {
      const p = await firstValueFrom(
        this.api.guardarPerfil({
          cargos_objetivo: lista(this.cargos),
          ciudades: lista(this.ciudades),
          palabras_excluir: this.excluir(),
          modalidad: this.modalidad,
          salario_minimo: this.salario || null,
        }),
      );
      this.llenar(p);
      this.aviso.set('Preferencias guardadas. Se aplican desde la próxima actualización.');
    } catch (e) {
      this.error.set(mensajeError(e));
    } finally {
      this.guardando.set(false);
    }
  }

  agregarExclusion(): void {
    const nuevas = lista(this.nuevaExclusion.toLowerCase()).filter((x) => !this.excluir().includes(x));
    this.nuevaExclusion = '';
    if (nuevas.length) this.guardarExclusiones([...this.excluir(), ...nuevas]);
  }

  quitarExclusion(palabra: string): void {
    this.guardarExclusiones(this.excluir().filter((x) => x !== palabra));
  }

  /** Las exclusiones se guardan al instante (sin esperar "Guardar preferencias"). */
  private async guardarExclusiones(palabras: string[]): Promise<void> {
    const antes = this.excluir();
    this.excluir.set(palabras);
    this.error.set('');
    try {
      const p = await firstValueFrom(this.api.guardarPerfil({ palabras_excluir: palabras }));
      this.perfil.set(p);
    } catch (e) {
      this.excluir.set(antes);
      this.error.set(mensajeError(e));
    }
  }

  async cambiarClave(): Promise<void> {
    this.errorClave.set('');
    this.avisoClave.set('');
    if (this.claveNueva.length < 10) {
      this.errorClave.set('La clave nueva debe tener al menos 10 caracteres.');
      return;
    }
    if (this.claveNueva !== this.claveConfirmacion) {
      this.errorClave.set('La confirmación no coincide con la clave nueva.');
      return;
    }
    this.cambiandoClave.set(true);
    try {
      await firstValueFrom(this.api.cambiarClave(this.claveActual, this.claveNueva));
      this.claveActual = this.claveNueva = this.claveConfirmacion = '';
      this.avisoClave.set('Clave actualizada.');
    } catch (e) {
      this.errorClave.set(mensajeError(e));
    } finally {
      this.cambiandoClave.set(false);
    }
  }
}
