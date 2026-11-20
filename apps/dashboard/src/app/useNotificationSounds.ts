import { useEffect, useRef, useState } from "react";

const preference = "drivetwin-alert-sounds";
const tones = {
  warning: [520, 395],
  recommendation: [505, 675],
  approved: [470, 630],
  charging: [440, 550, 660],
  charged: [550, 695],
  arrival: [495, 625, 745],
};
type Sound = keyof typeof tones;

export function useNotificationSounds() {
  const [enabled, setEnabled] = useState(() => {
    try {
      return localStorage.getItem(preference) !== "off";
    } catch {
      return true;
    }
  });
  const audio = useRef<AudioContext | null>(null);
  const heard = useRef(new Set<string>());
  useEffect(
    () => () => {
      const context = audio.current;
      audio.current = null;
      if (context) void context.close().catch(() => {});
    },
    [],
  );

  function unlock() {
    try {
      const context = (audio.current ??= new AudioContext());
      if (context.state !== "running") void context.resume().catch(() => {});
    } catch {
      // Audio support is optional. Visual notifications still work.
    }
  }

  function play(id: string, sound: Sound) {
    if (heard.current.has(id)) return;
    heard.current.add(id);
    const context = audio.current;
    if (!enabled || context?.state !== "running") return;
    const start = context.currentTime + 0.015;
    tones[sound].forEach((frequency, index) => {
      const at = start + index * 0.15;
      const oscillator = context.createOscillator();
      const envelope = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      envelope.gain.setValueAtTime(0, at);
      envelope.gain.linearRampToValueAtTime(0.09, at + 0.025);
      envelope.gain.exponentialRampToValueAtTime(0.001, at + 0.135);
      oscillator.connect(envelope).connect(context.destination);
      oscillator.start(at);
      oscillator.stop(at + 0.14);
      oscillator.onended = () => {
        oscillator.disconnect();
        envelope.disconnect();
      };
    });
  }

  function toggle() {
    const next = !enabled;
    if (next) unlock();
    setEnabled(next);
    try {
      localStorage.setItem(preference, next ? "on" : "off");
    } catch {
      // The toggle remains available for this page.
    }
  }

  return { enabled, play, toggle, unlock };
}
