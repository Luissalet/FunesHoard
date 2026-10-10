import { useEffect, useState } from 'react';
import { Bot, CheckCircle2, GitCommit, RefreshCw } from 'lucide-react';
import { api, errorMessage, formatClock, formatLongDate, type AgentDay, type AgentEventKind } from '../api';
import { STRINGS, type Lang } from '../i18n';

export function AgentLane({ day, lang }: { day: string; lang: Lang }) {
  const t = STRINGS[lang];
  const [data, setData] = useState<AgentDay | null>(null);
  const [kind, setKind] = useState<AgentEventKind | ''>('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    setBusy(true); setError('');
    if (offset === 0) setData(null);
    api.agentsDay({ day, kind: kind || undefined, offset, limit: 10 })
      .then(next => {
        if (cancelled) return;
        setData(previous => offset && previous ? { ...next, items: [...previous.items, ...next.items] } : next);
      })
      .catch(err => !cancelled && setError(errorMessage(err)))
      .finally(() => !cancelled && setBusy(false));
    return () => { cancelled = true; };
  }, [day, kind, offset, refresh]);
  const names: Record<string, string> = { bus: 'Hoard Hub', agora: 'Ágora', git: 'Git' };
  const labels = { turn: t.agents_turns, task: t.agents_tasks, commit: t.commits };
  const states: Record<string, string> = { finished: t.agents_finished, completed: t.agents_finished,
    failed: t.agents_failed, cancelled: t.agents_cancelled };
  return <section className="card agent-lane" aria-labelledby="agents-day-title" data-testid="agents-lane">
    <div className="agent-lane__head">
      <h2 id="agents-day-title">{t.agents_day}</h2>
      <button className="btn btn-small" onClick={() => { setOffset(0); setRefresh(n => n + 1); }} disabled={busy} aria-label={t.agents_refresh}>
        <RefreshCw size={15} /> {t.agents_refresh}
      </button>
    </div>
    <p className="muted agent-lane__intro">{t.agents_intro}</p>
    <div className="agent-lane__filters" aria-label={t.agents_filter}>
      <button className="btn btn-small" aria-pressed={!kind} onClick={() => { setKind(''); setOffset(0); }}>{t.agents_all}</button>
      {(['turn', 'task', 'commit'] as const).map(value => <button className="btn btn-small" key={value} aria-pressed={kind === value}
        onClick={() => { setKind(value); setOffset(0); }}>{labels[value]}{data && ` · ${data.totals[value]}`}</button>)}
    </div>
    {data && Object.entries(data.sources).map(([name, source]) => !source.ok ?
      <p className="agent-lane__notice" key={name}>{names[name] ?? name}: {t.agents_unavailable}</p> : source.truncated ?
      <p className="agent-lane__notice" key={name}>{names[name] ?? name}: {source.reason === 'retained_window' && source.since != null
        ? `${t.agents_retained} ${formatLongDate(source.since, lang)}.` : t.agents_partial}</p> : null)}
    {error && <p role="alert" className="call-error">{error}</p>}
    {busy && !data && <p role="status">{t.agents_loading}</p>}
    {data && !data.items.length && <p className="muted">{t.agents_empty}</p>}
    {data && <ol className="agent-lane__events" aria-label={t.agents_events}>
      {data.items.map(item => <li key={item.ref} className="agent-lane__event" data-kind={item.kind}>
        <time dateTime={new Date(item.ts * 1000).toISOString()}>{formatClock(item.ts, lang)}</time>
        <span className="agent-lane__icon" aria-hidden="true">{item.kind === 'turn' ? <Bot size={17} /> : item.kind === 'task' ? <CheckCircle2 size={17} /> : <GitCommit size={17} />}</span>
        <div className="agent-lane__body">
          <div className="agent-lane__title">{item.kind === 'turn' ? t.agents_turn_title : item.title}</div>
          <div className="agent-lane__meta">{[item.agent, item.repo, item.kind === 'turn' ? states[item.status] : '', `${names[item.source] ?? item.source} · ${item.source_id}`].filter(Boolean).join(' · ')}</div>
        </div>
      </li>)}
    </ol>}
    {data?.has_more && <button className="btn" disabled={busy} onClick={() => setOffset(data.next_offset ?? 0)}>{busy ? t.agents_loading : t.agents_more}</button>}
  </section>;
}
