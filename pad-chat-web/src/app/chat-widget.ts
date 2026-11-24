import { Component, ElementRef, effect, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ChatService, RasaButton } from './chat.service';
import { VoiceService } from './voice.service';

@Component({
  selector: 'app-chat-widget',
  imports: [FormsModule],
  templateUrl: './chat-widget.html',
  styleUrl: './chat-widget.scss',
  host: { '(document:keydown.escape)': 'close()' },
})
export class ChatWidget {
  protected readonly chat = inject(ChatService);
  protected readonly voice = inject(VoiceService);
  protected readonly open = signal(false);
  protected readonly draft = signal('');
  protected readonly loginOpen = signal(false);
  protected readonly loginError = signal('');
  protected readonly loginBusy = signal(false);
  protected loginNom = '';
  protected loginMdp = '';

  private readonly list = viewChild<ElementRef<HTMLElement>>('list');
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('field');

  constructor() {
    this.chat.greet();
    // Défile vers le bas à chaque nouveau message ou changement d'état.
    effect(() => {
      this.chat.messages();
      this.chat.loading();
      this.open();
      setTimeout(() => {
        const el = this.list()?.nativeElement;
        if (el) el.scrollTop = el.scrollHeight;
      });
    });
  }

  protected toggle(): void {
    this.open.update((v) => !v);
    if (this.open()) setTimeout(() => this.field()?.nativeElement.focus());
  }

  protected close(): void {
    this.voice.stop(true);
    this.open.set(false);
  }

  protected restart(): void {
    this.voice.stop(true);
    this.chat.newConversation();
    this.draft.set('');
    setTimeout(() => this.field()?.nativeElement.focus());
  }

  protected accountClick(): void {
    this.voice.stop(true);
    if (this.chat.user()) {
      void this.chat.logout();
      return;
    }
    this.loginError.set('');
    this.loginOpen.update((v) => !v);
  }

  protected async doLogin(event: Event): Promise<void> {
    event.preventDefault();
    if (this.loginBusy()) return;
    this.loginBusy.set(true);
    this.loginError.set('');
    try {
      await this.chat.login(this.loginNom, this.loginMdp);
      this.loginMdp = '';
      this.loginNom = '';
      this.loginOpen.set(false);
      this.draft.set('');
    } catch (e) {
      const st = (e as { status?: number }).status;
      this.loginError.set(
        st === 401 ? 'Identifiant ou mot de passe incorrect.' : st === 429 ? 'Trop de tentatives, réessayez dans quelques minutes.' : 'Connexion impossible pour le moment.',
      );
    } finally {
      this.loginBusy.set(false);
    }
  }

  protected onInput(event: Event): void {
    this.draft.set((event.target as HTMLInputElement).value);
  }

  protected submit(event: Event): void {
    event.preventDefault();
    const text = this.draft().trim();
    if (!text || this.chat.loading()) return;
    this.draft.set('');
    void this.chat.send(text);
  }

  protected toggleMic(): void {
    if (this.voice.listening()) {
      this.voice.stop();
      return;
    }
    if (this.chat.loading()) return;
    this.voice.start(
      (interim) => this.draft.set(interim),
      (final) => {
        this.draft.set('');
        void this.chat.send(final);
      },
    );
  }

  protected choose(button: RasaButton): void {
    void this.chat.send(button.payload, button.title);
  }
}
