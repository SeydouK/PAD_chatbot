import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import { ChatWidget } from './chat-widget';

@Component({
  selector: 'app-root',
  imports: [ChatWidget, RouterOutlet],
  template: `
    <router-outlet />
    @if (!estAdmin()) {
      <app-chat-widget />
    }
  `,
})
export class App {
  private readonly router = inject(Router);
  /** Le widget de chat n'apparaît pas dans l'interface d'administration. */
  protected readonly estAdmin = toSignal(
    this.router.events.pipe(
      filter((e) => e instanceof NavigationEnd),
      map(() => this.router.url.startsWith('/admin')),
    ),
    { initialValue: typeof location !== 'undefined' && location.pathname.startsWith('/admin') },
  );
}
