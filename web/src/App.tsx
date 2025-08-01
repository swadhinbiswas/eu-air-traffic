import { useEffect, useState } from "react"
import { Plane, Activity, Clock, Database, CheckCircle2, XCircle } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { BarChart, Bar, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer, LineChart, Line, CartesianGrid } from "recharts"
import * as duckdb from "@duckdb/duckdb-wasm"

export default function App() {
  const [stats, setStats] = useState({
    flights: "--",
    delay: "--",
    airports: "--",
    size: "16.5 MB"
  })
  
  const [airportData, setAirportData] = useState<any[]>([])
  const [airlineData, setAirlineData] = useState<any[]>([])
  const [status, setStatus] = useState("Initializing WebAssembly Engine...")
  const [isConnected, setIsConnected] = useState(false)

  useEffect(() => {
    async function initDuckDB() {
      try {
        setStatus("Downloading DuckDB-WASM bundles...")
        const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles()
        const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES)
        
        const worker_url = URL.createObjectURL(
          new Blob([`importScripts("${bundle.mainWorker}");`], {type: 'text/javascript'})
        )
        const worker = new Worker(worker_url)
        const logger = new duckdb.ConsoleLogger()
        const db = new duckdb.AsyncDuckDB(logger, worker)
        
        setStatus("Instantiating WebAssembly...")
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker)
        URL.revokeObjectURL(worker_url)

        setStatus("Connecting to Hugging Face Data Lake...")
        
        const conn = await db.connect()
        const HF_BASE = 'https://huggingface.co/datasets/swadhinbiswas/air-traffic/resolve/main'

        // Create views over the remote Parquet files
        await conn.query(`
          CREATE OR REPLACE VIEW fact_flights AS 
          SELECT * FROM read_parquet('${HF_BASE}/flights/data.parquet')
        `)
        
        await conn.query(`
          CREATE OR REPLACE VIEW dim_airport AS 
          SELECT * FROM read_parquet('${HF_BASE}/airports/airports.parquet')
        `)
        
        await conn.query(`
          CREATE OR REPLACE VIEW gold_airport_metrics AS 
          SELECT * FROM read_parquet('${HF_BASE}/airport_metrics/airport_metrics.parquet')
        `)
        
        await conn.query(`
          CREATE OR REPLACE VIEW gold_airline_rankings AS 
          SELECT * FROM read_parquet('${HF_BASE}/airline_rankings/airline_rankings.parquet')
        `)

        setIsConnected(true)
        setStatus("Connected to HF Parquet Lake (Live)")

        // Queries
        const flightsRes = await conn.query(`SELECT COUNT(*) as total FROM fact_flights`)
        const totalFlights = Number(flightsRes.toArray()[0].total)
        
        const delayRes = await conn.query(`SELECT AVG(delay_minutes) as avg_d FROM fact_flights WHERE status != 'cancelled'`)
        const avgDelay = Number(delayRes.toArray()[0].avg_d).toFixed(1)
        
        const airportsRes = await conn.query(`SELECT COUNT(*) as c FROM dim_airport`)
        const totalAirports = Number(airportsRes.toArray()[0].c)

        setStats(prev => ({
          ...prev,
          flights: totalFlights.toLocaleString(),
          delay: avgDelay,
          airports: totalAirports.toLocaleString()
        }))

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

      } catch (err: any) {
        setStatus("Connection Failed: " + err.message)
        setIsConnected(false)
      }
    }
    
    initDuckDB()
  }, [])

  return (
    <div className="min-h-screen bg-background p-8 font-sans antialiased text-foreground">
      <div className="max-w-7xl mx-auto space-y-8">
        
        {/* Header */}
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Air Traffic Edge</h1>
            <p className="text-muted-foreground">European aviation analytics powered by DuckDB-WASM</p>
          </div>
          <div className="flex items-center gap-2 bg-secondary px-4 py-2 rounded-full text-sm font-medium">
            {isConnected ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : <XCircle className="w-4 h-4 text-rose-500" />}
            {status}
          </div>
        </div>

        {/* KPIs */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total Flights</CardTitle>
              <Plane className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{stats.flights}</div>
              <p className="text-xs text-muted-foreground mt-1 text-emerald-500 font-medium">Live from Hugging Face</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Avg Delay</CardTitle>
              <Clock className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{stats.delay} <span className="text-lg text-muted-foreground">min</span></div>
              <p className="text-xs text-muted-foreground mt-1 text-rose-500 font-medium">System-wide average</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Active Airports</CardTitle>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{stats.airports}</div>
              <p className="text-xs text-muted-foreground mt-1">European coverage</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Data Size</CardTitle>
              <Database className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold">{stats.size}</div>
              <p className="text-xs text-muted-foreground mt-1 text-emerald-500 font-medium">Zero-copy DuckDB cache</p>
            </CardContent>
          </Card>
        </div>

        {/* Charts */}
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="col-span-1">
            <CardHeader>
              <CardTitle>Top Airports by Volume</CardTitle>
            </CardHeader>
            <CardContent className="h-[350px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={airportData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                  <XAxis dataKey="name" stroke="hsl(var(--muted-foreground))" fontSize={12} tickLine={false} axisLine={false} />
                  <YAxis stroke="hsl(var(--muted-foreground))" fontSize={12} tickLine={false} axisLine={false} />
                  <RechartsTooltip cursor={{fill: 'hsl(var(--muted))'}} contentStyle={{ borderRadius: '8px', border: '1px solid hsl(var(--border))' }} />
                  <Bar dataKey="total" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
          
          <Card className="col-span-1">
            <CardHeader>
              <CardTitle>Airline On-Time Performance</CardTitle>
            </CardHeader>
            <CardContent className="h-[350px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={airlineData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                  <XAxis dataKey="name" stroke="hsl(var(--muted-foreground))" fontSize={12} tickLine={false} axisLine={false} />
                  <YAxis stroke="hsl(var(--muted-foreground))" fontSize={12} tickLine={false} axisLine={false} domain={['auto', 100]} />
                  <RechartsTooltip contentStyle={{ borderRadius: '8px', border: '1px solid hsl(var(--border))' }} />
                  <Line type="monotone" dataKey="otp" stroke="#10b981" strokeWidth={3} dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
