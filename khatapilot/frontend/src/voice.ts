// Talking instead of typing. The browser's own speech recognition (Chrome, Edge, Safari) writes what the
// owner says into the message box as they speak, while the microphone's level drives a small waveform
// beside the mic. Nothing is sent until the owner presses send, so a misheard amount can be fixed first.
// Browsers without speech recognition (Firefox) simply get no mic button.

import { useCallback, useEffect, useRef, useState } from "react";

// The recognizer is not in TypeScript's DOM types (only its results are), so the part we use is declared here.
interface Recognizer {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { resultIndex: number; results: SpeechRecognitionResultList }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognizerClass = new () => Recognizer;

const Recognition: RecognizerClass | undefined =
  (window as unknown as { SpeechRecognition?: RecognizerClass }).SpeechRecognition ??
  (window as unknown as { webkitSpeechRecognition?: RecognizerClass }).webkitSpeechRecognition;

const BANGLA = /[ঀ-৿]/;
export const LANG_NAMES: Record<string, string> = { "bn-BD": "Bangla", "en-IN": "English" };

/**
 * Browsers cannot tell which language is being spoken; they must be told before listening. So the
 * app chooses: Bangla if the owner has been writing in Bangla script, otherwise English (which also
 * catches Banglish), and the browser's own language when there is nothing to go on yet.
 */
export function chooseLang(recent: string): "bn-BD" | "en-IN" {
  if (recent.trim()) return BANGLA.test(recent) ? "bn-BD" : "en-IN";
  return navigator.languages?.some((l) => l.toLowerCase().startsWith("bn")) ? "bn-BD" : "en-IN";
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Allow the microphone for this page to talk instead of typing.",
  "service-not-allowed": "Allow the microphone for this page to talk instead of typing.",
  "audio-capture": "No microphone was found.",
  network: "Speech recognition needs the internet. Please type instead.",
  "language-not-supported": "This browser cannot listen in that language. Please type instead.",
};

interface Options {
  text: string; // the message box now
  onText: (value: string) => void; // the message box as the owner speaks
  onError: (message: string) => void;
  recent: string; // the owner's recent messages, to choose the language
}

export function useVoice({ text, onText, onError, recent }: Options) {
  const [listening, setListening] = useState(false);
  const [lang, setLang] = useState<string>("en-IN");
  const level = useRef(0); // 0..1, read by the waveform every frame without re-rendering
  const latest = useRef({ text, onText, onError, recent });
  latest.current = { text, onText, onError, recent };
  const session = useRef<{ recognizer: Recognizer; stream?: MediaStream; context?: AudioContext; frame?: number } | null>(
    null,
  );

  /** Stop listening and let go of the microphone. The words stay in the message box. */
  const end = useCallback((how: "stop" | "abort") => {
    const s = session.current;
    if (!s) return;
    session.current = null;
    s.recognizer.onend = null;
    if (how === "stop") s.recognizer.stop();
    else s.recognizer.abort();
    if (s.frame) cancelAnimationFrame(s.frame);
    s.stream?.getTracks().forEach((t) => t.stop());
    void s.context?.close().catch(() => undefined);
    level.current = 0;
    setListening(false);
  }, []);
  const stop = useCallback(() => end("stop"), [end]);

  const start = useCallback(() => {
    if (!Recognition || session.current) return;
    const chosen = chooseLang(`${latest.current.recent} ${latest.current.text}`);
    setLang(chosen);
    const before = latest.current.text.trimEnd();
    let heard = "";
    const recognizer = new Recognition();
    recognizer.lang = chosen;
    recognizer.continuous = true;
    recognizer.interimResults = true;
    recognizer.onresult = (event) => {
      let pending = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const phrase = event.results[i][0].transcript;
        if (event.results[i].isFinal) heard = `${heard} ${phrase}`.trim();
        else pending += phrase;
      }
      latest.current.onText([before, `${heard} ${pending}`.trim()].filter(Boolean).join(" "));
    };
    recognizer.onerror = (event) => {
      if (event.error === "no-speech" || event.error === "aborted") return;
      latest.current.onError(ERRORS[event.error] ?? "I could not hear that. Please try again or type.");
    };
    recognizer.onend = () => end("abort"); // the browser stopped listening by itself (a long silence)
    session.current = { recognizer };
    setListening(true);
    try {
      recognizer.start();
    } catch {
      end("abort");
      return;
    }

    // The same microphone, only for the waveform. If it is refused, recognition reports it.
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((stream) => {
        const s = session.current;
        if (!s || s.recognizer !== recognizer) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        s.stream = stream;
        s.context = new AudioContext();
        const analyser = s.context.createAnalyser();
        analyser.fftSize = 512;
        s.context.createMediaStreamSource(stream).connect(analyser);
        const samples = new Uint8Array(analyser.fftSize);
        const tick = () => {
          analyser.getByteTimeDomainData(samples);
          let sum = 0;
          for (const v of samples) sum += ((v - 128) / 128) ** 2;
          level.current = Math.min(1, Math.sqrt(sum / samples.length) * 4);
          s.frame = requestAnimationFrame(tick);
        };
        tick();
      })
      .catch(() => undefined);
  }, [end]);

  useEffect(() => () => end("abort"), [end]);

  /** For sending: stop at once, keeping what was heard so far. */
  const finish = useCallback(() => end("abort"), [end]);

  return { supported: Boolean(Recognition), listening, lang, level, start, stop, finish };
}
