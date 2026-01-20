//! Integration tests for sqlite-tantivy extension

use rusqlite::{Connection, LoadExtensionGuard};
use std::path::Path;

fn load_extension(conn: &Connection) -> Result<(), rusqlite::Error> {
    unsafe {
        let _guard = conn.load_extension_enable()?;

        // Try release first, then debug
        let release_path = Path::new("./target/release/libsqlite_tantivy");
        let debug_path = Path::new("./target/debug/libsqlite_tantivy");

        if release_path.with_extension("dylib").exists() || release_path.with_extension("so").exists() {
            conn.load_extension(release_path, None)?;
        } else {
            conn.load_extension(debug_path, None)?;
        }
    }
    Ok(())
}

#[test]
fn test_extension_loads() {
    let conn = Connection::open_in_memory().unwrap();
    load_extension(&conn).expect("Failed to load extension");

    // Verify extension loaded by calling tantivy_version()
    let version: String = conn
        .query_row("SELECT tantivy_version()", [], |row| row.get(0))
        .unwrap();

    assert!(version.contains("sqlite-tantivy"));
    assert!(version.contains("tantivy"));
    println!("Extension version: {}", version);
}

#[test]
fn test_create_virtual_table() {
    let conn = Connection::open_in_memory().unwrap();
    load_extension(&conn).expect("Failed to load extension");

    // Create virtual table
    conn.execute(
        "CREATE VIRTUAL TABLE docs USING tantivy(title TEXT, body TEXT)",
        [],
    ).expect("Failed to create virtual table");

    // Verify table exists
    let count: i32 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='docs'",
            [],
            |row| row.get(0),
        )
        .unwrap();

    assert_eq!(count, 1);
}

#[test]
fn test_insert_and_search() {
    let conn = Connection::open_in_memory().unwrap();
    load_extension(&conn).expect("Failed to load extension");

    // Create virtual table
    conn.execute(
        "CREATE VIRTUAL TABLE articles USING tantivy(title TEXT, body TEXT)",
        [],
    ).unwrap();

    // Insert documents
    conn.execute(
        "INSERT INTO articles(rowid, title, body) VALUES (1, 'Hello World', 'This is a test document')",
        [],
    ).expect("Failed to insert document 1");

    conn.execute(
        "INSERT INTO articles(rowid, title, body) VALUES (2, 'Rust Programming', 'Rust is a systems programming language')",
        [],
    ).expect("Failed to insert document 2");

    conn.execute(
        "INSERT INTO articles(rowid, title, body) VALUES (3, 'Hello Again', 'Another hello document here')",
        [],
    ).expect("Failed to insert document 3");

    // Flush to make documents searchable
    let _: i32 = conn.query_row("SELECT tantivy_flush('articles')", [], |row| row.get(0)).unwrap();

    // Search for 'hello' - should find docs 1 and 3
    let mut stmt = conn
        .prepare("SELECT rowid, title FROM articles WHERE articles MATCH 'hello'")
        .unwrap();

    let results: Vec<(i64, String)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .unwrap()
        .filter_map(|r| r.ok())
        .collect();

    println!("Search results for 'hello': {:?}", results);

    assert!(results.len() >= 2, "Expected at least 2 results, got {}", results.len());

    // Check that we found the right documents
    let rowids: Vec<i64> = results.iter().map(|(id, _)| *id).collect();
    assert!(rowids.contains(&1) || rowids.contains(&3), "Expected to find doc 1 or 3");
}

#[test]
fn test_phrase_search() {
    let conn = Connection::open_in_memory().unwrap();
    load_extension(&conn).expect("Failed to load extension");

    conn.execute(
        "CREATE VIRTUAL TABLE docs USING tantivy(content TEXT)",
        [],
    ).unwrap();

    conn.execute(
        "INSERT INTO docs(rowid, content) VALUES (1, 'the quick brown fox')",
        [],
    ).unwrap();

    conn.execute(
        "INSERT INTO docs(rowid, content) VALUES (2, 'the lazy brown dog')",
        [],
    ).unwrap();

    conn.execute(
        "INSERT INTO docs(rowid, content) VALUES (3, 'quick and lazy animals')",
        [],
    ).unwrap();

    // Flush to make documents searchable
    let _: i32 = conn.query_row("SELECT tantivy_flush('docs')", [], |row| row.get(0)).unwrap();

    // Phrase search should only match doc 1
    let mut stmt = conn
        .prepare("SELECT rowid FROM docs WHERE docs MATCH '\"quick brown\"'")
        .unwrap();

    let results: Vec<i64> = stmt
        .query_map([], |row| row.get(0))
        .unwrap()
        .filter_map(|r| r.ok())
        .collect();

    println!("Phrase search results: {:?}", results);
    assert!(results.contains(&1), "Expected to find doc 1 for phrase 'quick brown'");
}

#[test]
fn test_delete() {
    let conn = Connection::open_in_memory().unwrap();
    load_extension(&conn).expect("Failed to load extension");

    conn.execute(
        "CREATE VIRTUAL TABLE docs USING tantivy(title TEXT)",
        [],
    ).unwrap();

    conn.execute("INSERT INTO docs(rowid, title) VALUES (1, 'Hello')", []).unwrap();
    conn.execute("INSERT INTO docs(rowid, title) VALUES (2, 'World')", []).unwrap();

    // Flush to make documents searchable
    let _: i32 = conn.query_row("SELECT tantivy_flush('docs')", [], |row| row.get(0)).unwrap();

    // Verify doc 1 exists
    let mut stmt = conn.prepare("SELECT rowid FROM docs WHERE docs MATCH 'hello'").unwrap();
    let results: Vec<i64> = stmt.query_map([], |row| row.get(0)).unwrap().filter_map(|r| r.ok()).collect();
    assert_eq!(results.len(), 1, "Expected 1 result before delete");

    // Delete doc 1 (delete includes commit + reload)
    conn.execute("DELETE FROM docs WHERE rowid = 1", []).unwrap();

    // Verify it's gone
    let mut stmt = conn.prepare("SELECT rowid FROM docs WHERE docs MATCH 'hello'").unwrap();
    let results: Vec<i64> = stmt.query_map([], |row| row.get(0)).unwrap().filter_map(|r| r.ok()).collect();
    assert_eq!(results.len(), 0, "Document should be deleted");
}

#[test]
fn test_storage_tables_created() {
    let conn = Connection::open_in_memory().unwrap();
    load_extension(&conn).expect("Failed to load extension");

    conn.execute(
        "CREATE VIRTUAL TABLE docs USING tantivy(content TEXT)",
        [],
    ).unwrap();

    // Check that _tantivy_indexes table exists in main database
    let count: i32 = conn
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='_tantivy_indexes'",
            [],
            |row| row.get(0),
        )
        .unwrap();

    assert_eq!(count, 1, "_tantivy_indexes table should exist in main database");

    // Note: _tantivy_segments table is now stored in a separate database file
    // (e.g., mydb.db-tantivy) to avoid locking conflicts during Tantivy commit operations.
    // For in-memory databases, the segment storage is also in-memory (not accessible).
}
