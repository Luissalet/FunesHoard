import React, { useEffect, useRef } from "react";
import { fmtClock, SPEAKER_LABEL } from "../format.js";

/** Segment list with channel colours; click → onSeek(start_s); follows `currentTime` and auto-scrolls when `follow`. */
export default function Transcript({ segments, currentTime = null, onSeek, follow = false, highlight = "" }) {
  const box = useRef(null);
  const activeId = currentTime == null ? null : (segments.find((s) => currentTime >= s.start_s && currentTime < Math.max(s.end_s, s.start_s + 0.5)) || {}).id;

  useEffect(() => {
    if (!follow || !box.current) return;
    box.current.scrollTop = box.current.scrollHeight;
  }, [segments.length, follow]);

  useEffect(() => {
    if (activeId == null || !box.current) return;
    const el = box.current.querySelector(`[data-id="${activeId}"]`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  if (!segments.length) return <p className="help px-2 py-6 text-center">Todavía no hay texto.</p>;
  return (
    <div className="transcript" ref={box}>
      {segments.map((seg) => (
        <div
          key={seg.id}
          data-id={seg.id}
          className={`seg seg-${seg.speaker} ${seg.live ? "live" : ""}`}
          aria-current={seg.id === activeId ? "true" : undefined}
          onClick={() => onSeek && onSeek(seg.start_s)}
          role={onSeek ? "button" : undefined}
          tabIndex={onSeek ? 0 : undefined}
          onKeyDown={(e) => e.key === "Enter" && onSeek && onSeek(seg.start_s)}
        >
          <span className="t num">{fmtClock(seg.start_s)}</span>
          <span className="who">{SPEAKER_LABEL[seg.speaker] || seg.speaker}</span>
          <span className="text min-w-0 break-words">{highlight ? mark(seg.text, highlight) : seg.text}</span>
        </div>
      ))}
    </div>
  );
}

function mark(text, words) {
  const terms = words.split(/\s+/).filter((w) => w.length >= 2);
  if (!terms.length) return text;
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(pattern).map((part, index) => (pattern.test(part) ? <mark key={index}>{part}</mark> : part));
}
