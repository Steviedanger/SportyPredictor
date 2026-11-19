import { createClient } from '@supabase/supabase-js'

// Initialize Supabase client for the frontend
const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || '',
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
)

async function getMatchesWithPredictions() {
  // Fetch matches along with their calculated predictions
  const { data, error } = await supabase
    .from('matches')
    .select(`
      id,
      league,
      kickoff,
      status,
      home_team:teams!matches_home_team_id_fkey(name),
      away_team:teams!matches_away_team_id_fkey(name),
      predictions(home_win_prob, draw_prob, away_win_prob, over_25_prob, predicted_score)
    `)
    .order('kickoff', { ascending: true })

  if (error) {
    console.error('Error fetching data:', error)
    return []
  }

  return data || []
}

export default async function Home() {
  const matches = await getMatchesWithPredictions()

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-6 md:p-12">
      <div className="max-w-4xl mx-auto">
        <header className="mb-10 text-center">
          <h1 className="text-4xl font-extrabold tracking-tight text-emerald-400 mb-2">
            ⚽ SportyPredictor
          </h1>
          <p className="text-slate-400">
            Automated Poisson & Elo Football Match Predictions
          </p>
        </header>

        {matches.length === 0 ? (
          <div className="text-center py-16 bg-slate-900 rounded-2xl border border-slate-800">
            <p className="text-slate-400">No matches or predictions found yet. Run your Python pipeline!</p>
          </div>
        ) : (
          <div className="grid gap-6">
            {matches.map((match: any) => {
              const pred = match.predictions?.[0] || {}
              const homeName = match.home_team?.name || 'Home Team'
              const awayName = match.away_team?.name || 'Away Team'

              return (
                <div 
                  key={match.id} 
                  className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl hover:border-emerald-500/50 transition-all"
                >
                  <div className="flex justify-between items-center text-xs text-slate-400 mb-4">
                    <span className="bg-emerald-950 text-emerald-400 px-3 py-1 rounded-full font-medium border border-emerald-800/50">
                      {match.league || 'League Match'}
                    </span>
                    <span>{new Date(match.kickoff).toLocaleString()}</span>
                  </div>

                  <div className="flex justify-between items-center my-6">
                    <div className="text-lg font-bold text-center w-5/12 truncate">{homeName}</div>
                    <div className="text-slate-500 font-mono text-sm px-2">VS</div>
                    <div className="text-lg font-bold text-center w-5/12 truncate">{awayName}</div>
                  </div>

                  {pred.predicted_score ? (
                    <div className="mt-6 pt-4 border-t border-slate-800 grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                      <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                        <div className="text-xs text-slate-400 mb-1">Score Prediction</div>
                        <div className="text-lg font-black text-emerald-400 font-mono">{pred.predicted_score}</div>
                      </div>
                      <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                        <div className="text-xs text-slate-400 mb-1">Home Win</div>
                        <div className="text-lg font-semibold text-slate-200">{(pred.home_win_prob * 100).toFixed(1)}%</div>
                      </div>
                      <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                        <div className="text-xs text-slate-400 mb-1">Draw</div>
                        <div className="text-lg font-semibold text-slate-200">{(pred.draw_prob * 100).toFixed(1)}%</div>
                      </div>
                      <div className="bg-slate-950 p-3 rounded-xl border border-slate-800">
                        <div className="text-xs text-slate-400 mb-1">Over 2.5 Goals</div>
                        <div className="text-lg font-semibold text-slate-200">{(pred.over_25_prob * 100).toFixed(1)}%</div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center text-sm text-slate-500 mt-4">Prediction pending model run...</div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </main>
  )
}