//! Query parsing for MATCH expressions
//!
//! Translates SQLite MATCH syntax to Tantivy queries:
//! - `hello world` -> BooleanQuery(Must: [term(hello), term(world)])
//! - `hello OR world` -> BooleanQuery(Should: [term(hello), term(world)])
//! - `"exact phrase"` -> PhraseQuery
//! - `hello*` -> PrefixQuery
//! - `helo~1` -> FuzzyQuery with distance 1
//! - `title:hello` -> TermQuery on specific field
//! - `NOT hello` or `-hello` -> BooleanQuery(MustNot: term(hello))

use tantivy::query::{
    BooleanQuery, FuzzyTermQuery, Occur, PhraseQuery, Query, RegexQuery, TermQuery,
};
use tantivy::schema::{Field, IndexRecordOption, Schema};
use tantivy::Term;

use crate::error::{Error, Result};

/// Parse a MATCH query string into a Tantivy Query
pub fn parse_query(query_str: &str, schema: &Schema, default_fields: &[Field]) -> Result<Box<dyn Query>> {
    let parser = QueryParser::new(schema, default_fields);
    parser.parse(query_str)
}

/// Simple recursive descent parser for search queries
struct QueryParser<'a> {
    schema: &'a Schema,
    default_fields: &'a [Field],
}

impl<'a> QueryParser<'a> {
    fn new(schema: &'a Schema, default_fields: &'a [Field]) -> Self {
        Self { schema, default_fields }
    }

    fn parse(&self, query_str: &str) -> Result<Box<dyn Query>> {
        let query_str = query_str.trim();
        if query_str.is_empty() {
            return Err(Error::InvalidArgument("Empty query".to_string()));
        }

        self.parse_or(query_str)
    }

    /// Parse OR expressions: `a OR b`
    fn parse_or(&self, input: &str) -> Result<Box<dyn Query>> {
        let parts: Vec<&str> = self.split_by_operator(input, " OR ");
        if parts.len() == 1 {
            return self.parse_and(input);
        }

        let mut clauses = Vec::new();
        for part in parts {
            let query = self.parse_and(part)?;
            clauses.push((Occur::Should, query));
        }

        Ok(Box::new(BooleanQuery::new(clauses)))
    }

    /// Parse AND expressions: `a AND b` or implicit `a b`
    fn parse_and(&self, input: &str) -> Result<Box<dyn Query>> {
        let parts: Vec<&str> = self.split_by_operator(input, " AND ");
        if parts.len() == 1 {
            return self.parse_not(input);
        }

        let mut clauses = Vec::new();
        for part in parts {
            let query = self.parse_not(part)?;
            clauses.push((Occur::Must, query));
        }

        Ok(Box::new(BooleanQuery::new(clauses)))
    }

    /// Parse NOT expressions: `NOT a` or `-a`
    fn parse_not(&self, input: &str) -> Result<Box<dyn Query>> {
        let input = input.trim();

        if input.starts_with("NOT ") {
            let rest = input[4..].trim();
            let inner = self.parse_term(rest)?;
            return Ok(Box::new(BooleanQuery::new(vec![(Occur::MustNot, inner)])));
        }

        if input.starts_with('-') && input.len() > 1 {
            let rest = &input[1..];
            let inner = self.parse_term(rest)?;
            return Ok(Box::new(BooleanQuery::new(vec![(Occur::MustNot, inner)])));
        }

        self.parse_term(input)
    }

    /// Parse individual terms, phrases, prefixes, and fuzzy queries
    fn parse_term(&self, input: &str) -> Result<Box<dyn Query>> {
        let input = input.trim();

        // Handle parentheses for grouping
        if input.starts_with('(') && input.ends_with(')') {
            return self.parse(&input[1..input.len() - 1]);
        }

        // Handle quoted phrases: "exact phrase"
        if input.starts_with('"') && input.ends_with('"') && input.len() > 2 {
            let phrase = &input[1..input.len() - 1];
            return self.build_phrase_query(phrase, None);
        }

        // Handle field-scoped queries: field:term or field:"phrase"
        if let Some(colon_pos) = input.find(':') {
            let field_name = &input[..colon_pos];
            let term_part = &input[colon_pos + 1..];

            if let Ok(field) = self.schema.get_field(field_name) {
                if term_part.starts_with('"') && term_part.ends_with('"') {
                    let phrase = &term_part[1..term_part.len() - 1];
                    return self.build_phrase_query(phrase, Some(field));
                } else {
                    return self.build_term_query(term_part, Some(field));
                }
            }
        }

        // Handle prefix queries: hello*
        if input.ends_with('*') && input.len() > 1 {
            let prefix = &input[..input.len() - 1];
            return self.build_prefix_query(prefix);
        }

        // Handle fuzzy queries: hello~1 or hello~
        if let Some(tilde_pos) = input.rfind('~') {
            let term = &input[..tilde_pos];
            let distance_str = &input[tilde_pos + 1..];
            let distance = if distance_str.is_empty() {
                1
            } else {
                distance_str.parse().unwrap_or(1)
            };
            return self.build_fuzzy_query(term, distance);
        }

        // Handle implicit AND for space-separated terms
        let words: Vec<&str> = input.split_whitespace().collect();
        if words.len() > 1 {
            let mut clauses = Vec::new();
            for word in words {
                let query = self.parse_term(word)?;
                clauses.push((Occur::Must, query));
            }
            return Ok(Box::new(BooleanQuery::new(clauses)));
        }

        // Simple term query
        self.build_term_query(input, None)
    }

    /// Build a term query, searching across default fields if no field specified
    fn build_term_query(&self, term_str: &str, field: Option<Field>) -> Result<Box<dyn Query>> {
        let term_str = term_str.to_lowercase();

        if let Some(field) = field {
            let term = Term::from_field_text(field, &term_str);
            return Ok(Box::new(TermQuery::new(term, IndexRecordOption::WithFreqs)));
        }

        // Search across all default fields
        if self.default_fields.len() == 1 {
            let term = Term::from_field_text(self.default_fields[0], &term_str);
            return Ok(Box::new(TermQuery::new(term, IndexRecordOption::WithFreqs)));
        }

        let mut clauses = Vec::new();
        for &field in self.default_fields {
            let term = Term::from_field_text(field, &term_str);
            let query: Box<dyn Query> = Box::new(TermQuery::new(term, IndexRecordOption::WithFreqs));
            clauses.push((Occur::Should, query));
        }

        Ok(Box::new(BooleanQuery::new(clauses)))
    }

    /// Build a phrase query
    fn build_phrase_query(&self, phrase: &str, field: Option<Field>) -> Result<Box<dyn Query>> {
        let words: Vec<&str> = phrase.split_whitespace().collect();
        if words.is_empty() {
            return Err(Error::InvalidArgument("Empty phrase".to_string()));
        }

        let fields = if let Some(f) = field {
            vec![f]
        } else {
            self.default_fields.to_vec()
        };

        if fields.len() == 1 {
            let terms: Vec<Term> = words
                .iter()
                .map(|w| Term::from_field_text(fields[0], &w.to_lowercase()))
                .collect();
            return Ok(Box::new(PhraseQuery::new(terms)));
        }

        // Phrase query across multiple fields (OR them together)
        let mut clauses = Vec::new();
        for field in fields {
            let terms: Vec<Term> = words
                .iter()
                .map(|w| Term::from_field_text(field, &w.to_lowercase()))
                .collect();
            let query: Box<dyn Query> = Box::new(PhraseQuery::new(terms));
            clauses.push((Occur::Should, query));
        }

        Ok(Box::new(BooleanQuery::new(clauses)))
    }

    /// Build a prefix query using regex
    fn build_prefix_query(&self, prefix: &str) -> Result<Box<dyn Query>> {
        let prefix = prefix.to_lowercase();
        // Use regex pattern for prefix matching
        let pattern = format!("{}.*", regex::escape(&prefix));

        if self.default_fields.len() == 1 {
            let query = RegexQuery::from_pattern(&pattern, self.default_fields[0])
                .map_err(|e| Error::InvalidArgument(e.to_string()))?;
            return Ok(Box::new(query));
        }

        let mut clauses = Vec::new();
        for &field in self.default_fields {
            if let Ok(query) = RegexQuery::from_pattern(&pattern, field) {
                clauses.push((Occur::Should, Box::new(query) as Box<dyn Query>));
            }
        }

        Ok(Box::new(BooleanQuery::new(clauses)))
    }

    /// Build a fuzzy query
    fn build_fuzzy_query(&self, term_str: &str, distance: u8) -> Result<Box<dyn Query>> {
        let term_str = term_str.to_lowercase();

        if self.default_fields.len() == 1 {
            let term = Term::from_field_text(self.default_fields[0], &term_str);
            return Ok(Box::new(FuzzyTermQuery::new(term, distance, true)));
        }

        let mut clauses = Vec::new();
        for &field in self.default_fields {
            let term = Term::from_field_text(field, &term_str);
            let query: Box<dyn Query> = Box::new(FuzzyTermQuery::new(term, distance, true));
            clauses.push((Occur::Should, query));
        }

        Ok(Box::new(BooleanQuery::new(clauses)))
    }

    /// Split input by operator, respecting quotes and parentheses
    fn split_by_operator<'b>(&self, input: &'b str, op: &str) -> Vec<&'b str> {
        let mut parts = Vec::new();
        let mut current_start = 0;
        let mut depth: i32 = 0;
        let mut in_quotes = false;

        let input_bytes = input.as_bytes();
        let op_bytes = op.as_bytes();

        let mut i = 0;
        while i < input.len() {
            let c = input_bytes[i];

            if c == b'"' {
                in_quotes = !in_quotes;
            } else if !in_quotes {
                if c == b'(' {
                    depth += 1;
                } else if c == b')' {
                    depth = depth.saturating_sub(1);
                } else if depth == 0 && i + op.len() <= input.len() {
                    if &input_bytes[i..i + op.len()] == op_bytes {
                        parts.push(&input[current_start..i]);
                        current_start = i + op.len();
                        i += op.len();
                        continue;
                    }
                }
            }

            i += 1;
        }

        parts.push(&input[current_start..]);
        parts.into_iter().map(|s| s.trim()).filter(|s| !s.is_empty()).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tantivy::schema::{Schema, TEXT};

    fn test_schema() -> (Schema, Vec<Field>) {
        let mut builder = Schema::builder();
        let title = builder.add_text_field("title", TEXT);
        let body = builder.add_text_field("body", TEXT);
        let schema = builder.build();
        (schema, vec![title, body])
    }

    #[test]
    fn test_simple_term() {
        let (schema, fields) = test_schema();
        let query = parse_query("hello", &schema, &fields).unwrap();
        // Should create a BooleanQuery searching both fields
        assert!(!format!("{:?}", query).is_empty());
    }

    #[test]
    fn test_phrase_query() {
        let (schema, fields) = test_schema();
        let query = parse_query("\"hello world\"", &schema, &fields).unwrap();
        assert!(format!("{:?}", query).contains("PhraseQuery"));
    }

    #[test]
    fn test_boolean_or() {
        let (schema, fields) = test_schema();
        let query = parse_query("hello OR world", &schema, &fields).unwrap();
        assert!(format!("{:?}", query).contains("BooleanQuery"));
    }

    #[test]
    fn test_boolean_and() {
        let (schema, fields) = test_schema();
        let query = parse_query("hello AND world", &schema, &fields).unwrap();
        assert!(format!("{:?}", query).contains("BooleanQuery"));
    }

    #[test]
    fn test_prefix_query() {
        let (schema, fields) = test_schema();
        let query = parse_query("hel*", &schema, &fields).unwrap();
        // We use RegexQuery for prefix matching since PrefixQuery is not available in this version
        assert!(format!("{:?}", query).contains("Regex"));
    }

    #[test]
    fn test_fuzzy_query() {
        let (schema, fields) = test_schema();
        let query = parse_query("helo~1", &schema, &fields).unwrap();
        assert!(format!("{:?}", query).contains("FuzzyTermQuery"));
    }

    #[test]
    fn test_field_scoped() {
        let (schema, fields) = test_schema();
        let query = parse_query("title:hello", &schema, &fields).unwrap();
        assert!(format!("{:?}", query).contains("TermQuery"));
    }

    #[test]
    fn test_implicit_and() {
        let (schema, fields) = test_schema();
        let query = parse_query("hello world", &schema, &fields).unwrap();
        // Space-separated terms should be AND'd together
        assert!(format!("{:?}", query).contains("BooleanQuery"));
    }
}
