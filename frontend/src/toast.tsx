import { useEffect, useState } from "react";

type Tone = "ok" | "error" | "info";
interface Notice { id: number; text: string; tone: Tone }

export function toast(text: string, tone: Tone = "info") {
  window.dispatchEvent(new CustomEvent("agentops:toast", { detail: { text, tone } }));
}

export function ToastHost() {
  const [items, setItems] = useState<Notice[]>([]);
  useEffect(() => {
    let n = 0;
    const onToast = (e: Event) => {
      const detail = (e as CustomEvent<{ text: string; tone: Tone }>).detail;
      const id = ++n;
      setItems((xs) => [...xs, { id, text: detail.text, tone: detail.tone }].slice(-4));
      window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5200);
    };
    window.addEventListener("agentops:toast", onToast);
    return () => window.removeEventListener("agentops:toast", onToast);
  }, []);
  if (!items.length) return null;
  return (
    <div className="toasts" aria-live="polite">
      {items.map((item) => <div key={item.id} className={`toast ${item.tone}`} role="status">{item.text}</div>)}
    </div>
  );
}
