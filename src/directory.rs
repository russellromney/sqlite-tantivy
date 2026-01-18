//! SqliteDirectory - Tantivy Directory implementation backed by SQLite BLOBs
//!
//! This module implements `tantivy::directory::Directory` to store index segments
//! inside SQLite tables, enabling single-file databases compatible with Litestream
//! and the broader SQLite ecosystem.

use std::io::{self, Write};
use std::ops::Range;
use std::path::Path;
use std::sync::Arc;

use tantivy::directory::{
    error::{DeleteError, OpenReadError, OpenWriteError},
    Directory, FileHandle, OwnedBytes, TerminatingWrite, WatchCallback, WatchHandle, WritePtr,
};

use crate::error::{Error, Result};

/// SQL schema for storing Tantivy segments in SQLite
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
    UNIQUE(index_id, file_name),
    FOREIGN KEY (index_id) REFERENCES _tantivy_indexes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_segments_index_file
ON _tantivy_segments(index_id, file_name);
"#;

/// A callback type for SQLite operations
/// Since we can't hold a rusqlite Connection in the virtual table context,
/// we use callbacks that receive the connection pointer from sqlite-loadable
pub type SqliteCallback = Arc<dyn Fn(&str, &[SqliteValue]) -> std::result::Result<Vec<Vec<SqliteValue>>, String> + Send + Sync>;

/// Simple value type for SQLite operations
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

/// Tantivy Directory implementation that stores files in SQLite BLOBs
#[derive(Clone)]
pub struct SqliteDirectory {
    index_id: i64,
    callback: SqliteCallback,
}

impl SqliteDirectory {
    /// Create a new SqliteDirectory for the given index
    pub fn new(index_id: i64, callback: SqliteCallback) -> Self {
        Self { index_id, callback }
    }

    /// Initialize the schema tables (call once per database)
    pub fn init_schema(callback: &SqliteCallback) -> Result<()> {
        // Split into individual statements and execute each
        for stmt in SCHEMA_SQL.split(';').filter(|s| !s.trim().is_empty()) {
            (callback)(stmt.trim(), &[]).map_err(Error::sqlite)?;
        }
        Ok(())
    }

    /// Create a new index entry and return its ID
    pub fn create_index(callback: &SqliteCallback, name: &str, schema: &str, settings: Option<&str>) -> Result<i64> {
        let settings_val = settings.map(|s| SqliteValue::Text(s.to_string())).unwrap_or(SqliteValue::Null);

        (callback)(
            "INSERT INTO _tantivy_indexes (name, schema, settings) VALUES (?, ?, ?)",
            &[SqliteValue::Text(name.to_string()), SqliteValue::Text(schema.to_string()), settings_val],
        ).map_err(Error::sqlite)?;

        // Get last insert rowid
        let result = (callback)("SELECT last_insert_rowid()", &[]).map_err(Error::sqlite)?;
        result
            .first()
            .and_then(|row| row.first())
            .and_then(|v| v.as_i64())
            .ok_or_else(|| Error::sqlite("Failed to get index ID"))
    }

    /// Get index ID by name
    pub fn get_index_id(callback: &SqliteCallback, name: &str) -> Result<Option<i64>> {
        let result = (callback)(
            "SELECT id FROM _tantivy_indexes WHERE name = ?",
            &[SqliteValue::Text(name.to_string())],
        ).map_err(Error::sqlite)?;

        Ok(result.first().and_then(|row| row.first()).and_then(|v| v.as_i64()))
    }

    /// Read file data from SQLite
    fn read_file(&self, file_name: &str) -> Result<Vec<u8>> {
        let result = (self.callback)(
            "SELECT data FROM _tantivy_segments WHERE index_id = ? AND file_name = ?",
            &[SqliteValue::Integer(self.index_id), SqliteValue::Text(file_name.to_string())],
        ).map_err(Error::sqlite)?;

        result
            .into_iter()
            .next()
            .and_then(|row| row.into_iter().next())
            .and_then(|v| v.into_blob())
            .ok_or_else(|| Error::file_not_found(file_name))
    }

    /// Write file data to SQLite
    fn write_file(&self, file_name: &str, data: &[u8]) -> Result<()> {
        (self.callback)(
            "INSERT OR REPLACE INTO _tantivy_segments (index_id, file_name, data) VALUES (?, ?, ?)",
            &[
                SqliteValue::Integer(self.index_id),
                SqliteValue::Text(file_name.to_string()),
                SqliteValue::Blob(data.to_vec()),
            ],
        ).map_err(Error::sqlite)?;
        Ok(())
    }

    /// Delete file from SQLite
    fn delete_file(&self, file_name: &str) -> Result<bool> {
        (self.callback)(
            "DELETE FROM _tantivy_segments WHERE index_id = ? AND file_name = ?",
            &[SqliteValue::Integer(self.index_id), SqliteValue::Text(file_name.to_string())],
        ).map_err(Error::sqlite)?;

        // Check if any rows were deleted (SQLite changes() function)
        let result = (self.callback)("SELECT changes()", &[]).map_err(Error::sqlite)?;
        let changes = result
            .first()
            .and_then(|row| row.first())
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        Ok(changes > 0)
    }

    /// Check if file exists in SQLite
    fn file_exists(&self, file_name: &str) -> Result<bool> {
        let result = (self.callback)(
            "SELECT 1 FROM _tantivy_segments WHERE index_id = ? AND file_name = ? LIMIT 1",
            &[SqliteValue::Integer(self.index_id), SqliteValue::Text(file_name.to_string())],
        ).map_err(Error::sqlite)?;

        Ok(!result.is_empty())
    }

    /// List all files for this index
    fn list_files(&self) -> Result<Vec<String>> {
        let result = (self.callback)(
            "SELECT file_name FROM _tantivy_segments WHERE index_id = ?",
            &[SqliteValue::Integer(self.index_id)],
        ).map_err(Error::sqlite)?;

        Ok(result
            .into_iter()
            .filter_map(|row| row.into_iter().next())
            .filter_map(|v| match v {
                SqliteValue::Text(s) => Some(s),
                _ => None,
            })
            .collect())
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
        let deleted = self.delete_file(&file_name).map_err(|e| {
            DeleteError::IoError {
                io_error: Arc::new(io::Error::new(io::ErrorKind::Other, e.to_string())),
                filepath: path.to_path_buf(),
            }
        })?;

        if deleted {
            Ok(())
        } else {
            Err(DeleteError::FileDoesNotExist(path.to_path_buf()))
        }
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
        // The WatchHandle needs to prevent the callback from being dropped
        Ok(WatchHandle::empty())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::RwLock;

    /// In-memory mock for testing without SQLite
    fn create_mock_callback() -> (SqliteCallback, Arc<RwLock<HashMap<String, Vec<u8>>>>) {
        let storage: Arc<RwLock<HashMap<String, Vec<u8>>>> = Arc::new(RwLock::new(HashMap::new()));
        let storage_clone = storage.clone();

        let callback: SqliteCallback = Arc::new(move |sql, params| {
            let mut store = storage_clone.write().unwrap();

            if sql.starts_with("INSERT OR REPLACE INTO _tantivy_segments") {
                if let (Some(SqliteValue::Integer(idx)), Some(SqliteValue::Text(name)), Some(SqliteValue::Blob(data))) =
                    (params.get(0), params.get(1), params.get(2))
                {
                    let key = format!("{}:{}", idx, name);
                    store.insert(key, data.clone());
                }
                Ok(vec![])
            } else if sql.starts_with("SELECT data FROM _tantivy_segments") {
                if let (Some(SqliteValue::Integer(idx)), Some(SqliteValue::Text(name))) =
                    (params.get(0), params.get(1))
                {
                    let key = format!("{}:{}", idx, name);
                    if let Some(data) = store.get(&key) {
                        return Ok(vec![vec![SqliteValue::Blob(data.clone())]]);
                    }
                }
                Ok(vec![])
            } else if sql.starts_with("SELECT 1 FROM _tantivy_segments") {
                if let (Some(SqliteValue::Integer(idx)), Some(SqliteValue::Text(name))) =
                    (params.get(0), params.get(1))
                {
                    let key = format!("{}:{}", idx, name);
                    if store.contains_key(&key) {
                        return Ok(vec![vec![SqliteValue::Integer(1)]]);
                    }
                }
                Ok(vec![])
            } else if sql.starts_with("DELETE FROM _tantivy_segments") {
                if let (Some(SqliteValue::Integer(idx)), Some(SqliteValue::Text(name))) =
                    (params.get(0), params.get(1))
                {
                    let key = format!("{}:{}", idx, name);
                    store.remove(&key);
                }
                Ok(vec![])
            } else if sql.starts_with("SELECT changes()") {
                Ok(vec![vec![SqliteValue::Integer(1)]])
            } else if sql.starts_with("SELECT file_name FROM _tantivy_segments") {
                if let Some(SqliteValue::Integer(idx)) = params.get(0) {
                    let prefix = format!("{}:", idx);
                    let files: Vec<Vec<SqliteValue>> = store
                        .keys()
                        .filter(|k| k.starts_with(&prefix))
                        .map(|k| vec![SqliteValue::Text(k.strip_prefix(&prefix).unwrap().to_string())])
                        .collect();
                    return Ok(files);
                }
                Ok(vec![])
            } else {
                Ok(vec![])
            }
        });

        (callback, storage)
    }

    #[test]
    fn test_write_and_read() {
        let (callback, _storage) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let path = Path::new("test.txt");
        dir.atomic_write(path, b"hello world").unwrap();

        let data = dir.atomic_read(path).unwrap();
        assert_eq!(data, b"hello world");
    }

    #[test]
    fn test_exists() {
        let (callback, _storage) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let path = Path::new("test.txt");
        assert!(!dir.exists(path).unwrap());

        dir.atomic_write(path, b"data").unwrap();
        assert!(dir.exists(path).unwrap());
    }

    #[test]
    fn test_delete() {
        let (callback, _storage) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let path = Path::new("test.txt");
        dir.atomic_write(path, b"data").unwrap();
        assert!(dir.exists(path).unwrap());

        dir.delete(path).unwrap();
        assert!(!dir.exists(path).unwrap());
    }

    #[test]
    fn test_file_handle() {
        let (callback, _storage) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let path = Path::new("test.txt");
        let content = b"hello world";
        dir.atomic_write(path, content).unwrap();

        let handle = dir.get_file_handle(path).unwrap();
        let bytes = handle.read_bytes(0..5).unwrap();
        assert_eq!(&bytes[..], b"hello");

        let bytes = handle.read_bytes(6..11).unwrap();
        assert_eq!(&bytes[..], b"world");
    }

    #[test]
    fn test_streaming_write() {
        let (callback, _storage) = create_mock_callback();
        let dir = SqliteDirectory::new(1, callback);

        let path = Path::new("stream.txt");
        let mut writer = dir.open_write(path).unwrap();

        writer.write_all(b"hello ").unwrap();
        writer.write_all(b"world").unwrap();
        writer.terminate().unwrap();

        let data = dir.atomic_read(path).unwrap();
        assert_eq!(data, b"hello world");
    }
}
