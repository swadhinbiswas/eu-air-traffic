import { useEffect, useState } from 'react'
import * as duckdb from '@duckdb/duckdb-wasm'
import duckdb_wasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import mvp_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import duckdb_wasm_eh from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import eh_worker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, AreaChart, Area } from 'recharts'
import { Plane, Activity, Clock, Database, Map as MapIcon, RefreshCcw } from 'lucide-react'
import { motion } from 'framer-motion'

const MANUAL_BUNDLES: duckdb.DuckDBBundles = {
  mvp: {
    mainModule: duckdb_wasm,
    mainWorker: mvp_worker,
  },
  eh: {
    mainModule: duckdb_wasm_eh,
    mainWorker: eh_worker,
  },
}

export default function App() {
  const [_, setDb] = useState<duckdb.AsyncDuckDB | null>(null)
  const [status, setStatus] = useState("Initializing DuckDB-WASM...")
  const [stats, setStats] = useState({ flights: "0", delay: "0", airports: "0", otp: "0" })
  const [airportData, setAirportData] = useState<any[]>([])
  const [airlineData, setAirlineData] = useState<any[]>([])
  const [isConnected, setIsConnected] = useState(false)

  useEffect(() => {
    async function initDuckDB() {
      try {
        const bundle = await duckdb.selectBundle(MANUAL_BUNDLES)
        const worker = new Worker(bundle.mainWorker!)
        const logger = new duckdb.VoidLogger()
        const db = new duckdb.AsyncDuckDB(logger, worker)
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker)
        
        setDb(db)
        setStatus("Connecting to HF Parquet Lake...")
        
        const conn = await db.connect()
        const HF_BASE = 'https://huggingface.co/datasets/swadhinbiswas/air-traffic/resolve/main'

        // Register files explicitly in the VFS to avoid globbing issues
        await db.registerFileURL('data.parquet', `${HF_BASE}/flights/data.parquet`, duckdb.DuckDBDataProtocol.HTTP, false)
        await db.registerFileURL('airports.parquet', `${HF_BASE}/airports/airports.parquet`, duckdb.DuckDBDataProtocol.HTTP, false)
        await db.registerFileURL('airport_metrics.parquet', `${HF_BASE}/airport_metrics/airport_metrics.parquet`, duckdb.DuckDBDataProtocol.HTTP, false)
        await db.registerFileURL('airline_rankings.parquet', `${HF_BASE}/airline_rankings/airline_rankings.parquet`, duckdb.DuckDBDataProtocol.HTTP, false)
        
        // Create views using the local VFS references
        await conn.query(`CREATE OR REPLACE VIEW fact_flights AS SELECT * FROM read_parquet('data.parquet')`)
        await conn.query(`CREATE OR REPLACE VIEW dim_airport AS SELECT * FROM read_parquet('airports.parquet')`)
        await conn.query(`CREATE OR REPLACE VIEW gold_airport_metrics AS SELECT * FROM read_parquet('airport_metrics.parquet')`)
        await conn.query(`CREATE OR REPLACE VIEW gold_airline_rankings AS SELECT * FROM read_parquet('airline_rankings.parquet')`)

        setIsConnected(true)
        setStatus("Connected to Data Lake (Live)")

        // Queries
        const flightsRes = await conn.query(`SELECT COUNT(*) as total FROM fact_flights`)
        const totalFlights = Number(flightsRes.toArray()[0].total)
        
        const delayRes = await conn.query(`SELECT AVG(delay_minutes) as avg_d FROM fact_flights WHERE status != 'cancelled'`)
        const avgDelay = Number(delayRes.toArray()[0].avg_d).toFixed(1)
        
        const airportsRes = await conn.query(`SELECT COUNT(*) as c FROM dim_airport`)
        const totalAirports = Number(airportsRes.toArray()[0].c)

        const optRes = await conn.query(`SELECT AVG(on_time_rate) as otp FROM gold_airline_rankings`)
        const avgOtp = (Number(optRes.toArray()[0].otp) * 100).toFixed(1)

        setStats({
          flights: totalFlights.toLocaleString(),
          delay: avgDelay,
          airports: totalAirports.toLocaleString(),
          otp: avgOtp
        })

        const topAirports = await conn.query(`
          SELECT airport_icao as icao, CAST(total_flights AS DOUBLE) as flights 
          FROM gold_airport_metrics 
          ORDER BY total_flights DESC LIMIT 10
        `)
        setAirportData(topAirports.toArray().map((row: any) => ({
          name: row.icao,
          total: Number(row.flights)
        })))

        const airlines = await conn.query(`
          SELECT airline_icao as icao, CAST(on_time_rate * 100 AS DOUBLE) as otp
          FROM gold_airline_rankings
          ORDER BY total_flights DESC LIMIT 10
        `)
        setAirlineData(airlines.toArray().map((row: any) => ({
          name: row.icao,
          otp: Number(row.otp)
        })))

        await conn.close()
      } catch (err) {
        console.error(err)
        setStatus("Error connecting to data lake.")
      }
    }
    
    initDuckDB()
  }, [])

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-50 font-sans selection:bg-emerald-500/30 pb-20">
      {/* Top Navigation */}
      <header className="sticky top-0 z-50 border-b border-white/10 bg-zinc-950/80 backdrop-blur-xl">
        <div className="container mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-emerald-500/20 p-2 rounded-lg">
              <Plane className="w-5 h-5 text-emerald-500" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-zinc-100">Air Traffic Edge</h1>
          </div>
          
          <div className="flex items-center gap-4">
            <Badge variant={isConnected ? "success" : "secondary"} className="gap-1.5 py-1 px-3">
              {isConnected ? <Activity className="w-3.5 h-3.5" /> : <RefreshCcw className="w-3.5 h-3.5 animate-spin" />}
              {status}
            </Badge>
            <a href="https://github.com/swadhinbiswas/air-traffic" target="_blank" rel="noreferrer" className="text-zinc-400 hover:text-zinc-100 transition-colors">
              <Database className="w-5 h-5" />
            </a>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-6 py-8 space-y-8">
        
        {/* KPIs */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { title: "Total Flights", value: stats.flights, icon: <Activity className="w-4 h-4 text-emerald-500" /> },
            { title: "Avg Delay (mins)", value: stats.delay, icon: <Clock className="w-4 h-4 text-amber-500" /> },
            { title: "On-Time Rate", value: `${stats.otp}%`, icon: <Plane className="w-4 h-4 text-blue-500" /> },
            { title: "Active Airports", value: stats.airports, icon: <MapIcon className="w-4 h-4 text-purple-500" /> }
          ].map((kpi, idx) => (
            <motion.div key={idx} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: idx * 0.1 }}>
              <Card className="bg-zinc-900/50 border-white/5 backdrop-blur-sm">
                <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
                  <CardTitle className="text-sm font-medium text-zinc-400">{kpi.title}</CardTitle>
                  {kpi.icon}
                </CardHeader>
                <CardContent>
                  <div className="text-3xl font-bold text-zinc-100">{kpi.value}</div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>

        {/* Analytics Tabs */}
        <Tabs defaultValue="overview" className="w-full">
          <TabsList className="bg-zinc-900/50 border border-white/5">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="airlines">Airlines</TabsTrigger>
          </TabsList>
          
          <TabsContent value="overview" className="mt-6">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
              <Card className="bg-zinc-900/50 border-white/5 backdrop-blur-sm">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Database className="w-5 h-5 text-zinc-400" />
                    Top Busiest Airports
                  </CardTitle>
                </CardHeader>
                <CardContent className="h-[400px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={airportData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" vertical={false} />
                      <XAxis dataKey="name" stroke="#ffffff50" fontSize={12} tickLine={false} axisLine={false} />
                      <YAxis stroke="#ffffff50" fontSize={12} tickLine={false} axisLine={false} tickFormatter={(v) => `${v/1000}k`} />
                      <Tooltip 
                        contentStyle={{ backgroundColor: '#18181b', borderColor: '#ffffff10', borderRadius: '8px' }}
                        itemStyle={{ color: '#10b981' }}
                        cursor={{fill: '#ffffff05'}}
                      />
                      <Bar dataKey="total" fill="#10b981" radius={[4, 4, 0, 0]} maxBarSize={50} />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            </motion.div>
          </TabsContent>

          <TabsContent value="airlines" className="mt-6">
             <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.2 }}>
              <Card className="bg-zinc-900/50 border-white/5 backdrop-blur-sm">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Plane className="w-5 h-5 text-zinc-400" />
                    Airline On-Time Performance (OTP)
                  </CardTitle>
                </CardHeader>
                <CardContent className="h-[400px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={airlineData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                      <defs>
                        <linearGradient id="colorOtp" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                          <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" vertical={false} />
                      <XAxis dataKey="name" stroke="#ffffff50" fontSize={12} tickLine={false} axisLine={false} />
                      <YAxis stroke="#ffffff50" fontSize={12} tickLine={false} axisLine={false} domain={[0, 100]} tickFormatter={(v) => `${v}%`} />
                      <Tooltip 
                        contentStyle={{ backgroundColor: '#18181b', borderColor: '#ffffff10', borderRadius: '8px' }}
                        itemStyle={{ color: '#3b82f6' }}
                      />
                      <Area type="monotone" dataKey="otp" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#colorOtp)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            </motion.div>
          </TabsContent>
        </Tabs>
      </main>
    </div>
  )
}
