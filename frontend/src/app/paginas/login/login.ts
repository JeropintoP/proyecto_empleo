import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { Sesion, mensajeError } from '../../nucleo/sesion';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <main class="login">
      <form class="tarjeta" (ngSubmit)="entrar()">
        <h1>Proyecto Empleo</h1>
        <p class="sutil">Ingrese con su cuenta</p>

        <label>
          Correo
          <input name="email" type="email" autocomplete="username" required [(ngModel)]="email" />
        </label>
        <label>
          Clave
          <input name="clave" type="password" autocomplete="current-password" required [(ngModel)]="clave" />
        </label>

        @if (error()) {
          <p class="error">{{ error() }}</p>
        }
        <button class="primario" type="submit" [disabled]="cargando() || !email || !clave">
          {{ cargando() ? 'Ingresando…' : 'Ingresar' }}
        </button>
      </form>
    </main>
  `,
  styles: `
    .login { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
    .tarjeta { width: 100%; max-width: 360px; display: grid; gap: 14px; }
    h1 { margin: 0; font-size: 22px; }
    .sutil { margin: -8px 0 6px; }
    label { display: grid; gap: 6px; font-size: 14px; font-weight: 500; }
  `,
})
export class Login {
  private readonly sesion = inject(Sesion);
  private readonly router = inject(Router);

  email = '';
  clave = '';
  readonly cargando = signal(false);
  readonly error = signal('');

  async entrar(): Promise<void> {
    this.cargando.set(true);
    this.error.set('');
    try {
      await this.sesion.iniciar(this.email, this.clave);
      this.router.navigateByUrl('/');
    } catch (e) {
      this.error.set(mensajeError(e));
    } finally {
      this.cargando.set(false);
    }
  }
}
