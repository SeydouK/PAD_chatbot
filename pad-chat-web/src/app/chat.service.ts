import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';
import { ADMIN_API, CHAT_CONFIG } from './config';

export interface RasaButton {
  title: string;
  payload: string;
}

interface RasaReply {
  recipient_id: string;
  text?: string;
  image?: string;
  buttons?: RasaButton[];
}

export interface ChatUser {
  nom: string;
  groupes: string[];
}

interface Session extends ChatUser {
  sender_id: string;
}

const CLE_SESSION = 'pad-chat-session';

export interface ChatMessage {
  id: number;
  from: 'user' | 'bot';
  text?: string;
  image?: string;
  buttons?: RasaButton[];
  error?: boolean;
}

const SUGGESTIONS: RasaButton[] = [
  { title: 'Horaires', payload: 'Quels sont vos horaires ?' },
  { title: 'Services', payload: 'Quels sont vos services ?' },
  { title: 'Suivi de navire', payload: 'Je veux suivre un navire' },
  { title: 'Rendez-vous', payload: 'Je voudrais un rendez-vous' },
  { title: 'Points forts et faibles du port', payload: 'Quels sont les points forts du port ?' },
];

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly http = inject(HttpClient);
  private senderId = newSenderId();
  private nextId = 1;

  readonly messages = signal<ChatMessage[]>([]);
  readonly loading = signal(false);
  /** Utilisateur connecté (null = visiteur anonyme : seuls les documents publics lui répondent). */
  readonly user = signal<ChatUser | null>(null);

  constructor() {
    // Session retrouvée après un rechargement de page : on vérifie auprès du serveur qu'elle est encore valide.
    const saved = lireSession();
    if (saved) {
      this.senderId = saved.sender_id;
      this.user.set({ nom: saved.nom, groupes: saved.groupes });
      this.http
        .post<ChatUser>(`${ADMIN_API}/chat/session`, { sender_id: saved.sender_id })
        .subscribe({
          next: (u) => this.user.set(u),
          error: (e) => {
            if (e?.status === 401) this.expirer();
          },
        });
    }
  }

  /** Connexion d'un utilisateur : le serveur fournit l'identifiant de conversation, lié à ses groupes. */
  async login(nom: string, mot_de_passe: string): Promise<void> {
    const r = await firstValueFrom(this.http.post<Session>(`${ADMIN_API}/chat/connexion`, { nom, mot_de_passe }));
    this.appliquer(r);
    this.messages.set([]);
    this.loading.set(false);
    this.greet();
  }

  async logout(): Promise<void> {
    const sid = this.senderId;
    if (this.user()) {
      try {
        await firstValueFrom(this.http.post(`${ADMIN_API}/chat/deconnexion`, { sender_id: sid }));
      } catch {
        /* serveur injoignable : la session expirera d'elle-même */
      }
    }
    this.user.set(null);
    ecrireSession(null);
    this.senderId = newSenderId(true);
    this.messages.set([]);
    this.loading.set(false);
    this.greet();
  }

  private appliquer(r: Session): void {
    this.senderId = r.sender_id;
    this.user.set({ nom: r.nom, groupes: r.groupes });
    ecrireSession(r);
  }

  /** Session refusée par le serveur (expirée, utilisateur désactivé...) : retour au mode visiteur. */
  private expirer(): void {
    if (!this.user()) return;
    this.user.set(null);
    ecrireSession(null);
    this.senderId = newSenderId(true);
    this.messages.set([]);
    this.push({ from: 'bot', error: true, text: 'Votre session a expiré : reconnectez-vous pour accéder aux documents réservés.' });
  }

  /** Message d'accueil local (n'appelle pas Rasa). */
  greet(): void {
    if (this.messages().length > 0) return;
    this.push({
      from: 'bot',
      text: "Bonjour ! Je suis l'assistant virtuel du Port Autonome de Dakar. Comment puis-je vous aider ?",
      buttons: SUGGESTIONS,
    });
  }

  /**
   * Repart de zéro : nouvel identifiant de conversation, donc nouveau suivi côté Rasa
   * (utile quand le bot est resté bloqué dans un formulaire).
   */
  newConversation(): void {
    this.messages.set([]);
    if (this.user()) {
      // Connecté : nouvel identifiant fourni par le serveur, avec les mêmes droits (on attend sa réponse).
      this.loading.set(true);
      this.http.post<Session>(`${ADMIN_API}/chat/nouvelle`, { sender_id: this.senderId }).subscribe({
        next: (r) => {
          this.appliquer(r);
          this.loading.set(false);
          this.greet();
        },
        error: () => {
          this.loading.set(false);
          this.expirer();
        },
      });
      return;
    }
    this.senderId = newSenderId(true);
    this.loading.set(false);
    this.greet();
  }

  /** Envoie un message à Rasa. `shown` = texte affiché si différent du texte envoyé (boutons). */
  async send(text: string, shown: string = text): Promise<void> {
    const message = text.trim();
    if (!message || this.loading()) return;

    const sender = this.senderId;
    this.clearButtons();
    this.push({ from: 'user', text: shown.trim() });
    this.loading.set(true);

    try {
      const replies = await firstValueFrom(
        this.http
          .post<RasaReply[]>(CHAT_CONFIG.rasaUrl, { sender, message })
          .pipe(timeout(CHAT_CONFIG.timeoutMs)),
      );
      if (sender !== this.senderId) return; // la conversation a été réinitialisée entre-temps
      if (replies.length === 0) {
        this.push({ from: 'bot', text: "Je n'ai pas de réponse pour le moment. Pouvez-vous reformuler ?" });
      }
      for (const r of replies) {
        this.push({
          from: 'bot',
          text: r.text,
          image: r.image,
          buttons: r.buttons?.length ? r.buttons : undefined,
        });
      }
    } catch {
      if (sender !== this.senderId) return;
      this.push({
        from: 'bot',
        error: true,
        text: 'Le service est momentanément indisponible. Veuillez réessayer dans un instant.',
      });
    } finally {
      if (sender === this.senderId) this.loading.set(false);
    }
  }

  private push(m: Omit<ChatMessage, 'id'>): void {
    this.messages.update((list) => [...list, { ...m, id: this.nextId++ }]);
  }

  /** Les boutons déjà proposés disparaissent une fois qu'on a répondu. */
  private clearButtons(): void {
    this.messages.update((list) => list.map((m) => (m.buttons ? { ...m, buttons: undefined } : m)));
  }
}

/** Identifiant de conversation Rasa, conservé pour la durée de l'onglet. */
function newSenderId(renew = false): string {
  const make = () =>
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  try {
    const key = 'pad-chat-sender';
    let id = renew ? null : sessionStorage.getItem(key);
    if (!id) {
      id = make();
      sessionStorage.setItem(key, id);
    }
    return id;
  } catch {
    return make();
  }
}

function lireSession(): Session | null {
  try {
    const brut = sessionStorage.getItem(CLE_SESSION);
    const r = brut ? (JSON.parse(brut) as Session) : null;
    return r && typeof r.sender_id === 'string' && r.sender_id.startsWith('u-') ? r : null;
  } catch {
    return null;
  }
}

function ecrireSession(r: Session | null): void {
  try {
    if (r) sessionStorage.setItem(CLE_SESSION, JSON.stringify(r));
    else sessionStorage.removeItem(CLE_SESSION);
  } catch {
    /* stockage indisponible */
  }
}
