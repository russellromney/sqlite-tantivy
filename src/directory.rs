//! SqliteDirectory - Tantivy Directory implementation backed by SQLite BLOBs
//!
//! This module implements `tantivy::directory::Directory` to store index segments
//! inside SQLite tables, enabling single-file databases compatible with Litestream
//! and the broader SQLite ecosystem.
//!
//! Uses a callback function for database operations to maintain compatibility with
//! the SQLite connection used by the extension.

use std::collections::HashMap;
use std::io::{self, Write};
use std::ops::Range;
use std::path::Path;
use std::sync::Arc;
use parking_lot::Mutex;

use tantivy::directory::{
    error::{DeleteError, OpenReadError, OpenWriteError},
    Directory, FileHandle, OwnedBytes, TerminatingWrite, WatchCallback, WatchHandle, WritePtr,
};

use crate::error::{Error, Result};

/// SQL schema for index metadata (stored in main database)
pub const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS _tantivy_indexes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    schema TEXT NOT NULL,
    settings TEXT
);

CREATE TABLE IF NOT EXISTS _tantivy_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    index_id INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    data BLOB NOT NULL,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(index_id, file_name)
);

CREATE INDEX IF NOT EXISTS idx_segments_index_file
ON _tantivy_segments(index_id, file_name);
"#;

/// Simple value type for SQLite operations (used by vtab for queries)
#[derive(Debug, Clone)]
pub enum SqliteValue {
    Null,
    Integer(i64),
    Text(String),
    Blob(Vec<u8>),
}

impl SqliteValue {
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            SqliteValue::Integer(v) => Some(*v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            SqliteValue::Text(v) => Some(v.as_str()),
            _ => None,
        }
    }

    pub fn as_blob(&self) -> Option<&[u8]> {
        match self {
            SqliteValue::Blob(v) => Some(v.as_slice()),
            _ => None,
        }
    }

    pub fn into_blob(self) -> Option<Vec<u8>> {
        match self {
            SqliteValue::Blob(v) => Some(v),
            _ => None,
        }
    }
}

/// Type for SQL execution callback
pub type SqlCallback = Arc<dyn Fn(&str, &[SqliteValue]) -> std::result::Result<Vec<Vec<SqliteValue>>, String> + Send + Sync>;

/// Tantivy Directory implementation that stores files in SQLite BLOBs
/// Uses a callback for database operations to work with the extension's connection
/// Buffers writes to avoid reentrancy issues during index creation
#[derive(Clone)]
pub struct SqliteDirectory {
    index_id: i64,
    sql_callback: SqlCallback,
    /// Write buffer: filename -> file data
    /// Writes are buffered in memory until flush() is called
    write_buffer: Arc<Mutex<HashMap<String, Vec<u8>>>>,
}

impl SqliteDirectory {
    /// Create a new SqliteDirectory for the given index
    pub fn new(index_id: i64, sql_callback: SqlCallback) -> Self {
        Self {
            index_id,
            sql_callback,
            write_buffer: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Read file data from buffer or SQLite
    fn read_file(&self, file_name: &str) -> Result<Vec<u8>> {
        // Check buffer first (for uncommitted writes)
        {
            let buffer = self.write_buffer.lock();
            if let Some(data) = buffer.get(file_name) {
                return Ok(data.clone());
            }
        }

        // Not in buffer, read from database
        let results = (self.sql_callback)(
            "SELECT data FROM _tantivy_segments WHERE index_id = ? AND file_name = ?",
            &[
                SqliteValue::Integer(self.index_id),
                SqliteValue::Text(file_name.to_string()),
            ],
        ).map_err(|e| Error::sqlite(e))?;

        if let Some(row) = results.first() {
            if let Some(data) = row.first() {
                if let Some(bytes) = data.clone().into_blob() {
                    return Ok(bytes);
                }
            }
        }
        Err(Error::file_not_found(file_name))
    }

    /// Write file data to buffer (actual write happens on flush)
    fn write_file(&self, file_name: &str, data: &[u8]) -> Result<()> {
        // Buffer the write instead of executing SQL immediately
        // This avoids reentrancy issues during index creation
        let mut buffer = self.write_buffer.lock();
        buffer.insert(file_name.to_string(), data.to_vec());
        Ok(())
    }

    /// Flush all buffered writes to the database
    pub fn flush(&self) -> Result<()> {
        let mut buffer = self.write_buffer.lock();

        // Write all buffered files to database
        for (file_name, data) in buffer.drain() {
            (self.sql_callback)(
                "INSERT OR REPLACE INTO _tantivy_segments (index_id, file_name, data) VALUES (?, ?, ?)",
                &[
                    SqliteValue::Integer(self.index_id),
                    SqliteValue::Text(file_name),
                    SqliteValue::Blob(data),
                ],
            ).map_err(|e| Error::sqlite(e))?;
        }

        Ok(())
    }

    /// Delete file from SQLite
    fn delete_file(&self, file_name: &str) -> Result<bool> {
        let _ = (self.sql_callback)(
            "DELETE FROM _tantivy_segments WHERE index_id = ? AND file_name = ?",
            &[
                SqliteValue::Integer(self.index_id),
                SqliteValue::Text(file_name.to_string()),
            ],
        ).map_err(|e| Error::sqlite(e))?;
        // Note: We can't easily check if a row was actually deleted with this interface
        // For now, assume success if no error
        Ok(true)
    }

    /// Check if file exists in buffer or SQLite
    fn file_exists(&self, file_name: &str) -> Result<bool> {
        // Check buffer first
        {
            let buffer = self.write_buffer.lock();
            if buffer.contains_key(file_name) {
                return Ok(true);
            }
        }

        // Not in buffer, check database
        let results = (self.sql_callback)(
            "SELECT 1 FROM _tantivy_segments WHERE index_id = ? AND file_name = ? LIMIT 1",
            &[
                SqliteValue::Integer(self.index_id),
                SqliteValue::Text(file_name.to_string()),
            ],
        ).map_err(|e| Error::sqlite(e))?;
        Ok(!results.is_empty())
    }
}

impl std::fmt::Debug for SqliteDirectory {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SqliteDirectory")
            .field("index_id", &self.index_id)
            .finish()
    }
}

/// File handle for reading from SQLite blobs
#[derive(Debug)]
struct SqliteFileHandle {
    data: OwnedBytes,
}

impl tantivy_common::HasLen for SqliteFileHandle {
    fn len(&self) -> usize {
        self.data.len()
    }
}

impl FileHandle for SqliteFileHandle {
    fn read_bytes(&self, range: Range<usize>) -> io::Result<OwnedBytes> {
        Ok(self.data.slice(range))
    }
}

/// Writer that buffers data and writes to SQLite on terminate
struct SqliteWriter {
    directory: SqliteDirectory,
    file_name: String,
    buffer: Vec<u8>,
}

impl Write for SqliteWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.buffer.extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl TerminatingWrite for SqliteWriter {
    fn terminate_ref(&mut self, _: tantivy::directory::AntiCallToken) -> io::Result<()> {
        self.directory
            .write_file(&self.file_name, &self.buffer)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }
}

impl Directory for SqliteDirectory {
    fn get_file_handle(&self, path: &Path) -> std::result::Result<Arc<dyn FileHandle>, OpenReadError> {
        let file_name = path.to_string_lossy().to_string();
        let data = self.read_file(&file_name).map_err(|e| {
            if matches!(e, Error::FileNotFound(_)) {
                OpenReadError::FileDoesNotExist(path.to_path_buf())
            } else {
                OpenReadError::IoError {
                    io_error: Arc::new(io::Error::new(io::ErrorKind::Other, e.to_string())),
                    filepath: path.to_path_buf(),
                }
            }
        })?;

        Ok(Arc::new(SqliteFileHandle {
            data: OwnedBytes::new(data),
        }))
    }

    fn delete(&self, path: &Path) -> std::result::Result<(), DeleteError> {
        let file_name = path.to_string_lossy().to_string();
        self.delete_file(&file_name).map_err(|e| {
            DeleteError::IoError {
                io_error: Arc::new(io::Error::new(io::ErrorKind::Other, e.to_string())),
                filepath: path.to_path_buf(),
            }
        })?;
        Ok(())
    }

    fn exists(&self, path: &Path) -> std::result::Result<bool, OpenReadError> {
        let file_name = path.to_string_lossy().to_string();
        self.file_exists(&file_name).map_err(|e| OpenReadError::IoError {
            io_error: Arc::new(io::Error::new(io::ErrorKind::Other, e.to_string())),
            filepath: path.to_path_buf(),
        })
    }

    fn open_write(&self, path: &Path) -> std::result::Result<WritePtr, OpenWriteError> {
        let file_name = path.to_string_lossy().to_string();
        let writer = SqliteWriter {
            directory: self.clone(),
            file_name,
            buffer: Vec::new(),
        };
        Ok(io::BufWriter::new(Box::new(writer)))
    }

    fn atomic_read(&self, path: &Path) -> std::result::Result<Vec<u8>, OpenReadError> {
        let file_name = path.to_string_lossy().to_string();
        self.read_file(&file_name).map_err(|e| {
            if matches!(e, Error::FileNotFound(_)) {
                OpenReadError::FileDoesNotExist(path.to_path_buf())
            } else {
                OpenReadError::IoError {
                    io_error: Arc::new(io::Error::new(io::ErrorKind::Other, e.to_string())),
                    filepath: path.to_path_buf(),
                }
            }
        })
    }

    fn atomic_write(&self, path: &Path, data: &[u8]) -> io::Result<()> {
        let file_name = path.to_string_lossy().to_string();
        self.write_file(&file_name, data)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    fn sync_directory(&self) -> io::Result<()> {
        // SQLite handles durability through its own WAL/journal
        Ok(())
    }

    fn watch(&self, _watch_callback: WatchCallback) -> tantivy::Result<WatchHandle> {
        // SQLite doesn't support file watches, return a no-op handle
        Ok(WatchHandle::empty())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;
    use std::collections::HashMap;

    // Simple in-memory store for testing
    fn create_mock_callback() -> (SqlCallback, Arc<Mutex<HashMap<(i64, String), Vec<u8>>>>) {
        let store: Arc<Mutex<HashMap<(i64, String), Vec<u8>>>> = Arc::new(Mutex::new(HashMap::new()));
        let store_clone = store.clone();

        let callback: SqlCallback = Arc::new(move |sql: &str, params: &[SqliteValue]| {
            let mut store = store_clone.lock().unwrap();

            if sql.contains("INSERT OR REPLACE") {
                if let (Some(SqliteValue::Integer(index_id)), Some(SqliteValue::Text(file_name)), Some(SqliteValue::Blob(data))) =
                    (params.get(0), params.get(1), params.get(2))
                {
                    store.insert((*index_id, file_name.clone()), data.clone());
                }
                Ok(vec![])
            } else if sql.contains("SELECT data") {
                if let (Some(SqliteValue::Integer(index_id)), Some(SqliteValue::Text(file_name))) =
                    (params.get(0), params.get(1))
                {
                    if let Some(data) = store.get(&(*index_id, file_name.clone())) {
                        return Ok(vec![vec![SqliteValue::Blob(data.clone())]]);
                    }
                }
                Ok(vec![])
            } else if sql.contains("SELECT 1") {
                if let (Some(SqliteValue::Integer(index_id)), Some(SqliteValue::Text(file_name))) =
                    (params.get(0), params.get(1))
                {
                    if store.contains_key(&(*index_id, file_name.clone())) {
                        return Ok(vec![vec![SqliteValue::Integer(1)]]);
                    }
                }
                Ok(vec![])
            } else if sql.contains("DELETE") {
                if let (Some(SqliteValue::Integer(index_id)), Some(SqliteValue::Text(file_name))) =
                    (params.get(0), params.get(1))
                {
                    store.remove(&(*index_id, file_name.clone()));
                }
                Ok(vec![])
            } else {
                Ok(vec![])
            }
        });

        (callback, store)
    }

    #[test]
    fn test_write_and_read() {
        let (callback, _store) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let test_path = Path::new("test.txt");
        dir.atomic_write(test_path, b"hello world").unwrap();

        let data = dir.atomic_read(test_path).unwrap();
        assert_eq!(data, b"hello world");
    }

    #[test]
    fn test_exists() {
        let (callback, _store) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let test_path = Path::new("test.txt");
        assert!(!dir.exists(test_path).unwrap());

        dir.atomic_write(test_path, b"data").unwrap();
        assert!(dir.exists(test_path).unwrap());
    }

    #[test]
    fn test_delete() {
        let (callback, _store) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let test_path = Path::new("test.txt");
        dir.atomic_write(test_path, b"data").unwrap();
        assert!(dir.exists(test_path).unwrap());

        dir.delete(test_path).unwrap();
        assert!(!dir.exists(test_path).unwrap());
    }

    #[test]
    fn test_file_handle() {
        let (callback, _store) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let test_path = Path::new("test.txt");
        let content = b"hello world";
        dir.atomic_write(test_path, content).unwrap();

        let handle = dir.get_file_handle(test_path).unwrap();
        let bytes = handle.read_bytes(0..5).unwrap();
        assert_eq!(&bytes[..], b"hello");

        let bytes = handle.read_bytes(6..11).unwrap();
        assert_eq!(&bytes[..], b"world");
    }

    #[test]
    fn test_streaming_write() {
        let (callback, _store) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let test_path = Path::new("stream.txt");
        let mut writer = dir.open_write(test_path).unwrap();

        writer.write_all(b"hello ").unwrap();
        writer.write_all(b"world").unwrap();
        writer.terminate().unwrap();

        let data = dir.atomic_read(test_path).unwrap();
        assert_eq!(data, b"hello world");
    }
}
