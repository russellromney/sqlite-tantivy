//! tantivy-bench - SQLite Tantivy vs FTS5 Benchmark CLI
//!
//! Concurrent multi-reader benchmark comparing Tantivy and FTS5 search performance

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

#[derive(Debug)]
struct Cli {
    database: PathBuf,
    engine: String,
    read_duration_secs: u64,
    reader_threads: usize,
    cache_percent: f64,
    query_type: String,
    extension_path: Option<PathBuf>,
}

impl Cli {
    fn from_args() -> Self {
        let args: Vec<String> = std::env::args().collect();

        if args.len() < 3 {
            eprintln!("Usage: {} <database> <engine> [options]", args[0]);
            eprintln!();
            eprintln!("Arguments:");
            eprintln!("  <database>          Path to database file");
            eprintln!("  <engine>            Engine: tantivy or fts5");
            eprintln!();
            eprintln!("Options:");
            eprintln!("  --duration <secs>   Read benchmark duration (default: 30)");
            eprintln!("  --threads <n>       Number of reader threads (default: 4)");
            eprintln!("  --cache <percent>   Cache size as % of DB (default: 0.10)");
            eprintln!("  --query <type>      Query type: exact, phrase, prefix, fuzzy (default: exact)");
            eprintln!("  --extension <path>  Path to tantivy extension (auto-detect if not specified)");
            std::process::exit(1);
        }

        let database = PathBuf::from(&args[1]);
        let engine = args[2].clone();

        let mut read_duration_secs = 30;
        let mut reader_threads = 4;
        let mut cache_percent = 0.10;
        let mut query_type = "exact".to_string();
        let mut extension_path = None;

        let mut i = 3;
        while i < args.len() {
            match args[i].as_str() {
                "--duration" => {
                    i += 1;
                    if i < args.len() {
                        read_duration_secs = args[i].parse().unwrap_or(30);
                    }
                }
                "--threads" => {
                    i += 1;
                    if i < args.len() {
                        reader_threads = args[i].parse().unwrap_or(4);
                    }
                }
                "--cache" => {
                    i += 1;
                    if i < args.len() {
                        cache_percent = args[i].parse().unwrap_or(0.10);
                    }
                }
                "--query" => {
                    i += 1;
                    if i < args.len() {
                        query_type = args[i].clone();
                    }
                }
                "--extension" => {
                    i += 1;
                    if i < args.len() {
                        extension_path = Some(PathBuf::from(&args[i]));
                    }
                }
                _ => {}
            }
            i += 1;
        }

        Cli {
            database,
            engine,
            read_duration_secs,
            reader_threads,
            cache_percent,
            query_type,
            extension_path,
        }
    }
}

#[derive(Serialize, Deserialize, Debug)]
struct BenchmarkResult {
    engine: String,
    document_count: usize,
    query_type: String,
    reader_threads: usize,
    read_ops_per_sec: f64,
    read_p50_us: f64,
    read_p95_us: f64,
    read_p99_us: f64,
    cache_percent: f64,
    cache_size_kb: i64,
    file_size_mb: f64,
    logical_size_mb: f64,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::from_args();

    if !cli.database.exists() {
        eprintln!("Error: Database file not found: {}", cli.database.display());
        std::process::exit(1);
    }

    println!("=== SQLite FTS Benchmark: {} ===", cli.engine.to_uppercase());
    println!("Database: {}", cli.database.display());
    println!("Read duration: {}s ({}x threads)", cli.read_duration_secs, cli.reader_threads);
    println!("Cache: {:.0}% of logical size", cli.cache_percent * 100.0);
    println!("Query type: {}", cli.query_type);
    println!();

    let result = bench_database(&cli)?;

    println!();
    println!("=== Results ===");
    println!("Engine: {}", result.engine);
    println!("Documents: {}", result.document_count);
    println!("Query type: {}", result.query_type);
    println!("Threads: {}", result.reader_threads);
    println!("Throughput: {:.0} queries/sec", result.read_ops_per_sec);
    println!("Latency P50: {:.2}µs", result.read_p50_us);
    println!("Latency P95: {:.2}µs", result.read_p95_us);
    println!("Latency P99: {:.2}µs", result.read_p99_us);
    println!("Cache: {:.0}% ({} KB)", result.cache_percent * 100.0, result.cache_size_kb);
    println!("File size: {:.2} MB", result.file_size_mb);
    println!("Logical size: {:.2} MB", result.logical_size_mb);

    // Output JSON for easy parsing
    println!();
    println!("JSON:");
    println!("{}", serde_json::to_string_pretty(&result)?);

    Ok(())
}

fn bench_database(cli: &Cli) -> Result<BenchmarkResult, Box<dyn std::error::Error>> {
    let start_total = Instant::now();

    // Open connection to get metadata
    let mut conn = Connection::open(&cli.database)?;

    // Load tantivy extension if needed
    if cli.engine == "tantivy" {
        let extension_path = if let Some(ref path) = cli.extension_path {
            path.clone()
        } else {
            // Auto-detect
            if cfg!(target_os = "macos") {
                PathBuf::from("./target/release/libsqlite_tantivy.dylib")
            } else if cfg!(target_os = "linux") {
                PathBuf::from("./target/release/libsqlite_tantivy.so")
            } else {
                PathBuf::from("./target/release/libsqlite_tantivy.dll")
            }
        };

        unsafe {
            conn.load_extension(&extension_path, None)?;
        }
        println!("  Loaded extension: {}", extension_path.display());
    }

    // Get document count
    let doc_count: usize = conn.query_row("SELECT COUNT(*) FROM articles", [], |row| row.get(0))?;

    // Calculate cache size based on logical DB size
    let page_count: i64 = conn.query_row("PRAGMA page_count", [], |row| row.get(0))?;
    let page_size: i64 = conn.query_row("PRAGMA page_size", [], |row| row.get(0))?;
    let logical_size_kb = (page_count * page_size) / 1024;
    let logical_size_mb = logical_size_kb as f64 / 1024.0;
    let cache_size_kb = (logical_size_kb as f64 * cli.cache_percent) as i64;

    // Get file size
    let file_size_mb = std::fs::metadata(&cli.database)?.len() as f64 / (1024.0 * 1024.0);

    println!("  Documents: {}", doc_count);
    println!("  Logical size: {:.2} MB", logical_size_mb);
    println!("  File size: {:.2} MB", file_size_mb);
    println!("  Cache size: {} KB ({:.0}%)", cache_size_kb, cli.cache_percent * 100.0);
    println!();

    // CRITICAL: Checkpoint WAL before benchmarking
    println!("  Checkpointing WAL...");
    conn.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()))?;

    drop(conn);

    // Collect sample search terms from the database
    let search_terms = collect_search_terms(&cli.database, cli.engine.as_str(), cli.extension_path.as_ref(), 100)?;
    println!("  Collected {} search terms", search_terms.len());
    println!();

    // Benchmark concurrent reads
    println!("  Running concurrent read benchmark...");
    let (read_count, read_latencies) = bench_reads_concurrent(
        &cli.database,
        cli.engine.as_str(),
        cli.extension_path.as_ref(),
        cli.read_duration_secs,
        cli.reader_threads,
        cache_size_kb,
        &search_terms,
        &cli.query_type,
    )?;

    let read_ops_per_sec = read_count as f64 / read_latencies.iter().sum::<f64>() * 1_000_000.0;
    let read_p50 = percentile(&read_latencies, 0.5);
    let read_p95 = percentile(&read_latencies, 0.95);
    let read_p99 = percentile(&read_latencies, 0.99);

    println!("  Completed {} queries in {:.2}s", read_count, start_total.elapsed().as_secs_f64());

    Ok(BenchmarkResult {
        engine: cli.engine.clone(),
        document_count: doc_count,
        query_type: cli.query_type.clone(),
        reader_threads: cli.reader_threads,
        read_ops_per_sec,
        read_p50_us: read_p50,
        read_p95_us: read_p95,
        read_p99_us: read_p99,
        cache_percent: cli.cache_percent,
        cache_size_kb,
        file_size_mb,
        logical_size_mb,
    })
}

fn collect_search_terms(
    db_path: &PathBuf,
    engine: &str,
    extension_path: Option<&PathBuf>,
    limit: usize,
) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let mut conn = Connection::open(db_path)?;

    if engine == "tantivy" {
        let ext_path = if let Some(path) = extension_path {
            path.clone()
        } else {
            if cfg!(target_os = "macos") {
                PathBuf::from("./target/release/libsqlite_tantivy.dylib")
            } else if cfg!(target_os = "linux") {
                PathBuf::from("./target/release/libsqlite_tantivy.so")
            } else {
                PathBuf::from("./target/release/libsqlite_tantivy.dll")
            }
        };
        unsafe {
            conn.load_extension(&ext_path, None)?;
        }
    }

    // Extract common words from titles for realistic search queries
    let mut stmt = conn.prepare("SELECT title FROM articles LIMIT ?")?;
    let mut terms = Vec::new();

    let rows = stmt.query_map([limit], |row| {
        let title: String = row.get(0)?;
        Ok(title)
    })?;

    for row_result in rows {
        let title = row_result?;
        // Split title into words and take the first significant word
        for word in title.split_whitespace() {
            if word.len() > 3 && !word.chars().all(|c| c.is_numeric()) {
                terms.push(word.to_lowercase());
                break;
            }
        }
    }

    // Deduplicate and limit
    terms.sort();
    terms.dedup();
    terms.truncate(limit);

    if terms.is_empty() {
        terms.push("the".to_string());
    }

    Ok(terms)
}

fn bench_reads_concurrent(
    db_path: &PathBuf,
    engine: &str,
    extension_path: Option<&PathBuf>,
    duration_secs: u64,
    num_threads: usize,
    cache_size_kb: i64,
    search_terms: &[String],
    query_type: &str,
) -> Result<(usize, Vec<f64>), Box<dyn std::error::Error>> {
    use std::thread;

    let db_path = Arc::new(db_path.clone());
    let engine = Arc::new(engine.to_string());
    let extension_path = Arc::new(extension_path.map(|p| p.clone()));
    let search_terms = Arc::new(search_terms.to_vec());
    let query_type = Arc::new(query_type.to_string());

    let mut handles = Vec::new();

    for thread_id in 0..num_threads {
        let db_path = Arc::clone(&db_path);
        let engine = Arc::clone(&engine);
        let extension_path = Arc::clone(&extension_path);
        let search_terms = Arc::clone(&search_terms);
        let query_type = Arc::clone(&query_type);

        let handle = thread::spawn(move || -> Result<(usize, Vec<f64>), String> {
            // Each thread opens its own connection
            let mut conn = Connection::open_with_flags(
                &*db_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY,
            ).map_err(|e| e.to_string())?;

            // Load extension if needed
            if engine.as_str() == "tantivy" {
                let ext_path = if let Some(ref path) = *extension_path {
                    path.clone()
                } else {
                    if cfg!(target_os = "macos") {
                        PathBuf::from("./target/release/libsqlite_tantivy.dylib")
                    } else if cfg!(target_os = "linux") {
                        PathBuf::from("./target/release/libsqlite_tantivy.so")
                    } else {
                        PathBuf::from("./target/release/libsqlite_tantivy.dll")
                    }
                };
                unsafe {
                    conn.load_extension(&ext_path, None).map_err(|e| e.to_string())?;
                }
            }

            // Configure cache for this connection
            conn.execute(&format!("PRAGMA cache_size = -{}", cache_size_kb), [])
                .map_err(|e| e.to_string())?;

            let mut latencies = Vec::new();
            let mut i = 0usize;
            let deadline = Instant::now() + std::time::Duration::from_secs(duration_secs);

            // Use fast random number generator
            use std::collections::hash_map::RandomState;
            use std::hash::{BuildHasher, Hasher};
            let hasher = RandomState::new();

            while Instant::now() < deadline {
                // Select random search term
                let mut h = hasher.build_hasher();
                h.write_usize(i);
                h.write_usize(thread_id);
                let term_idx = (h.finish() as usize) % search_terms.len();
                let search_term = &search_terms[term_idx];

                // Build query based on type
                let query = match query_type.as_str() {
                    "exact" => search_term.clone(),
                    "phrase" => format!("\"{}\"", search_term),
                    "prefix" => format!("{}*", &search_term[..search_term.len().min(4)]),
                    "fuzzy" => {
                        // Simulate fuzzy by adding wildcard
                        let prefix = &search_term[..search_term.len().min(3)];
                        format!("{}*", prefix)
                    }
                    _ => search_term.clone(),
                };

                let start = Instant::now();

                // Execute FTS query
                let sql = if engine.as_str() == "tantivy" {
                    "SELECT rowid, author, title FROM articles WHERE articles MATCH ? LIMIT 10"
                } else {
                    "SELECT rowid, author, title FROM articles WHERE articles MATCH ? LIMIT 10"
                };

                let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
                let mut rows = stmt.query([&query]).map_err(|e| e.to_string())?;

                // Fetch all results to ensure full query execution
                let mut count = 0;
                while let Ok(Some(_row)) = rows.next() {
                    count += 1;
                }

                latencies.push(start.elapsed().as_micros() as f64);
                i += 1;
            }

            Ok((i, latencies))
        });

        handles.push(handle);
    }

    // Collect results from all threads
    let mut total_count = 0;
    let mut all_latencies = Vec::new();

    for handle in handles {
        let (count, mut latencies) = handle.join()
            .map_err(|_| "Thread panicked")?
            .map_err(|e| e.to_string())?;
        total_count += count;
        all_latencies.append(&mut latencies);
    }

    Ok((total_count, all_latencies))
}

fn percentile(latencies: &[f64], p: f64) -> f64 {
    let mut sorted = latencies.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let idx = ((p * sorted.len() as f64) as usize).min(sorted.len() - 1);
    sorted[idx]
}
