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

  // Formulario de preferencias
  cargos = '';
  ciudades = '';
  excluir = '';
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
    this.excluir = p.palabras_excluir.join(', ');
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
          palabras_excluir: lista(this.excluir),
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
}
