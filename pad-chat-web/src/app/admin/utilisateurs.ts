import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminApi, Groupe, Utilisateur, messageErreur } from './admin-api';

@Component({
  selector: 'app-utilisateurs',
  imports: [FormsModule],
  styleUrl: './admin.scss',
  template: `
    <h1>Utilisateurs du chat</h1>
    <p class="muted">
      Ces personnes se connectent dans le widget de chat (bouton « personne » en haut du chat). Chacune appartient à un ou
      plusieurs groupes ; les documents réservés à un groupe ne servent qu'à ses membres (réglage dans la page Documents).
    </p>
    @if (info()) {
      <p class="msg" [class.ok]="!erreur()" [class.err]="erreur()" [attr.role]="erreur() ? 'alert' : 'status'">{{ info() }}</p>
    }

    <section class="card" aria-label="Groupes">
      <h2>Groupes ({{ groupes().length }})</h2>
      <table>
        <thead><tr><th>Nom</th><th>Description</th><th>Membres</th><th>Documents</th><th></th></tr></thead>
        <tbody>
          @for (g of groupes(); track g.id) {
            <tr>
              <td>{{ g.nom }}</td>
              <td>{{ g.description }}</td>
              <td>{{ g.nb_membres }}</td>
              <td>{{ g.nb_documents }}</td>
              <td><button type="button" class="danger" (click)="supprimerGroupe(g)" [attr.aria-label]="'Supprimer le groupe ' + g.nom">Supprimer</button></td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="muted">Aucun groupe pour l'instant.</td></tr>
          }
        </tbody>
      </table>
      <form class="row" style="margin-top:12px" (submit)="creerGroupe($event)">
        <input name="gnom" placeholder="Nom du groupe (ex. Agents)" [(ngModel)]="gNom" aria-label="Nom du groupe" style="max-width:240px" />
        <input name="gdesc" placeholder="Description (facultatif)" [(ngModel)]="gDesc" aria-label="Description du groupe" style="max-width:300px" />
        <button type="submit" [disabled]="!gNom.trim()">Créer le groupe</button>
      </form>
    </section>

    <section class="card" aria-label="Nouvel utilisateur">
      <h2>Nouvel utilisateur</h2>
      <form (submit)="creerUtilisateur($event)">
        <div class="grid">
          <label>Identifiant
            <input name="unom" [(ngModel)]="uNom" autocomplete="off" placeholder="ex. awa.diop" />
          </label>
          <label>Mot de passe (8 caractères minimum)
            <input name="umdp" type="password" [(ngModel)]="uMdp" autocomplete="new-password" />
          </label>
        </div>
        <div class="cols">
          @for (g of groupes(); track g.id) {
            <label class="check">
              <input type="checkbox" [name]="'ng' + g.id" [checked]="uGroupes().includes(g.id)" (change)="basculerNouveau(g.id)" /> {{ g.nom }}
            </label>
          }
        </div>
        <button type="submit" [disabled]="!uNom.trim() || !uMdp">Créer l'utilisateur</button>
      </form>
    </section>

    <section class="card" aria-label="Liste des utilisateurs">
      <h2>Utilisateurs ({{ utilisateurs().length }})</h2>
      <table>
        <thead><tr><th>Identifiant</th><th>Groupes</th><th>État</th><th></th></tr></thead>
        <tbody>
          @for (u of utilisateurs(); track u.nom) {
            <tr>
              <td>{{ u.nom }}</td>
              <td>
                @if (edite() === u.nom) {
                  @for (g of groupes(); track g.id) {
                    <label class="check">
                      <input type="checkbox" [name]="'eg' + g.id" [checked]="choix().includes(g.id)" (change)="basculer(g.id)" /> {{ g.nom }}
                    </label>
                  }
                  <div class="row">
                    <button type="button" (click)="enregistrerGroupes(u)">Enregistrer</button>
                    <button type="button" class="sec" (click)="edite.set('')">Annuler</button>
                  </div>
                } @else {
                  @for (id of u.groupes; track id) { <span class="badge main">{{ nomGroupe(id) }}</span> }
                  @if (!u.groupes.length) { <span class="muted">aucun</span> }
                  <button type="button" class="sec" (click)="modifier(u)" [attr.aria-label]="'Modifier les groupes de ' + u.nom">Modifier</button>
                }
              </td>
              <td><span class="badge">{{ u.actif ? 'actif' : 'désactivé' }}</span></td>
              <td>
                <div class="row">
                  <button type="button" class="sec" (click)="basculerActif(u)">{{ u.actif ? 'Désactiver' : 'Réactiver' }}</button>
                  <button type="button" class="sec" (click)="nouveauMdp(u)">Nouveau mot de passe</button>
                  <button type="button" class="danger" (click)="supprimerUtilisateur(u)" [attr.aria-label]="'Supprimer ' + u.nom">Supprimer</button>
                </div>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="4" class="muted">Aucun utilisateur.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class Utilisateurs implements OnInit {
  private readonly api = inject(AdminApi);
  protected readonly groupes = signal<Groupe[]>([]);
  protected readonly utilisateurs = signal<Utilisateur[]>([]);
  protected readonly info = signal('');
  protected readonly erreur = signal(false);
  protected readonly edite = signal('');
  protected readonly choix = signal<number[]>([]);
  protected readonly uGroupes = signal<number[]>([]);
  protected gNom = '';
  protected gDesc = '';
  protected uNom = '';
  protected uMdp = '';

  ngOnInit(): void {
    void this.charger();
  }

  private async charger(): Promise<void> {
    try {
      const [g, u] = await Promise.all([this.api.groupes(), this.api.utilisateurs()]);
      this.groupes.set(g);
      this.utilisateurs.set(u);
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  private dire(m: string, err: boolean): void {
    this.info.set(m);
    this.erreur.set(err);
  }

  protected nomGroupe(id: number): string {
    return this.groupes().find((g) => g.id === id)?.nom ?? `#${id}`;
  }

  protected async creerGroupe(ev: Event): Promise<void> {
    ev.preventDefault();
    try {
      await this.api.enregistrerGroupe({ nom: this.gNom, description: this.gDesc });
      this.dire(`Groupe « ${this.gNom.trim()} » créé.`, false);
      this.gNom = this.gDesc = '';
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  protected async supprimerGroupe(g: Groupe): Promise<void> {
    const avert = g.nb_documents
      ? `\n\nAttention : ${g.nb_documents} document(s) sont réservés à ce groupe ; ceux qui n'ont que lui redeviendront PUBLICS.`
      : '';
    if (!confirm(`Supprimer le groupe « ${g.nom} » ?${avert}`)) return;
    try {
      await this.api.supprimerGroupe(g.id);
      this.dire(`Groupe « ${g.nom} » supprimé.`, false);
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  protected basculerNouveau(id: number): void {
    this.uGroupes.update((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));
  }

  protected async creerUtilisateur(ev: Event): Promise<void> {
    ev.preventDefault();
    try {
      await this.api.creerUtilisateur(this.uNom, this.uMdp, this.uGroupes());
      this.dire(`Utilisateur « ${this.uNom.trim().toLowerCase()} » créé.`, false);
      this.uNom = this.uMdp = '';
      this.uGroupes.set([]);
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  protected modifier(u: Utilisateur): void {
    this.choix.set([...u.groupes]);
    this.edite.set(u.nom);
  }

  protected basculer(id: number): void {
    this.choix.update((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));
  }

  protected async enregistrerGroupes(u: Utilisateur): Promise<void> {
    await this.action(() => this.api.modifierUtilisateur(u.nom, { groupes: this.choix() }), `Groupes de « ${u.nom} » mis à jour.`);
    this.edite.set('');
  }

  protected basculerActif(u: Utilisateur): Promise<void> {
    return this.action(
      () => this.api.modifierUtilisateur(u.nom, { actif: !u.actif }),
      u.actif ? `« ${u.nom} » désactivé : ses sessions sont coupées.` : `« ${u.nom} » réactivé.`,
    );
  }

  protected nouveauMdp(u: Utilisateur): Promise<void> {
    const mdp = prompt(`Nouveau mot de passe pour « ${u.nom} » (8 caractères minimum) :`);
    if (!mdp) return Promise.resolve();
    return this.action(() => this.api.modifierUtilisateur(u.nom, { mot_de_passe: mdp }), `Mot de passe de « ${u.nom} » changé.`);
  }

  protected supprimerUtilisateur(u: Utilisateur): Promise<void> {
    if (!confirm(`Supprimer l'utilisateur « ${u.nom} » ?`)) return Promise.resolve();
    return this.action(() => this.api.supprimerUtilisateur(u.nom), `« ${u.nom} » supprimé.`);
  }

  private async action(f: () => Promise<unknown>, ok: string): Promise<void> {
    try {
      await f();
      this.dire(ok, false);
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }
}
