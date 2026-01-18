//! Schema parsing for CREATE VIRTUAL TABLE arguments
//!
//! Parses column definitions like:
//! - `title TEXT`
//! - `category TAG`
//! - `views INTEGER`
//! - `tokenizer='en_stem'`

use serde::{Deserialize, Serialize};
use crate::error::{Error, Result};

/// Supported field types
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FieldType {
    /// Full-text indexed text field
    Text,
    /// Exact-match tag field (like FTS5 unindexed but searchable with =)
    Tag,
    /// Numeric field for range queries
    Integer,
    /// Floating point field
    Float,
}

impl FieldType {
    pub fn from_str(s: &str) -> Result<Self> {
        match s.to_uppercase().as_str() {
            "TEXT" => Ok(FieldType::Text),
            "TAG" => Ok(FieldType::Tag),
            "INTEGER" | "INT" => Ok(FieldType::Integer),
            "FLOAT" | "REAL" | "DOUBLE" => Ok(FieldType::Float),
            _ => Err(Error::schema(format!("Unknown field type: {}", s))),
        }
    }
}

/// A field definition in the schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldDef {
    pub name: String,
    pub field_type: FieldType,
    /// Whether the field should be stored for retrieval
    pub stored: bool,
    /// Whether the field should be indexed (default true for TEXT)
    pub indexed: bool,
}

/// Supported tokenizers
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Tokenizer {
    /// Default tokenizer with stemming for English
    EnStem,
    /// Raw tokenizer (no processing)
    Raw,
    /// Unicode tokenizer (splits on Unicode word boundaries)
    Unicode,
    /// Whitespace tokenizer
    Whitespace,
    /// N-gram tokenizer with specified min/max
    Ngram { min: usize, max: usize },
}

impl Default for Tokenizer {
    fn default() -> Self {
        Tokenizer::EnStem
    }
}

impl Tokenizer {
    pub fn from_str(s: &str) -> Result<Self> {
        match s.to_lowercase().as_str() {
            "en_stem" | "english" | "porter" => Ok(Tokenizer::EnStem),
            "raw" | "none" => Ok(Tokenizer::Raw),
            "unicode" | "unicode61" => Ok(Tokenizer::Unicode),
            "whitespace" => Ok(Tokenizer::Whitespace),
            _ => Err(Error::schema(format!("Unknown tokenizer: {}", s))),
        }
    }
}

/// Complete schema for a tantivy virtual table
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TableSchema {
    pub fields: Vec<FieldDef>,
    pub tokenizer: Tokenizer,
    /// Fields to store for retrieval (if empty, store all)
    pub stored_fields: Vec<String>,
}

impl TableSchema {
    /// Parse schema from CREATE VIRTUAL TABLE arguments
    ///
    /// Arguments look like:
    /// - "title TEXT"
    /// - "body TEXT"
    /// - "tokenizer='en_stem'"
    pub fn parse(args: &[String]) -> Result<Self> {
        let mut fields = Vec::new();
        let mut tokenizer = Tokenizer::default();
        let mut stored_fields = Vec::new();

        for arg in args {
            let arg = arg.trim();
            if arg.is_empty() {
                continue;
            }

            // Check for key=value options
            if let Some((key, value)) = arg.split_once('=') {
                let key = key.trim().to_lowercase();
                let value = value.trim().trim_matches(|c| c == '\'' || c == '"');

                match key.as_str() {
                    "tokenizer" => {
                        tokenizer = Tokenizer::from_str(value)?;
                    }
                    "stored" => {
                        stored_fields = value.split(',').map(|s| s.trim().to_string()).collect();
                    }
                    _ => {
                        return Err(Error::schema(format!("Unknown option: {}", key)));
                    }
                }
                continue;
            }

            // Parse field definition: "name TYPE"
            let parts: Vec<&str> = arg.split_whitespace().collect();
            if parts.len() < 2 {
                return Err(Error::schema(format!(
                    "Invalid field definition: '{}'. Expected 'name TYPE'",
                    arg
                )));
            }

            let name = parts[0].to_string();
            let field_type = FieldType::from_str(parts[1])?;

            // Parse optional modifiers
            let stored = parts.iter().any(|&p| p.eq_ignore_ascii_case("STORED"));
            let unindexed = parts.iter().any(|&p| p.eq_ignore_ascii_case("UNINDEXED"));

            fields.push(FieldDef {
                name,
                field_type,
                stored,
                indexed: !unindexed,
            });
        }

        if fields.is_empty() {
            return Err(Error::schema("No fields defined"));
        }

        Ok(TableSchema {
            fields,
            tokenizer,
            stored_fields,
        })
    }

    /// Build the CREATE TABLE statement for SQLite
    /// This defines the columns that SQLite sees for the virtual table
    pub fn to_create_table(&self, table_name: &str) -> String {
        let mut columns = Vec::new();

        for field in &self.fields {
            let sql_type = match field.field_type {
                FieldType::Text | FieldType::Tag => "TEXT",
                FieldType::Integer => "INTEGER",
                FieldType::Float => "REAL",
            };
            columns.push(format!("{} {}", field.name, sql_type));
        }

        // Add hidden columns for tantivy internal use
        columns.push(format!("{} HIDDEN", table_name)); // For MATCH syntax

        format!("CREATE TABLE x({})", columns.join(", "))
    }

    /// Convert to Tantivy schema
    pub fn to_tantivy_schema(&self) -> tantivy::schema::Schema {
        use tantivy::schema::{
            Schema, TextOptions, TextFieldIndexing, IndexRecordOption,
            INDEXED, STORED, FAST, STRING,
        };

        let mut builder = Schema::builder();

        // Always add a rowid field for document identification
        builder.add_i64_field("_rowid", INDEXED | STORED | FAST);

        for field_def in &self.fields {
            match field_def.field_type {
                FieldType::Text => {
                    let text_options = TextOptions::default()
                        .set_indexing_options(
                            TextFieldIndexing::default()
                                .set_tokenizer("en_stem")
                                .set_index_option(IndexRecordOption::WithFreqsAndPositions),
                        )
                        .set_stored();
                    builder.add_text_field(&field_def.name, text_options);
                }
                FieldType::Tag => {
                    // Tags are stored as STRING (exact match, not tokenized)
                    builder.add_text_field(&field_def.name, STRING | STORED);
                }
                FieldType::Integer => {
                    builder.add_i64_field(&field_def.name, INDEXED | STORED | FAST);
                }
                FieldType::Float => {
                    builder.add_f64_field(&field_def.name, INDEXED | STORED | FAST);
                }
            }
        }

        builder.build()
    }

    /// Serialize schema to JSON for storage
    pub fn to_json(&self) -> Result<String> {
        serde_json::to_string(self).map_err(Error::from)
    }

    /// Deserialize schema from JSON
    pub fn from_json(json: &str) -> Result<Self> {
        serde_json::from_str(json).map_err(Error::from)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_simple_schema() {
        let args = vec![
            "title TEXT".to_string(),
            "body TEXT".to_string(),
        ];
        let schema = TableSchema::parse(&args).unwrap();

        assert_eq!(schema.fields.len(), 2);
        assert_eq!(schema.fields[0].name, "title");
        assert_eq!(schema.fields[0].field_type, FieldType::Text);
        assert_eq!(schema.fields[1].name, "body");
    }

    #[test]
    fn test_parse_mixed_types() {
        let args = vec![
            "title TEXT".to_string(),
            "category TAG".to_string(),
            "views INTEGER".to_string(),
            "rating FLOAT".to_string(),
        ];
        let schema = TableSchema::parse(&args).unwrap();

        assert_eq!(schema.fields.len(), 4);
        assert_eq!(schema.fields[0].field_type, FieldType::Text);
        assert_eq!(schema.fields[1].field_type, FieldType::Tag);
        assert_eq!(schema.fields[2].field_type, FieldType::Integer);
        assert_eq!(schema.fields[3].field_type, FieldType::Float);
    }

    #[test]
    fn test_parse_with_tokenizer() {
        let args = vec![
            "content TEXT".to_string(),
            "tokenizer='en_stem'".to_string(),
        ];
        let schema = TableSchema::parse(&args).unwrap();

        assert_eq!(schema.tokenizer, Tokenizer::EnStem);
    }

    #[test]
    fn test_create_table_sql() {
        let args = vec![
            "title TEXT".to_string(),
            "body TEXT".to_string(),
        ];
        let schema = TableSchema::parse(&args).unwrap();
        let sql = schema.to_create_table("articles");

        assert!(sql.contains("title TEXT"));
        assert!(sql.contains("body TEXT"));
        assert!(sql.contains("articles HIDDEN"));
    }

    #[test]
    fn test_tantivy_schema() {
        let args = vec![
            "title TEXT".to_string(),
            "views INTEGER".to_string(),
        ];
        let schema = TableSchema::parse(&args).unwrap();
        let tantivy_schema = schema.to_tantivy_schema();

        assert!(tantivy_schema.get_field("_rowid").is_ok());
        assert!(tantivy_schema.get_field("title").is_ok());
        assert!(tantivy_schema.get_field("views").is_ok());
    }

    #[test]
    fn test_json_round_trip() {
        let args = vec![
            "title TEXT".to_string(),
            "tokenizer='unicode'".to_string(),
        ];
        let schema = TableSchema::parse(&args).unwrap();

        let json = schema.to_json().unwrap();
        let restored = TableSchema::from_json(&json).unwrap();

        assert_eq!(restored.fields.len(), schema.fields.len());
        assert_eq!(restored.tokenizer, schema.tokenizer);
    }
}
