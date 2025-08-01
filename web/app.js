import * as duckdb from 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.28.0/+esm';

// UI Elements
const consoleEl = document.getElementById('console-logs');
const dotEl = document.getElementById('connection-dot');
const statusEl = document.getElementById('connection-status');

// Helper to log to the on-screen terminal
function log(msg) {
    const time = new Date().toISOString().split('T')[1].slice(0, 8);
    consoleEl.innerHTML += `[${time}] ${msg}\n`;
    consoleEl.scrollTop = consoleEl.scrollHeight;
    console.log(`[Edge] ${msg}`);
}

async function initDashboard() {
    try {
        log('Downloading DuckDB-WASM bundles via jsDelivr...');
        
        // 1. Initialize DuckDB WASM
        const JSDELIVR_BUNDLES = duckdb.getJsDelivrBundles();
        const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES);
        
        const worker_url = URL.createObjectURL(
            new Blob([`importScripts("${bundle.mainWorker}");`], {type: 'text/javascript'})
        );
        
        const worker = new Worker(worker_url);
        const logger = new duckdb.ConsoleLogger();
        const db = new duckdb.AsyncDuckDB(logger, worker);
        
        log('Instantiating WebAssembly engine...');
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
        URL.revokeObjectURL(worker_url);
        
        log('DuckDB-WASM successfully instantiated!');

        // 2. Connect to Hugging Face Data Lake
        const hfUrl = 'https://huggingface.co/datasets/swadhinbiswas/air-traffic/resolve/main/air_traffic.duckdb';
        log(`Registering remote Data Lake file (HTTP Range Requests enabled)...`);
        log(`Target: ${hfUrl}`);
        
        await db.registerFileURL('air_traffic.duckdb', hfUrl, duckdb.DuckDBDataProtocol.HTTP, false);
        
        const conn = await db.connect();
        
        dotEl.className = 'pulse-dot connected';
        statusEl.innerText = 'Connected to HF Data Lake';
        log('Successfully connected to remote DuckDB database!');

        // 3. Query KPIs
        log('Executing analytical queries against Gold Marts...');
        
        // Total Flights
        const flightsRes = await conn.query(`SELECT COUNT(*) as total FROM 'air_traffic.duckdb'.fact_flights`);
        const totalFlights = Number(flightsRes.toArray()[0].total);
        document.getElementById('kpi-flights').innerText = totalFlights.toLocaleString();

        // Avg Delay
        const delayRes = await conn.query(`SELECT AVG(delay_minutes) as avg_d FROM 'air_traffic.duckdb'.fact_flights WHERE status != 'cancelled'`);
        const avgDelay = Number(delayRes.toArray()[0].avg_d).toFixed(1);
        document.getElementById('kpi-delay').innerText = avgDelay;

        // Airports
        const airportsRes = await conn.query(`SELECT COUNT(*) as c FROM 'air_traffic.duckdb'.dim_airport`);
        document.getElementById('kpi-airports').innerText = Number(airportsRes.toArray()[0].c).toLocaleString();

        // File Size approximation
        document.getElementById('kpi-size').innerText = '16.5 MB';

        // 4. Query & Render Airports Chart
        log('Fetching top airports by volume...');
        const topAirports = await conn.query(`
            SELECT airport_icao, total_flights 
            FROM 'air_traffic.duckdb'.gold_airport_metrics 
            ORDER BY total_flights DESC 
            LIMIT 10
        `);
        
        const airportsData = topAirports.toArray().map(row => ({
            icao: row.airport_icao,
            flights: Number(row.total_flights)
        }));

        const ctxAirports = document.getElementById('airportsChart').getContext('2d');
        new Chart(ctxAirports, {
            type: 'bar',
            data: {
                labels: airportsData.map(d => d.icao),
                datasets: [{
                    label: 'Total Flights',
                    data: airportsData.map(d => d.flights),
                    backgroundColor: 'rgba(59, 130, 246, 0.8)',
                    borderRadius: 6,
                    borderWidth: 0,
                    barPercentage: 0.6
                }]
            },
            options: getChartOptions()
        });

        // 5. Query & Render Airlines Chart
        log('Fetching airline on-time performance...');
        const airlines = await conn.query(`
            SELECT airline_icao, on_time_rate * 100 as otp
            FROM 'air_traffic.duckdb'.gold_airline_rankings
            ORDER BY total_flights DESC
            LIMIT 10
        `);
        
        const airlineData = airlines.toArray().map(row => ({
            icao: row.airline_icao,
            otp: Number(row.otp)
        }));

        const ctxAirlines = document.getElementById('airlinesChart').getContext('2d');
        new Chart(ctxAirlines, {
            type: 'line',
            data: {
                labels: airlineData.map(d => d.icao),
                datasets: [{
                    label: 'On-Time %',
                    data: airlineData.map(d => d.otp),
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    borderWidth: 3,
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#0a0a0c',
                    pointBorderColor: '#10b981',
                    pointBorderWidth: 2,
                    pointRadius: 4
                }]
            },
            options: getChartOptions()
        });

        log('Dashboard fully rendered. Edge analytics complete.');

    } catch (err) {
        log(`ERROR: ${err.message}`);
        console.error(err);
        dotEl.className = 'pulse-dot error';
        statusEl.innerText = 'Connection Failed';
    }
}

function getChartOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(20, 24, 34, 0.9)',
                titleFont: { family: 'Outfit', size: 13 },
                bodyFont: { family: 'Outfit', size: 14, weight: 'bold' },
                padding: 12,
                cornerRadius: 8,
                displayColors: false
            }
        },
        scales: {
            y: {
                grid: { color: 'rgba(255, 255, 255, 0.05)', drawBorder: false },
                ticks: { color: '#94a3b8', font: { family: 'JetBrains Mono' } }
            },
            x: {
                grid: { display: false, drawBorder: false },
                ticks: { color: '#94a3b8', font: { family: 'Outfit', weight: '500' } }
            }
        },
        animation: {
            duration: 1500,
            easing: 'easeOutQuart'
        }
    };
}

// Start everything
document.addEventListener('DOMContentLoaded', initDashboard);
