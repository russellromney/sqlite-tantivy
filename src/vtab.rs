//! Virtual table implementation for tantivy full-text search
//!
//! Implements the SQLite virtual table interface to provide FTS5-like syntax:
//! ```sql
//! CREATE VIRTUAL TABLE docs USING tantivy(title TEXT, body TEXT);
//! INSERT INTO docs(rowid, title, body) VALUES (1, 'Hello', 'World');
//! SELECT * FROM docs WHERE docs MATCH 'hello';
//! ```

use std::mem;
use std::os::raw::c_int;
use std::sync::Arc;

use parking_lot::Mutex;
use sqlite_loadable::prelude::*;
use sqlite_loadable::table::{
    BestIndexError, IndexInfo, UpdateOperation, VTab, VTabArguments, VTabCursor, VTabWriteable,
    VTabWriteableWithTransactions,
};
use sqlite_loadable::{api, Result};
use tantivy::collector::TopDocs;
use tantivy::schema::{Field, Value};
use tantivy::{Index, IndexReader, IndexSettings, IndexWriter, ReloadPolicy, TantivyDocument, Term};

use crate::directory::{SqliteDirectory, SqliteValue, SCHEMA_SQL};
use crate::query::parse_query;
use crate::schema::{FieldType, TableSchema};
use crate::sql::{execute_sql, execute_sql_modify, create_sql_callback};

/// The virtual table structure
#[repr(C)]
pub struct TantivyTable {
    /// Base vtab structure required by SQLite
    base: sqlite3_vtab,
    /// SQLite database pointer (stored for SQL execution)
    db: *mut sqlite3,
    /// Index ID in _tantivy_indexes table
    index_id: i64,
    /// Parsed table schema
    table_schema: Option<TableSchema>,
    /// Tantivy index
    index: Option<Index>,
    /// Index writer (for INSERT/UPDATE/DELETE)
    writer: Option<Arc<Mutex<IndexWriter>>>,
    /// Index reader (for queries)
    reader: Option<IndexReader>,
    /// Default fields to search
    default_fields: Vec<Field>,
    /// Table name
    table_name: String,
    /// SqliteDirectory for persistence
    directory: Option<SqliteDirectory>,
    /// Count of uncommitted inserts (for batching commits)
    uncommitted_count: usize,
}

impl Drop for TantivyTable {
    fn drop(&mut self) {
        // IMPORTANT: During SQLite connection close, we can't execute any SQL
        // because the connection is in a closing state. Tantivy's writer/index
        // drop handlers try to update files via our SqliteDirectory callback,
        // which would deadlock.
        //
        // Solution: Use mem::forget to skip dropping these components entirely.
        // This leaks memory, but avoids the deadlock. The data is still persisted
        // in SQLite from previous commits.
        if let Some(writer) = self.writer.take() {
            std::mem::forget(writer);
        }
        if let Some(reader) = self.reader.take() {
            std::mem::forget(reader);
        }
        if let Some(index) = self.index.take() {
            std::mem::forget(index);
        }
        if let Some(directory) = self.directory.take() {
            std::mem::forget(directory);
        }
    }
}

impl TantivyTable {
    fn get_writer(&self) -> Result<parking_lot::MutexGuard<'_, IndexWriter>> {
        self.writer
            .as_ref()
            .map(|w| w.lock())
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index writer"))
    }

    fn get_reader(&self) -> Result<&IndexReader> {
        self.reader
            .as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index reader"))
    }

    /// Flush any uncommitted documents to disk
    fn flush_if_needed(&mut self) -> Result<()> {
        if self.uncommitted_count > 0 {
            if let Some(writer) = &self.writer {
                let mut w = writer.lock();
                w.commit()
                    .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;
                self.uncommitted_count = 0;
            }
            if let Some(reader) = &self.reader {
                let _ = reader.reload();
            }
        }
        Ok(())
    }
}

impl<'vtab> VTab<'vtab> for TantivyTable {
    type Aux = ();
    type Cursor = TantivyCursor;

    fn connect(
        db: *mut sqlite3,
        _aux: Option<&Self::Aux>,
        args: VTabArguments,
    ) -> Result<(String, Self)> {
        // Parse the schema from arguments
        let schema_args: Vec<String> = args.arguments.iter().map(|s| s.to_string()).collect();

        let table_schema = TableSchema::parse(&schema_args)
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Build CREATE TABLE statement for SQLite
        let table_name = args.table_name.to_string();
        let create_sql = table_schema.to_create_table(&table_name);

        // Create Tantivy schema
        let tantivy_schema = table_schema.to_tantivy_schema();

        // Initialize storage tables (create if not exists)
        init_storage_tables(db)
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Get or create index entry
        let schema_json = table_schema
            .to_json()
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        let index_id = get_or_create_index(db, &table_name, &schema_json)
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Create SqliteDirectory with callback for database operations
        let sql_callback = create_sql_callback(db);
        let sqlite_directory = SqliteDirectory::new(index_id, sql_callback.clone());

        // Check if index already exists (has files) - uses segment db via callback
        let has_segments = check_index_has_segments(&sql_callback, index_id)
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Create or open Tantivy index
        let index = if has_segments {
            // Open existing index
            Index::open(sqlite_directory.clone())
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?
        } else {
            // Create new index
            Index::create(sqlite_directory.clone(), tantivy_schema.clone(), IndexSettings::default())
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?
        };

        // Create writer with 15MB heap (smaller for SQLite use case)
        let writer = index
            .writer(15_000_000)
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Create reader with manual reload policy (we'll reload after commits)
        let reader = index
            .reader_builder()
            .reload_policy(ReloadPolicy::Manual)
            .try_into()
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Get default text fields for searching
        let mut default_fields = Vec::new();
        for field_def in &table_schema.fields {
            if field_def.field_type == FieldType::Text {
                if let Ok(field) = tantivy_schema.get_field(&field_def.name) {
                    default_fields.push(field);
                }
            }
        }

        let writer_arc = Arc::new(Mutex::new(writer));

        // Register with global registry for tantivy_flush() access
        crate::register_table(db, &table_name, writer_arc.clone(), reader.clone());

        let vtab = TantivyTable {
            base: unsafe { mem::zeroed() },
            db,
            index_id,
            table_schema: Some(table_schema),
            index: Some(index),
            writer: Some(writer_arc),
            reader: Some(reader),
            default_fields,
            table_name,
            directory: Some(sqlite_directory),
            uncommitted_count: 0,
        };

        Ok((create_sql, vtab))
    }

    fn best_index(&self, mut index_info: IndexInfo) -> core::result::Result<(), BestIndexError> {
        use sqlite_loadable::table::ConstraintOperator;

        let mut argv_index = 1; // SQLite expects argv indices to start at 1
        let mut has_match = false;
        let mut has_rowid_eq = false;

        // Process constraints to find MATCH and rowid constraints
        for mut constraint in index_info.constraints() {
            if !constraint.usable() {
                continue;
            }

            // Handle MATCH constraint - this is the primary FTS constraint
            if constraint.op() == Some(ConstraintOperator::MATCH) {
                constraint.set_argv_index(argv_index);
                constraint.set_omit(true); // vtab handles this constraint fully
                argv_index += 1;
                has_match = true;
            }
            // Handle rowid = X constraints (column -1 is rowid)
            else if constraint.column_idx() == -1 && constraint.op() == Some(ConstraintOperator::EQ) {
                constraint.set_argv_index(argv_index);
                constraint.set_omit(true);
                argv_index += 1;
                has_rowid_eq = true;
            }
        }

        // Set estimated costs based on query type
        if has_match {
            index_info.set_estimated_cost(10.0);  // FTS is fast
            index_info.set_estimated_rows(100);
            index_info.set_idxnum(1); // 1 = FTS mode
        } else if has_rowid_eq {
            index_info.set_estimated_cost(1.0);  // Direct rowid lookup is fastest
            index_info.set_estimated_rows(1);
            index_info.set_idxnum(2); // 2 = rowid lookup mode
        } else {
            index_info.set_estimated_cost(1000.0); // Full scan is expensive
            index_info.set_estimated_rows(10000);
            index_info.set_idxnum(0); // 0 = full scan
        }

        Ok(())
    }

    fn open(&'vtab mut self) -> Result<Self::Cursor> {
        // NOTE: Cannot flush here - it causes deadlock because we're in the middle
        // of a SQLite callback and can't execute other SQL.
        // Documents won't be searchable until explicitly committed.

        Ok(TantivyCursor {
            base: unsafe { mem::zeroed() },
            results: Vec::new(),
            position: 0,
            table: self as *mut _,
        })
    }
}

impl<'vtab> VTabWriteable<'vtab> for TantivyTable {
    fn update(&'vtab mut self, operation: UpdateOperation, p_rowid: *mut i64) -> Result<()> {
        match operation {
            UpdateOperation::Insert { values, rowid } => {
                self.handle_insert(values, rowid, p_rowid)
            }
            UpdateOperation::Delete(rowid_value) => {
                self.handle_delete(rowid_value)
            }
            UpdateOperation::Update { _values } => {
                // UPDATE is delete + insert, but we'll implement it properly later
                Err(sqlite_loadable::Error::new_message("UPDATE not yet supported"))
            }
        }
    }
}

impl<'vtab> VTabWriteableWithTransactions<'vtab> for TantivyTable {
    fn begin(&'vtab mut self) -> Result<()> {
        // Nothing special needed at transaction start
        Ok(())
    }

    fn sync(&'vtab mut self) -> Result<()> {
        // NOTE: Cannot commit here - Tantivy's commit writes via SqliteDirectory
        // which calls execute_sql, and we can't execute SQL during xSync (deadlock).
        // Instead, we commit lazily in xFilter before queries.
        Ok(())
    }

    fn commit(&'vtab mut self) -> Result<()> {
        // Called after sync succeeds - nothing more to do
        Ok(())
    }

    fn rollback(&'vtab mut self) -> Result<()> {
        // Discard uncommitted documents
        if self.uncommitted_count > 0 {
            if let Some(writer) = &self.writer {
                let mut w = writer.lock();
                let _ = w.rollback();
                self.uncommitted_count = 0;
            }
        }
        Ok(())
    }
}

impl TantivyTable {
    /// Handle INSERT operation
    fn handle_insert(
        &mut self,
        values: &[*mut sqlite3_value],
        rowid: Option<&*mut sqlite3_value>,
        p_rowid: *mut i64,
    ) -> Result<()> {
        let table_schema = self.table_schema.as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No schema"))?;

        let index = self.index.as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index"))?;

        let tantivy_schema = index.schema();

        // Determine rowid
        let doc_rowid = if let Some(rowid_ptr) = rowid {
            // Rowid was explicitly provided
            api::value_int64(rowid_ptr)
        } else if !values.is_empty() {
            // First value might be rowid if it's an integer and we have extra values
            // Actually, in SQLite vtab, the first column value is after rowid
            // Let's just generate a rowid based on current time
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as i64)
                .unwrap_or(1)
        } else {
            return Err(sqlite_loadable::Error::new_message("No values provided"));
        };

        // Build Tantivy document
        let mut doc = TantivyDocument::new();

        // Add _rowid field
        let rowid_field = tantivy_schema.get_field("_rowid")
            .map_err(|_| sqlite_loadable::Error::new_message("No _rowid field in schema"))?;
        doc.add_i64(rowid_field, doc_rowid);

        // Add other fields from values
        for (idx, field_def) in table_schema.fields.iter().enumerate() {
            if idx >= values.len() {
                break;
            }

            let value_ptr = values[idx];
            let field = tantivy_schema.get_field(&field_def.name)
                .map_err(|_| sqlite_loadable::Error::new_message(format!("Field not found: {}", field_def.name)))?;

            match field_def.field_type {
                FieldType::Text | FieldType::Tag => {
                    if let Ok(text) = api::value_text(&value_ptr) {
                        doc.add_text(field, text);
                    }
                }
                FieldType::Integer => {
                    let val = api::value_int64(&value_ptr);
                    doc.add_i64(field, val);
                }
                FieldType::Float => {
                    let val = api::value_double(&value_ptr);
                    doc.add_f64(field, val);
                }
            }
        }

        // Add document to index
        {
            let mut writer = self.writer.as_ref()
                .ok_or_else(|| sqlite_loadable::Error::new_message("No writer"))?
                .lock();

            writer.add_document(doc)
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

            // Don't auto-commit during INSERT - it causes deadlock because:
            // 1. Python/SQLite holds a lock during virtual table callback
            // 2. Tantivy commit uses worker threads that try to write back to SQLite
            // 3. Those writes need the same lock → deadlock
            //
            // Instead, we just count uncommitted docs. Commit happens in flush_if_needed()
            // which is called before queries (in open()) and can be called explicitly.
            self.uncommitted_count += 1;
        }

        // Set the rowid output
        unsafe {
            *p_rowid = doc_rowid;
        }

        Ok(())
    }

    /// Handle DELETE operation
    fn handle_delete(&mut self, rowid_value: &*mut sqlite3_value) -> Result<()> {
        let rowid = api::value_int64(rowid_value);

        let index = self.index.as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index"))?;

        let tantivy_schema = index.schema();
        let rowid_field = tantivy_schema.get_field("_rowid")
            .map_err(|_| sqlite_loadable::Error::new_message("No _rowid field"))?;

        // Delete by rowid
        {
            let mut writer = self.writer.as_ref()
                .ok_or_else(|| sqlite_loadable::Error::new_message("No writer"))?
                .lock();

            let term = Term::from_field_i64(rowid_field, rowid);
            writer.delete_term(term);

            writer.commit()
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;
        }

        // Reload reader
        if let Some(reader) = &self.reader {
            reader.reload()
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;
        }

        Ok(())
    }
}

/// Cursor for iterating search results
#[repr(C)]
pub struct TantivyCursor {
    base: sqlite3_vtab_cursor,
    results: Vec<SearchResult>,
    position: usize,
    table: *mut TantivyTable,
}

struct SearchResult {
    rowid: i64,
    score: f32,
    doc: TantivyDocument,
}

impl TantivyCursor {
    fn execute_fts_search(&mut self, table: &TantivyTable, query_str: &str) -> Result<()> {
        let index = table
            .index
            .as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index"))?;

        let reader = table.get_reader()?;
        let searcher = reader.searcher();

        let tantivy_schema = index.schema();

        // Parse and execute the query
        let query = parse_query(query_str, &tantivy_schema, &table.default_fields)
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Execute search
        let top_docs = searcher
            .search(&*query, &TopDocs::with_limit(1000))
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Get rowid field
        let rowid_field = tantivy_schema
            .get_field("_rowid")
            .map_err(|_| sqlite_loadable::Error::new_message("No _rowid field"))?;

        // Collect results
        for (score, doc_address) in top_docs {
            let doc: TantivyDocument = searcher
                .doc(doc_address)
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

            // Extract rowid from document
            let rowid = doc.get_first(rowid_field).and_then(|v| v.as_i64()).unwrap_or(0);

            self.results.push(SearchResult { rowid, score, doc });
        }

        Ok(())
    }

    fn execute_rowid_lookup(&mut self, table: &TantivyTable, target_rowid: i64) -> Result<()> {
        use tantivy::query::TermQuery;
        use tantivy::schema::IndexRecordOption;

        let index = table
            .index
            .as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index"))?;

        let reader = table.get_reader()?;
        let searcher = reader.searcher();

        let tantivy_schema = index.schema();
        let rowid_field = tantivy_schema
            .get_field("_rowid")
            .map_err(|_| sqlite_loadable::Error::new_message("No _rowid field"))?;

        // Create a term query for the specific rowid
        let term = Term::from_field_i64(rowid_field, target_rowid);
        let query = TermQuery::new(term, IndexRecordOption::Basic);

        // Execute search
        let top_docs = searcher
            .search(&query, &TopDocs::with_limit(1))
            .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

        // Collect results
        for (score, doc_address) in top_docs {
            let doc: TantivyDocument = searcher
                .doc(doc_address)
                .map_err(|e| sqlite_loadable::Error::new_message(e.to_string()))?;

            let rowid = doc.get_first(rowid_field).and_then(|v| v.as_i64()).unwrap_or(0);

            self.results.push(SearchResult { rowid, score, doc });
        }

        Ok(())
    }
}

impl VTabCursor for TantivyCursor {
    fn filter(
        &mut self,
        idx_num: c_int,
        _idx_str: Option<&str>,
        args: &[*mut sqlite3_value],
    ) -> Result<()> {
        self.results.clear();
        self.position = 0;

        // NOTE: Documents must be flushed via tantivy_flush('tablename') before querying
        // We can't flush here because Tantivy commit triggers SQL which deadlocks in callbacks

        let table = unsafe { &*self.table };

        // Handle different query modes based on idx_num set in best_index
        match idx_num {
            1 => {
                // FTS mode - MATCH query
                if args.is_empty() {
                    return Ok(());
                }
                let query_str = api::value_text(&args[0])?;
                self.execute_fts_search(table, query_str)?;
            }
            2 => {
                // Rowid lookup mode
                if args.is_empty() {
                    return Ok(());
                }
                let rowid = api::value_int64(&args[0]);
                self.execute_rowid_lookup(table, rowid)?;
            }
            _ => {
                // Full scan mode (idx_num = 0) - return all documents
                // TODO: Implement full table scan for completeness
            }
        }

        Ok(())
    }

    fn next(&mut self) -> Result<()> {
        self.position += 1;
        Ok(())
    }

    fn eof(&self) -> bool {
        self.position >= self.results.len()
    }

    fn column(&self, ctx: *mut sqlite3_context, col_idx: c_int) -> Result<()> {
        if self.position >= self.results.len() {
            return Ok(());
        }

        let result = &self.results[self.position];
        let table = unsafe { &*self.table };

        let table_schema = table
            .table_schema
            .as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No schema"))?;

        let index = table
            .index
            .as_ref()
            .ok_or_else(|| sqlite_loadable::Error::new_message("No index"))?;
        let tantivy_schema = index.schema();

        let col_idx = col_idx as usize;

        // Check if this is the hidden MATCH column (last column)
        if col_idx == table_schema.fields.len() {
            api::result_double(ctx, result.score as f64);
            return Ok(());
        }

        if col_idx >= table_schema.fields.len() {
            api::result_null(ctx);
            return Ok(());
        }

        let field_def = &table_schema.fields[col_idx];
        let field = tantivy_schema
            .get_field(&field_def.name)
            .map_err(|_| sqlite_loadable::Error::new_message(format!("Field not found: {}", field_def.name)))?;

        match result.doc.get_first(field) {
            Some(value) => match field_def.field_type {
                FieldType::Text | FieldType::Tag => {
                    if let Some(text) = value.as_str() {
                        api::result_text(ctx, text)?;
                    } else {
                        api::result_null(ctx);
                    }
                }
                FieldType::Integer => {
                    if let Some(n) = value.as_i64() {
                        api::result_int64(ctx, n);
                    } else {
                        api::result_null(ctx);
                    }
                }
                FieldType::Float => {
                    if let Some(n) = value.as_f64() {
                        api::result_double(ctx, n);
                    } else {
                        api::result_null(ctx);
                    }
                }
            },
            None => {
                api::result_null(ctx);
            }
        }

        Ok(())
    }

    fn rowid(&self) -> Result<i64> {
        if self.position >= self.results.len() {
            return Ok(0);
        }
        Ok(self.results[self.position].rowid)
    }
}

// Helper functions for storage management

/// Initialize the storage tables (_tantivy_indexes, _tantivy_segments)
fn init_storage_tables(db: *mut sqlite3) -> crate::error::Result<()> {
    // Split SCHEMA_SQL into individual statements and execute each
    for stmt in SCHEMA_SQL.split(';').filter(|s| !s.trim().is_empty()) {
        execute_sql_modify(db, stmt.trim(), &[])?;
    }
    Ok(())
}

/// Get or create index entry, returning the index ID
fn get_or_create_index(db: *mut sqlite3, name: &str, schema_json: &str) -> crate::error::Result<i64> {
    // Try to find existing index
    let results = execute_sql(
        db,
        "SELECT id FROM _tantivy_indexes WHERE name = ?",
        &[SqliteValue::Text(name.to_string())],
    )?;

    if let Some(row) = results.first() {
        if let Some(SqliteValue::Integer(id)) = row.first() {
            return Ok(*id);
        }
    }

    // Create new index entry
    execute_sql_modify(
        db,
        "INSERT INTO _tantivy_indexes (name, schema, settings) VALUES (?, ?, NULL)",
        &[
            SqliteValue::Text(name.to_string()),
            SqliteValue::Text(schema_json.to_string()),
        ],
    )?;

    // Get the last insert rowid
    let results = execute_sql(db, "SELECT last_insert_rowid()", &[])?;
    results
        .first()
        .and_then(|row| row.first())
        .and_then(|v| {
            if let SqliteValue::Integer(id) = v {
                Some(*id)
            } else {
                None
            }
        })
        .ok_or_else(|| crate::error::Error::sqlite("Failed to get index ID"))
}

/// Check if an index has any segments stored (via the sql_callback to the segment database)
fn check_index_has_segments(sql_callback: &crate::directory::SqlCallback, index_id: i64) -> crate::error::Result<bool> {
    let results = sql_callback(
        "SELECT 1 FROM _tantivy_segments WHERE index_id = ? LIMIT 1",
        &[SqliteValue::Integer(index_id)],
    ).map_err(|e| crate::error::Error::sqlite(e))?;
    Ok(!results.is_empty())
}
