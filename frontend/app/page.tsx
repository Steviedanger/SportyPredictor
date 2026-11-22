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
  home_xg?: number | null;
  away_xg?: number | null;
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

interface RolloverPick {
  matchId: number;
  league: string;
  kickoff: string;
  fixtureLabel: string;
  pickLabel: string;
  prob: number;
  impliedOdds: number;
}

function getPrediction(preds: MatchRow['predictions']): Prediction | null {
  if (!preds) return null;
  return Array.isArray(preds) ? preds[0] ?? null : preds;
}

function getMatchResultTip(match: MatchRow, pred: Prediction | null) {
  if (!pred) return { label: 'Pending', prob: 0 };

  const homeName = match.home_team?.name ?? 'Home';
  const awayName = match.away_team?.name ?? 'Away';

  const options = [
    { label: `${homeName} Win`, prob: pred.home_win_prob ?? 0 },
    { label: 'Draw', prob: pred.draw_prob ?? 0 },
    { label: `${awayName} Win`, prob: pred.away_win_prob ?? 0 },
  ];

  return options.reduce((best, curr) => (curr.prob > best.prob ? curr : best));
}

function getGoalsTipFromScoreline(pred: Prediction | null) {
  if (!pred) return { label: 'Pending', prob: 0 };

  // The single likeliest scoreline is almost always low (1-0, 1-1, 0-1) even when
  // expected goals are high, so the goals tip follows the over-2.5 probability.
  const overProb = pred.over_25_prob ?? 0;
  const underProb = Math.max(0, 1 - overProb);

  if (overProb >= underProb) {
    return { label: 'Over 2.5 Goals', prob: overProb };
  }
  return { label: 'Under 2.5 Goals', prob: underProb };
}

function isConfidentTeamElo(elo?: number): boolean {
  if (elo === undefined || elo === null) return false;
  // Exclude uncalibrated/default ~1500 teams where we lack strong rating separation
  return Math.abs(elo - 1500) >= 25;
}

function buildDailyThreeOddsRollover(matches: MatchRow[]): {
  picks: RolloverPick[];
  combinedOdds: number;
} {
  const candidates: RolloverPick[] = [];

  for (const match of matches) {
    const pred = getPrediction(match.predictions);
    if (!pred) continue;

    const homeElo = match.home_team?.elo;
    const awayElo = match.away_team?.elo;

    // Skip if either team is uncertain / still at default 1500 baseline
    if (!isConfidentTeamElo(homeElo) || !isConfidentTeamElo(awayElo)) {
      continue;
    }

    const homeName = match.home_team?.name ?? 'Home';
    const awayName = match.away_team?.name ?? 'Away';
    const goalsTip = getGoalsTipFromScoreline(pred);

    const marketChoices = [
      { label: `${homeName} Win`, prob: pred.home_win_prob ?? 0 },
      { label: `${awayName} Win`, prob: pred.away_win_prob ?? 0 },
      { label: goalsTip.label, prob: goalsTip.prob },
    ];

    const topMarket = marketChoices.reduce((best, curr) =>
      curr.prob > best.prob ? curr : best
    );

    // Require solid conviction so uncertain fixtures never enter the 3-odds rollover
    if (topMarket.prob < 0.54) continue;

    // Approximate decimal odds with a modest margin factor (~0.92)
    const rawFairOdds = topMarket.prob > 0 ? 1 / topMarket.prob : 1;
    const impliedOdds = Number(Math.max(1.12, rawFairOdds * 0.92).toFixed(2));

    candidates.push({
      matchId: match.id,
      league: match.league || 'Competition',
      kickoff: match.kickoff,
      fixtureLabel: `${homeName} vs ${awayName}`,
      pickLabel: topMarket.label,
      prob: topMarket.prob,
      impliedOdds,
    });
  }

  // Sort by highest probability first, then select up to 3 picks targeting ~3.00 combined odds
  candidates.sort((a, b) => b.prob - a.prob);

  const selected: RolloverPick[] = [];
  let runningOdds = 1.0;

  for (const pick of candidates) {
    if (selected.length >= 3) break;
    const nextOdds = runningOdds * pick.impliedOdds;
    // Avoid blowing past ~3.60x if we already have 2 solid picks near 3.00x
    if (selected.length >= 2 && nextOdds > 3.6) continue;
    selected.push(pick);
    runningOdds = nextOdds;
  }

  return {
    picks: selected,
    combinedOdds: Number(runningOdds.toFixed(2)),
  };
}

function toDayKey(isoString: string) {
  const d = new Date(isoString);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatDayTabLabel(dayKey: string) {
  const [y, m, d] = dayKey.split('-').map(Number);
  const dateObj = new Date(y, m - 1, d);
  const shortDate = dateObj.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });

  const now = new Date();
  const todayKey = toDayKey(now.toISOString());
  const tomorrowKey = toDayKey(new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString());

  if (dayKey === todayKey) return `Today · ${shortDate}`;
  if (dayKey === tomorrowKey) return `Tomorrow · ${shortDate}`;
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

      // Past matches keep stale predictions (the predictor only refreshes upcoming
      // games), so only load fixtures from the start of today onward.
      const startOfToday = new Date();
      startOfToday.setHours(0, 0, 0, 0);

      const { data, error } = await supabase
        .from('matches')
        .select(`
          id,
          league,
          kickoff,
          status,
          home_team:teams!matches_home_team_id_fkey(name, elo),
          away_team:teams!matches_away_team_id_fkey(name, elo),
          predictions(home_win_prob, draw_prob, away_win_prob, over_25_prob, predicted_score, home_xg, away_xg)
        `)
        .gte('kickoff', startOfToday.toISOString())
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

  const rollover = useMemo(
    () => buildDailyThreeOddsRollover(dayMatches),
    [dayMatches]
  );

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
    <div className="space-y-7">
      {/* Day Selector Bar */}
      <div className="flex flex-wrap items-center gap-2">
        {dayKeys.map((key) => {
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
              {formatDayTabLabel(key)}
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

      {/* Dedicated Daily 3-Odds Rollover Section (Max 3 Picks, Confident Teams Only) */}
      <section className="bg-gradient-to-br from-emerald-950/50 via-slate-900 to-slate-900 border border-emerald-700/40 rounded-2xl p-5 sm:p-6 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div>
            <div className="inline-flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-emerald-400">
              <span>Daily Rollover Slip</span>
              <span className="px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/40">
                Max 3 Picks
              </span>
            </div>
            <h2 className="text-lg font-extrabold text-white mt-1">
              High-Conviction 3-Odds Rollover
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Includes only matches with calibrated Elo separation and high model probability; uncertain teams are excluded.
            </p>
          </div>

          {rollover.picks.length > 0 && (
            <div className="bg-slate-950/90 border border-emerald-500/40 rounded-xl px-4 py-2.5 self-start sm:self-auto">
              <span className="text-[10px] uppercase tracking-wider text-slate-400 block">
                Est. Combined Odds
              </span>
              <span className="text-xl font-black font-mono text-emerald-400">
                ~{rollover.combinedOdds.toFixed(2)}x
              </span>
            </div>
          )}
        </div>

        {rollover.picks.length === 0 ? (
          <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 text-xs text-slate-400">
            No high-confidence picks met the rollover threshold for this selection—skipping uncertain matchups rather than forcing picks.
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            {rollover.picks.map((item, idx) => (
              <div
                key={item.matchId}
                className="bg-slate-950/85 border border-slate-800 rounded-xl p-4 flex flex-col justify-between gap-3"
              >
                <div>
                  <div className="flex items-center justify-between text-[11px] text-slate-400 mb-1.5">
                    <span className="font-mono text-emerald-400 font-bold">
                      LEG #{idx + 1}
                    </span>
                    <span className="truncate max-w-[150px]">{item.league}</span>
                  </div>
                  <div className="text-sm font-bold text-white leading-snug">
                    {item.fixtureLabel}
                  </div>
                </div>

                <div className="pt-2.5 border-t border-slate-800/80 flex items-center justify-between text-xs">
                  <span className="font-bold text-emerald-300">{item.pickLabel}</span>
                  <span className="font-mono text-slate-300">
                    {(item.prob * 100).toFixed(0)}% · ~{item.impliedOdds.toFixed(2)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

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
                  const resultTip = getMatchResultTip(match, pred);
                  const goalsTip = getGoalsTipFromScoreline(pred);
                  const homeName = match.home_team?.name ?? 'Home Team';
                  const awayName = match.away_team?.name ?? 'Away Team';

                  return (
                    <article
                      key={match.id}
                      className="bg-slate-900 border border-slate-800 rounded-2xl p-5 hover:border-emerald-500/40 transition-colors"
                    >
                      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
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
                              {match.home_team?.elo !== undefined
                                ? `Home Elo ${Math.round(match.home_team.elo)}`
                                : ''}
                              {match.home_team?.elo !== undefined &&
                              match.away_team?.elo !== undefined
                                ? ' · '
                                : ''}
                              {match.away_team?.elo !== undefined
                                ? `Away Elo ${Math.round(match.away_team.elo)}`
                                : ''}
                            </div>
                          </div>
                        </div>

                        {/* Separate 1X2 Tip + Goals Tip + Scoreline */}
                        <div className="flex flex-wrap items-center gap-2 shrink-0">
                          {pred ? (
                            <>
                              <div className="inline-flex items-center gap-1.5 bg-emerald-950/80 border border-emerald-700/60 text-emerald-300 px-3 py-1.5 rounded-xl text-xs font-bold">
                                <span className="uppercase text-[10px] tracking-wider bg-emerald-500 text-slate-950 px-1.5 py-0.5 rounded font-black">
                                  1X2 TIP
                                </span>
                                <span>{resultTip.label}</span>
                                <span className="font-mono text-emerald-400">
                                  ({(resultTip.prob * 100).toFixed(0)}%)
                                </span>
                              </div>

                              <div className="inline-flex items-center gap-1.5 bg-amber-950/50 border border-amber-700/50 text-amber-300 px-3 py-1.5 rounded-xl text-xs font-bold">
                                <span className="uppercase text-[10px] tracking-wider bg-amber-400 text-slate-950 px-1.5 py-0.5 rounded font-black">
                                  GOALS
                                </span>
                                <span>{goalsTip.label}</span>
                                <span className="font-mono text-amber-400">
                                  ({(goalsTip.prob * 100).toFixed(0)}%)
                                </span>
                              </div>

                              {pred.home_xg != null && pred.away_xg != null && (
                                <div className="bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-xl text-xs">
                                  <span className="text-slate-400 mr-1.5">Exp. goals:</span>
                                  <span className="font-mono font-bold text-slate-100">
                                    {pred.home_xg.toFixed(1)} – {pred.away_xg.toFixed(1)}
                                  </span>
                                </div>
                              )}

                              <div className="bg-slate-950 border border-slate-800 px-3 py-1.5 rounded-xl text-xs">
                                <span className="text-slate-400 mr-1.5">Likeliest score:</span>
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
            Daily Football Tips, 3-Odds Rollover Slip &amp; Poisson + Elo Model Picks
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