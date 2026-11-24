import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from './auth.service';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  styleUrl: './admin.scss',
  template: `
    <header>
      <strong>Administration · Chatbot PAD</strong>
      <nav>
        <a routerLink="documents" routerLinkActive="on">Documents</a>
        <a routerLink="bases" routerLinkActive="on">Bases de données</a>
        <a routerLink="api" routerLinkActive="on">API</a>
        <a routerLink="utilisateurs" routerLinkActive="on">Utilisateurs</a>
      </nav>
      <span class="spacer"></span>
      @if (auth.nom()) {
        <span class="muted">{{ auth.nom() }}</span>
      }
      <button type="button" class="sec" (click)="quitter()">Se déconnecter</button>
    </header>
    <main><router-outlet /></main>
  `,
  styles: `
    header { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--surface); }
    nav { display: flex; gap: 14px; }
    nav a { color: var(--muted); text-decoration: none; padding: 4px 2px; }
    nav a.on { color: var(--accent); border-bottom: 2px solid var(--accent); }
    .spacer { flex: 1; }
    .muted { margin: 0; }
    main { max-width: 900px; margin: 24px auto; padding: 0 16px; }
  `,
})
export class Shell {
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected quitter(): void {
    this.auth.fermer();
    void this.router.navigate(['/admin/connexion']);
  }
}
