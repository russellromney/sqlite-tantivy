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
//! -- Search
//! SELECT * FROM articles WHERE articles MATCH 'hello';
//! ```

pub mod directory;
pub mod error;
pub mod query;
pub mod schema;
pub mod sql;
pub mod vtab;

use sqlite_loadable::prelude::*;
use sqlite_loadable::table::define_virtual_table_writeable;
use sqlite_loadable::{api, define_scalar_function, Result};

use crate::vtab::TantivyTable;

/// Extension entry point - called when the extension is loaded
#[sqlite_entrypoint]
pub fn sqlite3_extension_init(db: *mut sqlite3) -> Result<()> {
    // Register the tantivy virtual table module with write support
    define_virtual_table_writeable::<TantivyTable>(db, "tantivy", None)?;

    // Register helper scalar functions
    define_scalar_function(
        db,
        "tantivy_version",
        0,
        tantivy_version,
        FunctionFlags::DETERMINISTIC,
    )?;

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
