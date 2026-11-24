import { Injectable, signal } from '@angular/core';

/* eslint-disable @typescript-eslint/no-explicit-any */
const Ctor: any =
  typeof window !== 'undefined' ? (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition : undefined;

const ERREURS: Record<string, string> = {
  'not-allowed': "L'accès au micro est refusé. Autorisez-le dans les réglages du navigateur.",
  'service-not-allowed': "L'accès au micro est refusé. Autorisez-le dans les réglages du navigateur.",
  'audio-capture': 'Aucun micro détecté.',
  'no-speech': "Je n'ai rien entendu. Réessayez.",
  network: 'La reconnaissance vocale est indisponible (connexion).',
};

/** Dictée vocale du navigateur (Web Speech API). Chrome, Edge et Safari ; pas Firefox. */
@Injectable({ providedIn: 'root' })
export class VoiceService {
  readonly supported = !!Ctor;
  readonly listening = signal(false);
  readonly error = signal('');
  private rec: any = null;

  start(onInterim: (texte: string) => void, onFinal: (texte: string) => void): void {
    if (!Ctor || this.listening()) return;
    this.error.set('');
    const rec = new Ctor();
    this.rec = rec;
    rec.lang = 'fr-FR';
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;

    let final = '';
    rec.onresult = (e: any) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) final += t;
        else interim += t;
      }
      onInterim((final + interim).trim());
    };
    rec.onerror = (e: any) => this.error.set(ERREURS[e.error] ?? 'La reconnaissance vocale a échoué.');
    rec.onend = () => {
      this.listening.set(false);
      this.rec = null;
      if (final.trim()) onFinal(final.trim());
    };
    try {
      rec.start();
      this.listening.set(true);
    } catch {
      this.error.set('Impossible de démarrer le micro.');
    }
  }

  /** Arrête l'écoute ; `abandon` = on jette ce qui a été dit. */
  stop(abandon = false): void {
    if (!this.rec) return;
    if (abandon) {
      this.rec.onend = () => {
        this.listening.set(false);
        this.rec = null;
      };
      this.rec.abort();
    } else {
      this.rec.stop();
    }
  }
}
