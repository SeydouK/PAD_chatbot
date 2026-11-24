import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AdminApi, messageErreur } from './admin-api';
import { AuthService } from './auth.service';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  styleUrl: './admin.scss',
  template: `
    <div class="box">
      <h1>Administration du chatbot PAD</h1>
      <p class="muted">Connexion réservée aux administrateurs.</p>
      <form class="card" (submit)="connecter($event)">
        <label>Identifiant
          <input name="nom" [(ngModel)]="nom" autocomplete="username" required />
        </label>
        <label>Mot de passe
          <input name="mdp" type="password" [(ngModel)]="mdp" autocomplete="current-password" required />
        </label>
        <button type="submit" [disabled]="enCours() || !nom || !mdp">Se connecter</button>
        @if (erreur()) {
          <p class="msg err" role="alert">{{ erreur() }}</p>
        }
      </form>
    </div>
  `,
  styles: `.box { max-width: 380px; margin: 60px auto; padding: 0 16px; }`,
})
export class Login {
  private readonly api = inject(AdminApi);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  protected nom = '';
  protected mdp = '';
  protected readonly enCours = signal(false);
  protected readonly erreur = signal('');

  protected async connecter(e: Event): Promise<void> {
    e.preventDefault();
    this.enCours.set(true);
    this.erreur.set('');
    try {
      const r = await this.api.login(this.nom, this.mdp);
      this.auth.ouvrir(r.jeton, r.nom);
      this.mdp = '';
      await this.router.navigate(['/admin']);
    } catch (err) {
      this.erreur.set(messageErreur(err));
    } finally {
      this.enCours.set(false);
    }
  }
}
