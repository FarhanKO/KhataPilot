// Talking instead of typing. While the owner speaks, the browser's own speech recognition (Chrome, Edge,
// Safari) writes a quick draft into the message box and the microphone's level drives a small waveform.
// The same speech is also recorded; when the owner taps pause, the recording is turned into text on the
// shop's own computer by an offline Whisper model that knows the shop's customer and supplier names and
// Banglish words (POST /api/voice/transcribe), and its better text replaces the draft. It works with no
// internet, and in browsers without speech recognition (Firefox) too. Nothing is sent until the owner
// presses send, so a misheard amount can be fixed first.

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
const CAN_RECORD = typeof window !== "undefined" && "MediaRecorder" in window && !!navigator.mediaDevices?.getUserMedia;

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

/** The recording format the browser can make: webm/opus in Chrome, Edge and Firefox, mp4 in Safari. */
export function recordingType(): string | undefined {
  if (!CAN_RECORD) return undefined;
  return ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((t) =>
    MediaRecorder.isTypeSupported?.(t),
  );
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Allow the microphone for this page to talk instead of typing.",
  "service-not-allowed": "Allow the microphone for this page to talk instead of typing.",
  "audio-capture": "No microphone was found.",
  network: "The browser's speech service needs the internet; your words are still being written from the recording.",
  "language-not-supported": "This browser cannot listen in that language. Please type instead.",
};

// Asked once per page load: can the shop's computer turn speech into text?
let serverCheck: Promise<boolean> | null = null;
function serverCanTranscribe(): Promise<boolean> {
  serverCheck ??= fetch("/api/voice")
    .then((r) => (r.ok ? r.json() : { server: false }))
    .then((j: { server?: boolean }) => Boolean(j.server))
    .catch(() => false);
  return serverCheck;
}

interface Options {
  text: string; // the message box now
  onText: (value: string) => void; // the message box as the owner speaks
  onError: (message: string) => void;
  recent: string; // the owner's recent messages, to choose the language
}

interface Session {
  recognizer?: Recognizer;
  recorder?: MediaRecorder;
  chunks: Blob[];
  stream?: MediaStream;
  context?: AudioContext;
  frame?: number;
  keep: boolean; // false: dropped (sent while listening), so the recording is not transcribed
}

export function useVoice({ text, onText, onError, recent }: Options) {
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [server, setServer] = useState(false);
  const [lang, setLang] = useState<string>("en-IN");
  const level = useRef(0); // 0..1, read by the waveform every frame without re-rendering
  const latest = useRef({ text, onText, onError, recent });
  latest.current = { text, onText, onError, recent };
  const session = useRef<Session | null>(null);
  const before = useRef(""); // the message box before this recording, kept in front of what is heard

  useEffect(() => {
    let alive = true;
    void serverCanTranscribe().then((ok) => alive && setServer(ok && Boolean(recordingType())));
    return () => {
      alive = false;
    };
  }, []);

  /** The recording, turned into text on the shop's computer; its text replaces the browser's draft. */
  const transcribe = useCallback(async (blob: Blob, chosen: string) => {
    if (blob.size < 2000) return; // a tap, not speech
    setTranscribing(true);
    try {
      const response = await fetch(`/api/voice/transcribe?lang=${chosen.startsWith("bn") ? "bn" : "en"}`, {
        method: "POST",
        headers: { "Content-Type": blob.type || "application/octet-stream" },
        body: blob,
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? response.statusText);
      const heard = ((await response.json()) as { text: string }).text.trim();
      if (heard) latest.current.onText([before.current, heard].filter(Boolean).join(" "));
    } catch (e) {
      // The browser's draft stays; say so only if there is none.
      if (latest.current.text.trim() === before.current.trim())
        latest.current.onError(`I could not write that down (${e instanceof Error ? e.message : e}). Please try again or type.`);
    } finally {
      setTranscribing(false);
    }
  }, []);

  /** Stop listening and let go of the microphone. The words stay in the message box. */
  const end = useCallback((how: "stop" | "abort") => {
    const s = session.current;
    if (!s) return;
    session.current = null;
    s.keep = how === "stop";
    if (s.recognizer) {
      s.recognizer.onend = null;
      if (how === "stop") s.recognizer.stop();
      else s.recognizer.abort();
    }
    if (s.recorder && s.recorder.state !== "inactive") s.recorder.stop(); // its onstop sends the recording
    if (s.frame) cancelAnimationFrame(s.frame);
    if (!s.recorder) s.stream?.getTracks().forEach((t) => t.stop());
    void s.context?.close().catch(() => undefined);
    level.current = 0;
    setListening(false);
  }, []);
  const stop = useCallback(() => end("stop"), [end]);

  const start = useCallback(() => {
    if (session.current || transcribing || (!Recognition && !server)) return;
    const chosen = chooseLang(`${latest.current.recent} ${latest.current.text}`);
    setLang(chosen);
    before.current = latest.current.text.trimEnd();
    const s: Session = { chunks: [], keep: true };
    session.current = s;
    setListening(true);

    if (Recognition) {
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
        latest.current.onText([before.current, `${heard} ${pending}`.trim()].filter(Boolean).join(" "));
      };
      recognizer.onerror = (event) => {
        if (event.error === "no-speech" || event.error === "aborted") return;
        if (server && event.error === "network") return; // offline: the recording still becomes text
        latest.current.onError(ERRORS[event.error] ?? "I could not hear that. Please try again or type.");
      };
      // The browser stopped by itself (a long silence): with a recording, finish it; without, just stop.
      recognizer.onend = () => end(server ? "stop" : "abort");
      s.recognizer = recognizer;
      try {
        recognizer.start();
      } catch {
        s.recognizer = undefined;
      }
    }

    // The microphone: for the waveform, and recorded for the shop's computer to write down.
    navigator.mediaDevices
      ?.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      .then((stream) => {
        if (session.current !== s) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        s.stream = stream;
        const type = recordingType();
        if (server && type) {
          const recorder = new MediaRecorder(stream, { mimeType: type, audioBitsPerSecond: 32_000 });
          recorder.ondataavailable = (e) => e.data.size && s.chunks.push(e.data);
          recorder.onstop = () => {
            stream.getTracks().forEach((t) => t.stop());
            if (s.keep) void transcribe(new Blob(s.chunks, { type }), chosen);
          };
          recorder.start(250);
          s.recorder = recorder;
          setTimeout(() => session.current === s && end("stop"), 60_000); // a minute at most
        }
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
      .catch(() => {
        if (!s.recognizer) {
          latest.current.onError(ERRORS["not-allowed"]);
          end("abort");
        }
      });
  }, [end, server, transcribe, transcribing]);

  useEffect(() => () => end("abort"), [end]);

  /** For sending: stop at once, keeping what was heard so far (the recording is not written down). */
  const finish = useCallback(() => end("abort"), [end]);

  return {
    supported: Boolean(Recognition) || server,
    server,
    listening,
    transcribing,
    lang,
    level,
    start,
    stop,
    finish,
  };
}
