//! sqlite-tantivy: SQLite extension for Tantivy-powered full-text search
//!
//! This extension provides an FTS5-compatible API for full-text search using
//! Tantivy as the underlying engine. Indexes are stored inside SQLite as BLOBs,
//! making the database compatible with Litestream and the broader SQLite ecosystem.
//!
//! # Usage
//!
//! ```sql
//! -- Load the extension
//! .load ./libsqlite_tantivy
//!
//! -- Create a virtual table
//! CREATE VIRTUAL TABLE articles USING tantivy(title TEXT, body TEXT);
//!
//! -- Insert documents
//! INSERT INTO articles(rowid, title, body) VALUES (1, 'Hello', 'World');
//!
//! -- Flush to make documents searchable
//! SELECT tantivy_flush('articles');
//!
//! -- Search
//! SELECT * FROM articles WHERE articles MATCH 'hello';
//! ```

pub mod directory;
pub mod error;
pub mod query;
pub mod schema;
pub mod sql;
pub mod vtab;

use std::collections::HashMap;
use std::sync::Arc;

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use sqlite_loadable::prelude::*;
use sqlite_loadable::table::define_virtual_table_writeable_with_transactions;
use sqlite_loadable::{api, define_scalar_function, Result};
use tantivy::{IndexReader, IndexWriter};

use crate::vtab::TantivyTable;

/// Global registry of Tantivy writers by table name
/// Used by tantivy_flush() to commit pending documents
pub static TANTIVY_REGISTRY: Lazy<Mutex<HashMap<String, TableHandle>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Handle to a Tantivy table's writer and reader for flushing
pub struct TableHandle {
    pub writer: Arc<Mutex<IndexWriter>>,
    pub reader: IndexReader,
}

/// Register a table's writer/reader for flush access
/// Uses db_ptr:table_name as the key to avoid collisions between databases
pub fn register_table(db: *mut sqlite3, name: &str, writer: Arc<Mutex<IndexWriter>>, reader: IndexReader) {
    let key = format!("{}:{}", db as usize, name);
    let mut registry = TANTIVY_REGISTRY.lock();
    registry.insert(key, TableHandle { writer, reader });
}

/// Unregister a table (called on DROP)
pub fn unregister_table(db: *mut sqlite3, name: &str) {
    let key = format!("{}:{}", db as usize, name);
    let mut registry = TANTIVY_REGISTRY.lock();
    registry.remove(&key);
}

/// Get the registry key for a table
pub fn get_registry_key(db: *mut sqlite3, name: &str) -> String {
    format!("{}:{}", db as usize, name)
}

/// Extension entry point - called when the extension is loaded
#[sqlite_entrypoint]
pub fn sqlite3_extension_init(db: *mut sqlite3) -> Result<()> {
    // Register the tantivy virtual table module with write + transaction support
    define_virtual_table_writeable_with_transactions::<TantivyTable>(db, "tantivy", None)?;

    // Register helper scalar functions
    define_scalar_function(
        db,
        "tantivy_version",
        0,
        tantivy_version,
        FunctionFlags::DETERMINISTIC,
    )?;

    define_scalar_function(
        db,
        "tantivy_flush",
        1,
        tantivy_flush,
        FunctionFlags::empty(),
    )?;

    Ok(())
}

/// Flush pending documents to make them searchable
/// Usage: SELECT tantivy_flush('tablename');
fn tantivy_flush(ctx: *mut sqlite3_context, args: &[*mut sqlite3_value]) -> Result<()> {
    let table_name = api::value_text(&args[0])?;
    let db = api::context_db_handle(ctx);
    let key = get_registry_key(db, table_name);

    let registry = TANTIVY_REGISTRY.lock();
    if let Some(handle) = registry.get(&key) {
        // Commit the writer
        let mut writer = handle.writer.lock();
        writer.commit()
            .map_err(|e| sqlite_loadable::Error::new_message(format!("Tantivy commit failed: {}", e)))?;

        // Reload the reader
        handle.reader.reload()
            .map_err(|e| sqlite_loadable::Error::new_message(format!("Reader reload failed: {}", e)))?;

        api::result_int(ctx, 1); // Success
    } else {
        api::result_int(ctx, 0); // Table not found
    }

    Ok(())
}

/// Returns the version of the sqlite-tantivy extension
fn tantivy_version(ctx: *mut sqlite3_context, _args: &[*mut sqlite3_value]) -> Result<()> {
    let version = format!(
        "sqlite-tantivy {} (tantivy {})",
        env!("CARGO_PKG_VERSION"),
        "0.22"
    );
    api::result_text(ctx, &version)?;
    Ok(())
}
