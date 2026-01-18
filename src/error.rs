//! Error types for sqlite-tantivy

use thiserror::Error;

/// Result type alias for sqlite-tantivy operations
pub type Result<T> = std::result::Result<T, Error>;

/// Error types that can occur in sqlite-tantivy
#[derive(Error, Debug)]
pub enum Error {
    /// SQLite operation failed
    #[error("SQLite error: {0}")]
    Sqlite(String),

    /// Tantivy operation failed
    #[error("Tantivy error: {0}")]
    Tantivy(#[from] tantivy::TantivyError),

    /// Tantivy query parsing failed
    #[error("Query parse error: {0}")]
    QueryParse(#[from] tantivy::query::QueryParserError),

    /// Directory operation failed
    #[error("Directory error: {0}")]
    Directory(String),

    /// Schema parsing failed
    #[error("Schema error: {0}")]
    Schema(String),

    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    /// JSON serialization error
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    /// File not found in directory
    #[error("File not found: {0}")]
    FileNotFound(String),

    /// Index not found
    #[error("Index not found: {0}")]
    IndexNotFound(String),

    /// Invalid argument
    #[error("Invalid argument: {0}")]
    InvalidArgument(String),
}

impl Error {
    /// Create a SQLite error from a message
    pub fn sqlite(msg: impl Into<String>) -> Self {
        Error::Sqlite(msg.into())
    }

    /// Create a directory error from a message
    pub fn directory(msg: impl Into<String>) -> Self {
        Error::Directory(msg.into())
    }

    /// Create a schema error from a message
    pub fn schema(msg: impl Into<String>) -> Self {
        Error::Schema(msg.into())
    }

    /// Create a file not found error
    pub fn file_not_found(path: impl Into<String>) -> Self {
        Error::FileNotFound(path.into())
    }
}

impl From<tantivy::directory::error::OpenReadError> for Error {
    fn from(err: tantivy::directory::error::OpenReadError) -> Self {
        Error::Directory(err.to_string())
    }
}

impl From<tantivy::directory::error::OpenWriteError> for Error {
    fn from(err: tantivy::directory::error::OpenWriteError) -> Self {
        Error::Directory(err.to_string())
    }
}

impl From<tantivy::directory::error::DeleteError> for Error {
    fn from(err: tantivy::directory::error::DeleteError) -> Self {
        Error::Directory(err.to_string())
    }
}
