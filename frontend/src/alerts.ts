import { getAlertPrefs } from "./api";

let ctx: AudioContext | null = null;

/** Two short tones: rising for "needs you", falling for problems. */
export function beep(kind: "attention" | "problem" | "good" = "attention") {
  try {
    ctx = ctx ?? new AudioContext();
    const notes = kind === "attention" ? [660, 880] : kind === "good" ? [523, 784] : [440, 330];
    notes.forEach((f, i) => {
      const o = ctx!.createOscillator();
      const g = ctx!.createGain();
      o.type = "sine";
      o.frequency.value = f;
      const t = ctx!.currentTime + i * 0.13;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.08, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
      o.connect(g).connect(ctx!.destination);
      o.start(t);
      o.stop(t + 0.13);
    });
  } catch { /* audio not available */ }
}

export function canNotify() {
  return typeof Notification !== "undefined";
}

export async function requestDesktopPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!canNotify()) return "unsupported";
  if (Notification.permission === "granted") return "granted";
  return Notification.requestPermission();
}

/** Desktop notification + sound, depending on the viewer's preferences. */
export function alertUser(title: string, body: string, link: string, kind: "attention" | "problem" | "good") {
  const prefs = getAlertPrefs();
  if (prefs.sound) beep(kind);
  if (prefs.desktop && canNotify() && Notification.permission === "granted") {
    try {
      const n = new Notification(title, { body, tag: link });
      n.onclick = () => { window.focus(); window.location.assign(link); };
    } catch { /* some browsers only allow notifications from a service worker */ }
  }
}
