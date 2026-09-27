import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtClock } from "../format.js";

/** Native audio element plus a waveform-lite bar (peaks) that seeks on click. */
export default function Player({ sessionId, duration, seekTo, onTime }) {
  const audio = useRef(null);
  const [peaks, setPeaks] = useState([]);
  const [time, setTime] = useState(0);
  const total = (audio.current && audio.current.duration) || duration || 0;

  useEffect(() => {
    let alive = true;
    api.peaks(sessionId, 240).then((r) => alive && setPeaks(r.peaks)).catch(() => {});
    return () => { alive = false; };
  }, [sessionId]);

  useEffect(() => {
    if (seekTo == null || !audio.current) return;
    audio.current.currentTime = seekTo.t;
    audio.current.play().catch(() => {});
  }, [seekTo]);

  const onUpdate = () => {
    const t = audio.current ? audio.current.currentTime : 0;
    setTime(t);
    if (onTime) onTime(t);
  };
  const clickWave = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    if (audio.current && total) audio.current.currentTime = ratio * total;
  };
  const played = total ? time / total : 0;

  return (
    <div>
      <div className="wave" onClick={clickWave} role="slider" aria-label="Posición" aria-valuemin={0} aria-valuemax={Math.round(total)} aria-valuenow={Math.round(time)}>
        {peaks.map((p, index) => (
          <i key={index} className={index / peaks.length <= played ? "played" : ""} style={{ height: `${Math.max(4, p * 100)}%` }} />
        ))}
        <span className="cursor" style={{ left: `${played * 100}%` }} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <audio ref={audio} controls preload="metadata" src={api.audioUrl(sessionId)} onTimeUpdate={onUpdate} onSeeked={onUpdate} className="min-w-0 flex-1" style={{ height: 36 }} />
        <span className="help num">{fmtClock(time)} / {fmtClock(total)}</span>
      </div>
    </div>
  );
}
