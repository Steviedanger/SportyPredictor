'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import { createClient } from '@supabase/supabase-js';

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);

interface Prediction {
  home_win_prob: number;
  draw_prob: number;
  away_win_prob: number;
  over_25_prob: number;
  predicted_score: string;
}

interface MatchRow {
  id: number;
  league: string | null;
  kickoff: string;
  status: string | null;
  home_team: { name: string; elo?: number } | null;
  away_team: { name: string; elo?: number } | null;
  predictions: Prediction[] | Prediction | null;
}

function getPrediction(preds: MatchRow['predictions']): Prediction | null {
  if (!preds) return null;
  return Array.isArray(preds) ? preds[0] ?? null : preds;
}

function getBestTip(match: MatchRow, pred: Prediction | null) {
  if (!pred) return { label: 'Pending', confidence: null };

  const homeName = match.home_team?.name ?? 'Home';
  const awayName = match.away_team?.name ?? 'Away';

  const candidates = [
    { label: `${homeName} Win`, prob: pred.home_win_prob ?? 0 },
    { label: 'Draw', prob: pred.draw_prob ?? 0 },
    { label: `${awayName} Win`, prob: pred.away_win_prob ?? 0 },
    { label: 'Over 2.5 Goals', prob: pred.over_25_prob ?? 0 },
  ];

  const best = candidates.reduce((prev, curr) =>
    curr.prob > prev.prob ? curr : prev
  );

  return {
    label: best.label,
    confidence: `${(best.prob * 100).toFixed(0)}%`,
  };
}

function toDayKey(isoString: string) {
  const d = new Date(isoString);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatDayTabLabel(dayKey: string, index: number) {
  const [y, m, d] = dayKey.split('-').map(Number);
  const dateObj = new Date(y, m - 1, d);
  const shortDate = dateObj.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });

  if (index === 0) return `Today · ${shortDate}`;
  if (index === 1) return `Tomorrow · ${shortDate}`;
  return shortDate;
}

function formatPct(val?: number) {
  if (val === undefined || val === null) return '—';
  return `${(val * 100).toFixed(1)}%`;
}

function PredictionsBoard() {
  const [matches, setMatches] = useState<MatchRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState<string>('ALL');
  const [selectedLeague, setSelectedLeague] = useState<string>('ALL');

  useEffect(() => {
    let active = true;

    async function load() {
      setLoading(true);
      const { data, error } = await supabase
        .from('matches')
        .select(`
          id,
          league,
          kickoff,
          status,
          home_team:teams!matches_home_team_id_fkey(name, elo),
          away_team:teams!matches_away_team_id_fkey(name, elo),
          predictions(home_win_prob, draw_prob, away_win_prob, over_25_prob, predicted_score)
        `)
        .order('kickoff', { ascending: true });

      if (!active) return;

      if (error) {
        setErrorMsg(error.message);
        setMatches([]);
      } else {
        const rows = (data as unknown as MatchRow[]) || [];
        setMatches(rows);
        if (rows.length > 0) {
          setSelectedDay(toDayKey(rows[0].kickoff));
        }
      }
      setLoading(false);
    }

    load();
    return () => {
      active = false;
    };
  }, []);

  const dayKeys = useMemo(() => {
    const unique = Array.from(new Set(matches.map((m) => toDayKey(m.kickoff))));
    return unique.sort();
  }, [matches]);

  const dayMatches = useMemo(() => {
    if (selectedDay === 'ALL') return matches;
    return matches.filter((m) => toDayKey(m.kickoff) === selectedDay);
  }, [matches, selectedDay]);

  const leagues = useMemo(() => {
    const names = Array.from(
      new Set(dayMatches.map((m) => m.league || 'Other Competitions'))
    );
    return names.sort();
  }, [dayMatches]);

  useEffect(() => {
    if (selectedLeague !== 'ALL' && !leagues.includes(selectedLeague)) {
      setSelectedLeague('ALL');
    }
  }, [leagues, selectedLeague]);

  const groupedByLeague = useMemo(() => {
    const filtered =
      selectedLeague === 'ALL'
        ? dayMatches
        : dayMatches.filter(
            (m) => (m.league || 'Other Competitions') === selectedLeague
          );

    const map = new Map<string, MatchRow[]>();
    for (const match of filtered) {
      const leagueName = match.league || 'Other Competitions';
      const existing = map.get(leagueName) ?? [];
      existing.push(match);
      map.set(leagueName, existing);
    }

    return Array.from(map.entries());
  }, [dayMatches, selectedLeague]);

  const totalVisible = groupedByLeague.reduce((acc, [, list]) => acc + list.length, 0);

  if (loading) {
    return (
      <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-12 text-center text-sm text-slate-400">
        Loading daily football tips &amp; model probabilities...
      </div>
    );
  }

  if (errorMsg) {
    return (
      <div className="bg-rose-950/30 border border-rose-800/60 rounded-2xl p-8 text-center">
        <p className="text-rose-300 font-semibold">Failed to load predictions</p>
        <p className="text-xs text-rose-400/80 mt-1">{errorMsg}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Day Selector Bar (BettingTips.Today / Free Super Tips style) */}
      <div className="flex flex-wrap items-center gap-2">
        {dayKeys.map((key, idx) => {
          const active = selectedDay === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setSelectedDay(key)}
              className={`px-3.5 py-2 rounded-xl text-xs font-semibold border transition-colors cursor-pointer ${
                active
                  ? 'bg-emerald-500 text-slate-950 border-emerald-400'
                  : 'bg-slate-900 text-slate-300 border-slate-800 hover:border-slate-700'
              }`}
            >
              {formatDayTabLabel(key, idx)}
            </button>
          );
        })}

        {dayKeys.length > 1 && (
          <button
            type="button"
            onClick={() => setSelectedDay('ALL')}
            className={`px-3.5 py-2 rounded-xl text-xs font-semibold border transition-colors cursor-pointer ${
              selectedDay === 'ALL'
                ? 'bg-emerald-500 text-slate-950 border-emerald-400'
                : 'bg-slate-900 text-slate-400 border-slate-800 hover:border-slate-700'
            }`}
          >
            All Days ({matches.length})
          </button>
        )}
      </div>

      {/* League Filter Strip */}
      {leagues.length > 0 && (
        <div className="flex items-center gap-2 overflow-x-auto pb-1">
          <button
            type="button"
            onClick={() => setSelectedLeague('ALL')}
            className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors cursor-pointer ${
              selectedLeague === 'ALL'
                ? 'bg-slate-100 text-slate-950 border-slate-100'
                : 'bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200'
            }`}
          >
            All Leagues ({dayMatches.length})
          </button>

          {leagues.map((league) => {
            const count = dayMatches.filter(
              (m) => (m.league || 'Other Competitions') === league
            ).length;
            const active = selectedLeague === league;
            return (
              <button
                key={league}
                type="button"
                onClick={() => setSelectedLeague(league)}
                className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors cursor-pointer ${
                  active
                    ? 'bg-slate-100 text-slate-950 border-slate-100'
                    : 'bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200'
                }`}
              >
                {league} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* Grouped League Sections */}
      {totalVisible === 0 ? (
        <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-12 text-center">
          <p className="text-slate-300 font-medium">No fixtures for this day/filter yet.</p>
          <p className="text-xs text-slate-500 mt-1">
            Switch dates above or run the SportyPredictor pipeline to ingest upcoming matches.
          </p>
        </div>
      ) : (
        <div className="space-y-8">
          {groupedByLeague.map(([leagueName, leagueMatches]) => (
            <section key={leagueName} className="space-y-3">
              <div className="flex items-center justify-between bg-slate-900/90 border border-slate-800 px-4 py-2.5 rounded-xl">
                <h2 className="text-sm font-bold text-slate-100 tracking-wide">
                  {leagueName}
                </h2>
                <span className="text-xs font-mono text-emerald-400">
                  {leagueMatches.length} {leagueMatches.length === 1 ? 'match' : 'matches'}
                </span>
              </div>

              <div className="grid gap-3">
                {leagueMatches.map((match) => {
                  const pred = getPrediction(match.predictions);
                  const tip = getBestTip(match, pred);
                  const homeName = match.home_team?.name ?? 'Home Team';
                  const awayName = match.away_team?.name ?? 'Away Team';

                  return (
                    <article
                      key={match.id}
                      className="bg-slate-900 border border-slate-800 rounded-2xl p-5 hover:border-emerald-500/40 transition-colors"
                    >
                      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
                        {/* Kickoff + Teams */}
                        <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-5 min-w-0">
                          <time
                            dateTime={match.kickoff}
                            className="text-xs font-mono text-slate-400 bg-slate-950 border border-slate-800 px-2.5 py-1.5 rounded-lg self-start shrink-0"
                          >
                            {new Date(match.kickoff).toLocaleTimeString('en-GB', {
                              hour: '2-digit',
                              minute: '2-digit',
                            })}
                          </time>

                          <div className="min-w-0">
                            <div className="text-base font-bold text-white truncate">
                              {homeName}{' '}
                              <span className="text-slate-500 font-normal mx-1">vs</span>{' '}
                              {awayName}
                            </div>
                            <div className="text-xs text-slate-500 font-mono mt-0.5">
                              {match.home_team?.elo ? `Home Elo ${Math.round(match.home_team.elo)}` : ''}
                              {match.home_team?.elo && match.away_team?.elo ? ' · ' : ''}
                              {match.away_team?.elo ? `Away Elo ${Math.round(match.away_team.elo)}` : ''}
                            </div>
                          </div>
                        </div>

                        {/* Model Tip + Scoreline */}
                        <div className="flex flex-wrap items-center gap-2.5 shrink-0">
                          {pred ? (
                            <>
                              <div className="inline-flex items-center gap-2 bg-emerald-950/80 border border-emerald-700/60 text-emerald-300 px-3 py-1.5 rounded-xl text-xs font-bold">
                                <span className="uppercase text-[10px] tracking-wider bg-emerald-500 text-slate-950 px-1.5 py-0.5 rounded font-black">
                                  TIP
                                </span>
                                <span>{tip.label}</span>
                                {tip.confidence && (
                                  <span className="font-mono text-emerald-400">
                                    ({tip.confidence})
                                  </span>
                                )}
                              </div>

                              <div className="bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-xl text-xs">
                                <span className="text-slate-400 mr-1.5">Score:</span>
                                <span className="font-mono font-bold text-slate-100">
                                  {pred.predicted_score}
                                </span>
                              </div>
                            </>
                          ) : (
                            <span className="text-xs text-slate-500">Awaiting tip</span>
                          )}
                        </div>
                      </div>

                      {/* Market Breakdown Row */}
                      {pred && (
                        <div className="mt-4 pt-3.5 border-t border-slate-800/80 grid grid-cols-4 gap-2 text-center text-xs">
                          <div className="bg-slate-950/70 rounded-lg py-2 px-2 border border-slate-800/60">
                            <span className="text-slate-500 block text-[10px] uppercase">1 (Home)</span>
                            <span className="font-semibold text-slate-200">
                              {formatPct(pred.home_win_prob)}
                            </span>
                          </div>
                          <div className="bg-slate-950/70 rounded-lg py-2 px-2 border border-slate-800/60">
                            <span className="text-slate-500 block text-[10px] uppercase">X (Draw)</span>
                            <span className="font-semibold text-slate-200">
                              {formatPct(pred.draw_prob)}
                            </span>
                          </div>
                          <div className="bg-slate-950/70 rounded-lg py-2 px-2 border border-slate-800/60">
                            <span className="text-slate-500 block text-[10px] uppercase">2 (Away)</span>
                            <span className="font-semibold text-slate-200">
                              {formatPct(pred.away_win_prob)}
                            </span>
                          </div>
                          <div className="bg-slate-950/70 rounded-lg py-2 px-2 border border-slate-800/60">
                            <span className="text-slate-500 block text-[10px] uppercase">Over 2.5</span>
                            <span className="font-semibold text-amber-400">
                              {formatPct(pred.over_25_prob)}
                            </span>
                          </div>
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 px-4 py-10 md:px-10">
      <div className="max-w-5xl mx-auto">
        <header className="pb-6 mb-6 border-b border-slate-800">
          <h1 className="text-3xl font-extrabold tracking-tight text-emerald-400">
            SportyPredictor
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Daily Football Tips, League Breakdowns &amp; Poisson + Elo Model Picks
          </p>
        </header>

        <Suspense
          fallback={
            <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-12 text-center text-sm text-slate-400">
              Loading SportyPredictor dashboard...
            </div>
          }
        >
          <PredictionsBoard />
        </Suspense>
      </div>
    </main>
  );
}