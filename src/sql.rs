//! SQL execution helpers for sqlite-tantivy
//!
//! Provides SQL execution with blob support using SQLite's hex literal syntax.
//! This works around sqlite-loadable not exposing bind_blob.

use std::ffi::CString;
use std::ptr;
use std::sync::Arc;

use sqlite_loadable::ext::{
    sqlite3, sqlite3_stmt, sqlite3ext_finalize, sqlite3ext_prepare_v2, sqlite3ext_step,
    sqlite3ext_column_value, sqlite3ext_value_bytes, sqlite3ext_value_int64,
    sqlite3ext_value_text, sqlite3ext_value_blob, sqlite3ext_value_type,
};

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

/// Helper to encode bytes as hex string
fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02X}", b)).collect()
}

/// Build SQL with parameters embedded (using SQLite's quoting rules)
/// This is necessary because sqlite-loadable doesn't expose bind_blob
fn build_sql_with_params(template: &str, params: &[SqliteValue]) -> String {
    let mut result = template.to_string();

    for (idx, param) in params.iter().enumerate().rev() {
        let value_str = match param {
            SqliteValue::Null => "NULL".to_string(),
            SqliteValue::Integer(v) => v.to_string(),
            SqliteValue::Text(s) => {
                // Escape single quotes by doubling them
                let escaped = s.replace('\'', "''");
                format!("'{}'", escaped)
            }
            SqliteValue::Blob(data) => {
                // Use SQLite hex literal syntax: X'HEXDATA'
                format!("X'{}'", hex_encode(data))
            }
        };

        // Find and replace the idx-th occurrence (from start)
        let mut count = 0;
        let mut new_result = String::new();
        let mut chars = result.chars().peekable();
        let mut replaced = false;

        while let Some(c) = chars.next() {
            if c == '?' && !replaced {
                if count == idx {
                    new_result.push_str(&value_str);
                    replaced = true;
                } else {
                    new_result.push(c);
                }
                count += 1;
            } else {
                new_result.push(c);
            }
        }

        result = new_result;
    }

    result
}

/// Execute SQL and return results
pub fn execute_sql(
    db: *mut sqlite3,
    sql: &str,
    params: &[SqliteValue],
) -> Result<Vec<Vec<SqliteValue>>> {
    // Build SQL with embedded parameters
    let final_sql = build_sql_with_params(sql, params);

    unsafe {
        // Prepare statement
        let c_sql = CString::new(final_sql.as_str())
            .map_err(|_| Error::sqlite("Invalid SQL string"))?;
        let mut stmt: *mut sqlite3_stmt = ptr::null_mut();

        let rc = sqlite3ext_prepare_v2(
            db,
            c_sql.as_ptr(),
            final_sql.len() as i32,
            &mut stmt,
            ptr::null_mut(),
        );

        if rc != SQLITE_OK {
            return Err(Error::sqlite(format!(
                "Failed to prepare statement (rc={}): {}",
                rc, final_sql
            )));
        }

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

/// Create a callback function for SqliteDirectory that uses a db pointer
pub fn create_sql_callback(db: *mut sqlite3) -> Arc<dyn Fn(&str, &[SqliteValue]) -> std::result::Result<Vec<Vec<SqliteValue>>, String> + Send + Sync> {
    let db_ptr = db as usize; // Store as usize to make it Send + Sync

    Arc::new(move |sql: &str, params: &[SqliteValue]| {
        let db = db_ptr as *mut sqlite3;
        execute_sql(db, sql, params).map_err(|e| e.to_string())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hex_encode() {
        let data = b"hello";
        let hex = hex_encode(data);
        assert_eq!(hex, "68656C6C6F");
    }

    #[test]
    fn test_build_sql_simple() {
        let sql = "SELECT * FROM t WHERE id = ?";
        let result = build_sql_with_params(sql, &[SqliteValue::Integer(42)]);
        assert_eq!(result, "SELECT * FROM t WHERE id = 42");
    }

    #[test]
    fn test_build_sql_text() {
        let sql = "INSERT INTO t (name) VALUES (?)";
        let result = build_sql_with_params(sql, &[SqliteValue::Text("hello".to_string())]);
        assert_eq!(result, "INSERT INTO t (name) VALUES ('hello')");
    }

    #[test]
    fn test_build_sql_text_escape() {
        let sql = "INSERT INTO t (name) VALUES (?)";
        let result = build_sql_with_params(sql, &[SqliteValue::Text("it's".to_string())]);
        assert_eq!(result, "INSERT INTO t (name) VALUES ('it''s')");
    }

    #[test]
    fn test_build_sql_blob() {
        let sql = "INSERT INTO t (data) VALUES (?)";
        let result = build_sql_with_params(sql, &[SqliteValue::Blob(vec![0x48, 0x65, 0x6C])]);
        assert_eq!(result, "INSERT INTO t (data) VALUES (X'48656C')");
    }

    #[test]
    fn test_build_sql_multiple() {
        let sql = "INSERT INTO t (a, b, c) VALUES (?, ?, ?)";
        let result = build_sql_with_params(sql, &[
            SqliteValue::Integer(1),
            SqliteValue::Text("hello".to_string()),
            SqliteValue::Blob(vec![0xAB, 0xCD]),
        ]);
        assert_eq!(result, "INSERT INTO t (a, b, c) VALUES (1, 'hello', X'ABCD')");
    }
}
