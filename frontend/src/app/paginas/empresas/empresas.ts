import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { Api } from '../../nucleo/api';
import { Empresa, NuevaEmpresa, PruebaEmpresa, TipoEmpresa } from '../../nucleo/modelos';
import { mensajeError } from '../../nucleo/sesion';

export const TIPOS: Record<TipoEmpresa, { nombre: string; campo: string; ejemplo: string; ayuda: string }> = {
  pagina: {
    nombre: 'Página de empleo',
    campo: 'Dirección de la página',
    ejemplo: 'https://www.dimar.mil.co/ofertas-de-empleo',
    ayuda: 'Sirve para la mayoría de empresas: se toman los enlaces de la página que parecen vacantes. '
      + 'Use «Probar» antes de agregarla; si no encuentra nada, la página arma la lista con JavaScript y no se puede leer así.',
  },
  smartrecruiters: {
    nombre: 'SmartRecruiters',
    campo: 'Identificador de la empresa',
    ejemplo: 'konecranes',
    ayuda: 'Para empresas cuya página de empleo está en jobs.smartrecruiters.com/<identificador>.',
  },
  successfactors: {
    nombre: 'SAP SuccessFactors',
    campo: 'Dirección del sitio de empleo',
    ejemplo: 'https://career17.sapsf.com/career?company=cotecmarcoP',
    ayuda: 'Para empresas cuya página de empleo está en …sapsf.com/career?company=…',
  },
};

const vacia = (): NuevaEmpresa => ({ nombre: '', tipo: 'pagina', identificador: '', pais: null, solo_para_mi: false });

@Component({
  selector: 'app-empresas',
  imports: [FormsModule, DatePipe],
  templateUrl: './empresas.html',
  styleUrl: './empresas.css',
})
export class Empresas implements OnInit {
  private readonly api = inject(Api);

  readonly tipos = TIPOS;
  readonly listaTipos = Object.keys(TIPOS) as TipoEmpresa[];
  readonly empresas = signal<Empresa[]>([]);
  readonly cargando = signal(true);
  readonly error = signal('');
  readonly errorFormulario = signal('');
  readonly guardando = signal(false);
  readonly probando = signal(false);
  readonly prueba = signal<PruebaEmpresa | null>(null);

  nueva: NuevaEmpresa = vacia();

  async ngOnInit(): Promise<void> {
    try {
      this.empresas.set(await firstValueFrom(this.api.empresas()));
    } catch (e) {
      this.error.set(mensajeError(e));
    } finally {
      this.cargando.set(false);
    }
  }

  cambioTipo(): void {
    this.nueva.pais = this.nueva.tipo === 'smartrecruiters' ? 'CO' : null;
    this.prueba.set(null);
  }

  async probar(): Promise<void> {
    this.probando.set(true);
    this.errorFormulario.set('');
    this.prueba.set(null);
    try {
      this.prueba.set(await firstValueFrom(this.api.probarEmpresa(this.datos())));
    } catch (e) {
      this.errorFormulario.set(mensajeError(e));
    } finally {
      this.probando.set(false);
    }
  }

  async agregar(): Promise<void> {
    this.guardando.set(true);
    this.errorFormulario.set('');
    try {
      const empresa = await firstValueFrom(this.api.agregarEmpresa(this.datos()));
      this.empresas.update((l) => [...l, empresa].sort((a, b) => a.nombre.localeCompare(b.nombre)));
      this.nueva = vacia();
      this.prueba.set(null);
    } catch (e) {
      this.errorFormulario.set(mensajeError(e));
    } finally {
      this.guardando.set(false);
    }
  }

  async alternar(e: Empresa): Promise<void> {
    try {
      const cambiada = await firstValueFrom(this.api.cambiarEmpresa(e.id, !e.activa));
      this.empresas.update((l) => l.map((x) => (x.id === e.id ? cambiada : x)));
    } catch (err) {
      this.error.set(mensajeError(err));
    }
  }

  async borrar(e: Empresa): Promise<void> {
    if (!confirm(`¿Quitar ${e.nombre} de la lista? Las vacantes ya encontradas se conservan.`)) return;
    try {
      await firstValueFrom(this.api.borrarEmpresa(e.id));
      this.empresas.update((l) => l.filter((x) => x.id !== e.id));
    } catch (err) {
      this.error.set(mensajeError(err));
    }
  }

  enlace(e: Empresa): string {
    return e.tipo === 'smartrecruiters' ? `https://jobs.smartrecruiters.com/${e.identificador}` : e.identificador;
  }

  private datos(): NuevaEmpresa {
    return { ...this.nueva, nombre: this.nueva.nombre.trim(), identificador: this.nueva.identificador.trim(),
      pais: this.nueva.pais?.trim() || null };
  }
}
