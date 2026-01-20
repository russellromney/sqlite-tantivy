//! SQL execution helpers for sqlite-tantivy
//!
//! Provides SQL execution with proper parameter binding for efficient blob handling.

use std::ffi::CString;
use std::os::raw::{c_int, c_void};
use std::ptr;
use std::sync::Arc;

use sqlite_loadable::ext::{
    sqlite3, sqlite3_stmt, sqlite3ext_finalize, sqlite3ext_prepare_v2, sqlite3ext_step,
    sqlite3ext_column_value, sqlite3ext_value_bytes, sqlite3ext_value_int64,
    sqlite3ext_value_text, sqlite3ext_value_blob, sqlite3ext_value_type,
    sqlite3ext_bind_int64, sqlite3ext_bind_text, sqlite3ext_bind_blob, sqlite3ext_bind_null,
    sqlite3ext_open_v2, sqlite3ext_db_filename,
};
use parking_lot::Mutex;

use crate::directory::SqliteValue;
use crate::error::{Error, Result};

/// SQLITE_OK constant
const SQLITE_OK: i32 = 0;
/// SQLITE_ROW constant
const SQLITE_ROW: i32 = 100;
/// SQLITE_DONE constant
const SQLITE_DONE: i32 = 101;

/// SQLite column types
const SQLITE_INTEGER: i32 = 1;
const SQLITE_TEXT: i32 = 3;
const SQLITE_BLOB: i32 = 4;
const SQLITE_NULL: i32 = 5;

/// SQLite destructor type
type SqliteDestructor = Option<unsafe extern "C" fn(*mut c_void)>;

/// SQLITE_TRANSIENT equivalent (-1) - tells SQLite to copy the data immediately
fn sqlite_transient() -> SqliteDestructor {
    // SQLITE_TRANSIENT is defined as ((sqlite3_destructor_type)-1) in SQLite
    // We need to transmute -1isize to a function pointer
    unsafe { std::mem::transmute::<isize, SqliteDestructor>(-1) }
}

/// Bind parameters to a prepared statement using proper parameter binding
unsafe fn bind_params(stmt: *mut sqlite3_stmt, params: &[SqliteValue]) -> Result<()> {
    for (idx, param) in params.iter().enumerate() {
        let param_idx = (idx + 1) as c_int; // SQLite params are 1-indexed
        let rc = match param {
            SqliteValue::Null => sqlite3ext_bind_null(stmt, param_idx),
            SqliteValue::Integer(v) => sqlite3ext_bind_int64(stmt, param_idx, *v),
            SqliteValue::Text(s) => {
                // bind_text needs a C string pointer, length, and destructor
                // SQLITE_TRANSIENT tells SQLite to make its own copy of the data
                sqlite3ext_bind_text(
                    stmt,
                    param_idx,
                    s.as_ptr() as *const i8,
                    s.len() as c_int,
                    sqlite_transient(),
                )
            }
            SqliteValue::Blob(data) => {
                sqlite3ext_bind_blob(
                    stmt,
                    param_idx,
                    data.as_ptr() as *const c_void,
                    data.len() as c_int,
                    sqlite_transient(),
                )
            }
        };
        if rc != SQLITE_OK {
            return Err(Error::sqlite(format!("Failed to bind parameter {}: rc={}", idx, rc)));
        }
    }
    Ok(())
}

/// Execute SQL and return results
pub fn execute_sql(
    db: *mut sqlite3,
    sql: &str,
    params: &[SqliteValue],
) -> Result<Vec<Vec<SqliteValue>>> {
    unsafe {
        // Prepare statement with placeholders
        let c_sql = CString::new(sql)
            .map_err(|_| Error::sqlite("Invalid SQL string"))?;
        let mut stmt: *mut sqlite3_stmt = ptr::null_mut();

        let rc = sqlite3ext_prepare_v2(
            db,
            c_sql.as_ptr(),
            sql.len() as i32,
            &mut stmt,
            ptr::null_mut(),
        );

        if rc != SQLITE_OK {
            return Err(Error::sqlite(format!(
                "Failed to prepare statement (rc={}): {}",
                rc, sql
            )));
        }

        // Bind parameters
        bind_params(stmt, params)?;

        // Execute and collect results
        let mut results = Vec::new();

        loop {
            let rc = sqlite3ext_step(stmt);

            if rc == SQLITE_ROW {
                let mut row = Vec::new();
                let mut col_idx = 0;

                loop {
                    // Get the column value
                    let value = sqlite3ext_column_value(stmt, col_idx);
                    if value.is_null() {
                        break;
                    }

                    let col_type = sqlite3ext_value_type(value);

                    if col_type == SQLITE_NULL && col_idx > 0 {
                        // Check if this is really a NULL or end of columns
                        let bytes = sqlite3ext_value_bytes(value);
                        if bytes == 0 {
                            // Might be end of columns, try next one
                            col_idx += 1;
                            if col_idx > 20 {
                                break;
                            }
                            continue;
                        }
                        row.push(SqliteValue::Null);
                    } else {
                        match col_type {
                            SQLITE_INTEGER => {
                                let val = sqlite3ext_value_int64(value);
                                row.push(SqliteValue::Integer(val));
                            }
                            SQLITE_TEXT => {
                                let bytes = sqlite3ext_value_bytes(value);
                                let text_ptr = sqlite3ext_value_text(value);
                                if !text_ptr.is_null() && bytes > 0 {
                                    let text = std::str::from_utf8(
                                        std::slice::from_raw_parts(text_ptr, bytes as usize)
                                    ).unwrap_or("");
                                    row.push(SqliteValue::Text(text.to_string()));
                                } else {
                                    row.push(SqliteValue::Text(String::new()));
                                }
                            }
                            SQLITE_BLOB => {
                                let bytes = sqlite3ext_value_bytes(value);
                                let blob_ptr = sqlite3ext_value_blob(value);
                                if !blob_ptr.is_null() && bytes > 0 {
                                    let data = std::slice::from_raw_parts(
                                        blob_ptr as *const u8,
                                        bytes as usize
                                    );
                                    row.push(SqliteValue::Blob(data.to_vec()));
                                } else {
                                    row.push(SqliteValue::Blob(Vec::new()));
                                }
                            }
                            SQLITE_NULL => {
                                row.push(SqliteValue::Null);
                            }
                            _ => {
                                // Unknown type, stop processing this row
                                break;
                            }
                        }
                    }

                    col_idx += 1;

                    // Safety limit
                    if col_idx > 100 {
                        break;
                    }
                }

                if !row.is_empty() {
                    results.push(row);
                }
            } else if rc == SQLITE_DONE {
                break;
            } else {
                sqlite3ext_finalize(stmt);
                return Err(Error::sqlite(format!("Step failed: {}", rc)));
            }
        }

        sqlite3ext_finalize(stmt);
        Ok(results)
    }
}

/// Execute SQL that modifies data (INSERT, UPDATE, DELETE, CREATE)
pub fn execute_sql_modify(
    db: *mut sqlite3,
    sql: &str,
    params: &[SqliteValue],
) -> Result<()> {
    execute_sql(db, sql, params)?;
    Ok(())
}

/// SQLite open flags
const SQLITE_OPEN_READWRITE: i32 = 0x00000002;
const SQLITE_OPEN_CREATE: i32 = 0x00000004;
const SQLITE_OPEN_URI: i32 = 0x00000040;
const SQLITE_OPEN_NOMUTEX: i32 = 0x00008000;
const SQLITE_OPEN_SHAREDCACHE: i32 = 0x00020000;

/// Create a callback function for SqliteDirectory that stores segments in the MAIN database.
///
/// Uses the original connection directly. The callback is called synchronously during
/// Tantivy operations, so no actual concurrency occurs.
/// For best performance, enable WAL mode: PRAGMA journal_mode=WAL
pub fn create_sql_callback(db: *mut sqlite3) -> Arc<dyn Fn(&str, &[SqliteValue]) -> std::result::Result<Vec<Vec<SqliteValue>>, String> + Send + Sync> {
    // Use the SAME connection for all operations (single-file architecture)
    // Table is pre-created by init_storage_tables so no creation needed
    let db_ptr = db as usize;

    Arc::new(move |sql: &str, params: &[SqliteValue]| {
        let db = db_ptr as *mut sqlite3;
        // Execute SQL directly on main connection
        // This works because Tantivy buffers writes and doesn't execute them during active callbacks
        execute_sql(db, sql, params).map_err(|e| e.to_string())
    })
}
