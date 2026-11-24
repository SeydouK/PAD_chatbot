import { Component, ElementRef, OnDestroy, OnInit, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminApi, Doc, EtatSite, Groupe, ReglagesSite, messageErreur } from './admin-api';

@Component({
  selector: 'app-documents',
  imports: [FormsModule],
  styleUrl: './admin.scss',
  template: `
    <h1>Documents</h1>
    <p class="muted">
      Les documents ajoutés ici sont pris en compte par le chatbot dès la question suivante, sans entraînement.
      Formats : PDF, Word (.docx), texte (.txt, .md) · 10 Mo maximum.
    </p>

    <section class="card">
      <h2>Ajouter un document</h2>
      <div class="row">
        <input #fichier type="file" accept=".pdf,.docx,.txt,.md" aria-label="Choisir un fichier" (change)="choisi()" />
        <button type="button" (click)="envoyer()" [disabled]="!pret() || enCours()">Téléverser</button>
      </div>
      @if (message()) {
        <p class="msg" [class.ok]="!echec()" [class.err]="echec()" [attr.role]="echec() ? 'alert' : 'status'">{{ message() }}</p>
      }
    </section>

    <section class="card" aria-label="Collecte du site">
      <h2>Mettre à jour depuis le site du PAD</h2>
      <p class="muted">
        Relit les pages publiques du site (en respectant son fichier robots.txt) et remplace les pages déjà collectées.
        Comptez environ 10 secondes par page : la collecte tourne en arrière-plan, vous pouvez quitter cette page.
      </p>
      <div class="grid">
        <label>Adresse du site
          <input name="siteurl" [(ngModel)]="site.url" [disabled]="etat().en_cours" />
        </label>
        <label>Nombre de pages maximum
          <input type="number" name="sitemax" min="1" max="500" [(ngModel)]="site.max_pages" [disabled]="etat().en_cours" />
        </label>
      </div>
      <label class="check">
        <input type="checkbox" name="siteinsecure" [(ngModel)]="site.insecure" [disabled]="etat().en_cours" />
        Ignorer la vérification du certificat SSL (à n'utiliser que si la collecte échoue sur une erreur de certificat)
      </label>
      <div class="row" style="margin-top:10px">
        @if (etat().en_cours) {
          <button type="button" class="danger" (click)="arreter()">Arrêter la collecte</button>
        } @else {
          <button type="button" (click)="lancer()" [disabled]="!site.url">Lancer la collecte</button>
        }
      </div>
      @if (etat().en_cours) {
        <p class="msg" role="status">Collecte en cours · {{ etat().pages }} page(s) collectée(s)…</p>
      } @else if (etat().fin) {
        <p class="msg" [class.ok]="etat().code === 0" [class.err]="etat().code !== 0" role="status">
          {{ etat().code === 0 ? 'Collecte terminée : ' + etat().pages + ' page(s) lues.' : 'Collecte interrompue ou en échec (voir le journal).' }}
        </p>
      }
      @if (siteMessage()) {
        <p class="msg err" role="alert">{{ siteMessage() }}</p>
      }
      @if (etat().journal.length) {
        <details>
          <summary class="muted">Journal</summary>
          <pre class="journal">{{ etat().journal.join('\n') }}</pre>
        </details>
      }
    </section>

    <section class="card">
      <h2>Documents connus du chatbot ({{ docs().length }})</h2>
      <table>
        <thead><tr><th>Fichier</th><th>Origine</th><th>Taille</th><th>Modifié</th><th>Visible par</th><th></th></tr></thead>
        <tbody>
          @for (d of docs(); track d.nom) {
            <tr>
              <td>{{ d.nom }}</td>
              <td><span class="badge">{{ d.origine }}</span></td>
              <td>{{ ko(d.taille) }}</td>
              <td>{{ date(d.modifie) }}</td>
              <td>
                @if (edite() === d.nom) {
                  @for (g of groupes(); track g.id) {
                    <label class="check">
                      <input type="checkbox" [name]="'g' + g.id" [checked]="choix().includes(g.id)" (change)="basculer(g.id)" />
                      {{ g.nom }}
                    </label>
                  } @empty {
                    <span class="muted">Aucun groupe créé (page Utilisateurs).</span>
                  }
                  <div class="row">
                    <button type="button" (click)="enregistrerGroupes(d)">Enregistrer</button>
                    <button type="button" class="sec" (click)="edite.set('')">Annuler</button>
                  </div>
                } @else {
                  @if (d.groupes.length) {
                    @for (id of d.groupes; track id) { <span class="badge main">{{ nomGroupe(id) }}</span> }
                  } @else {
                    <span class="badge">public</span>
                  }
                  <button type="button" class="sec" (click)="modifierGroupes(d)" [attr.aria-label]="'Choisir les groupes de ' + d.nom">Modifier</button>
                }
              </td>
              <td>
                @if (d.supprimable) {
                  <button type="button" class="danger" (click)="supprimer(d)" [attr.aria-label]="'Supprimer ' + d.nom">Supprimer</button>
                }
              </td>
            </tr>
          } @empty {
            <tr><td colspan="6" class="muted">Aucun document.</td></tr>
          }
        </tbody>
      </table>
      <p class="muted">« public » = tout le monde, y compris sans connexion. Dès qu'un groupe est choisi, seuls les utilisateurs du chat membres de ce groupe obtiennent des réponses issues du document.</p>
      <p class="muted">Les pages du site et les exports de base se gèrent par leurs scripts ou dans « Bases de données ».</p>
    </section>
  `,
})
export class Documents implements OnInit, OnDestroy {
  private readonly api = inject(AdminApi);
  private readonly champ = viewChild.required<ElementRef<HTMLInputElement>>('fichier');
  protected readonly docs = signal<Doc[]>([]);
  protected readonly message = signal('');
  protected readonly echec = signal(false);
  protected readonly enCours = signal(false);
  protected readonly pret = signal(false);
  protected readonly groupes = signal<Groupe[]>([]);
  protected readonly edite = signal('');
  protected readonly choix = signal<number[]>([]);

  protected site: ReglagesSite = { url: '', max_pages: 150, insecure: false };
  protected readonly etat = signal<EtatSite>({ en_cours: false, debut: null, fin: null, code: null, url: null, pages: 0, journal: [] });
  protected readonly siteMessage = signal('');
  private minuteur: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    void this.charger();
    void this.initSite();
    void this.api.groupes().then((g) => this.groupes.set(g)).catch(() => undefined);
  }

  ngOnDestroy(): void {
    this.stopperSuivi();
  }

  private async initSite(): Promise<void> {
    try {
      this.site = await this.api.reglagesSite();
      await this.rafraichir();
    } catch (e) {
      this.siteMessage.set(messageErreur(e));
    }
  }

  private async rafraichir(): Promise<void> {
    const avant = this.etat().en_cours;
    const e = await this.api.etatSite();
    this.etat.set(e);
    if (e.en_cours) this.suivre();
    else {
      this.stopperSuivi();
      if (avant) await this.charger(); // la collecte vient de finir : mettre la liste à jour
    }
  }

  private suivre(): void {
    if (this.minuteur) return;
    this.minuteur = setInterval(() => void this.rafraichir().catch(() => this.stopperSuivi()), 3000);
  }

  private stopperSuivi(): void {
    if (this.minuteur) clearInterval(this.minuteur);
    this.minuteur = null;
  }

  protected async lancer(): Promise<void> {
    this.siteMessage.set('');
    try {
      this.etat.set(await this.api.lancerSite(this.site));
      this.suivre();
    } catch (e) {
      this.siteMessage.set(messageErreur(e));
    }
  }

  protected async arreter(): Promise<void> {
    try {
      this.etat.set(await this.api.arreterSite());
      this.stopperSuivi();
      await this.charger();
    } catch (e) {
      this.siteMessage.set(messageErreur(e));
    }
  }

  private async charger(): Promise<void> {
    try {
      this.docs.set(await this.api.documents());
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  protected nomGroupe(id: number): string {
    return this.groupes().find((g) => g.id === id)?.nom ?? `#${id}`;
  }

  protected modifierGroupes(d: Doc): void {
    this.choix.set([...d.groupes]);
    this.edite.set(d.nom);
  }

  protected basculer(id: number): void {
    this.choix.update((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));
  }

  protected async enregistrerGroupes(d: Doc): Promise<void> {
    try {
      await this.api.groupesDuDocument(d.nom, this.choix());
      this.edite.set('');
      this.dire(this.choix().length ? `« ${d.nom} » réservé aux groupes choisis.` : `« ${d.nom} » est public.`, false);
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  protected choisi(): void {
    this.pret.set((this.champ().nativeElement.files?.length ?? 0) > 0);
  }

  protected async envoyer(): Promise<void> {
    const f = this.champ().nativeElement.files?.[0];
    if (!f) return;
    this.enCours.set(true);
    try {
      const r = await this.api.televerser(f);
      this.dire(`${f.name} ${r.remplace ? 'remplacé' : 'ajouté'} (${r.mots} mots).`, false);
      this.champ().nativeElement.value = '';
      this.pret.set(false);
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    } finally {
      this.enCours.set(false);
    }
  }

  protected async supprimer(d: Doc): Promise<void> {
    if (!confirm(`Supprimer « ${d.nom} » ? Le chatbot ne pourra plus s'en servir.`)) return;
    try {
      await this.api.supprimerDocument(d.nom);
      this.dire(`${d.nom} supprimé.`, false);
      await this.charger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  private dire(m: string, echec: boolean): void {
    this.message.set(m);
    this.echec.set(echec);
  }

  protected ko = (o: number) => (o < 1024 ? `${o} o` : `${Math.round(o / 1024)} Ko`);
  protected date = (iso: string) => new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
}
