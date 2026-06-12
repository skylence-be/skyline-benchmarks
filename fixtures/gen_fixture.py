#!/usr/bin/env python3
"""Generate deterministic synthetic Rust crate 'orbital' for the crossmodel benchmark.

Usage:
    python3 gen_fixture.py --output-dir <dir>        # write orbital/ crate into <dir>
    python3 gen_fixture.py --emit-manifests <dir>    # write tN.expected.sha256 into <dir>
    python3 gen_fixture.py --output-dir <dir> --emit-manifests <dir>  # both

The crate is fully deterministic (no RNG, stable across Python versions).
Manifests list sha256 of EVERY file in the expected post-task state.
"""
import argparse, hashlib, pathlib, re, sys

# -- T1/T2/T3 reference transforms --------------------------------------------

_T1_RE = re.compile(r'(?<![.\w])normalize_path(?=\()')

def _apply_t1_line(line: str, in_url_impl: bool) -> str:
    stripped = line.lstrip()
    if stripped.startswith('//') or stripped.startswith('///') or stripped.startswith('/*') or stripped.startswith('* ') or stripped == '*':
        return line
    if in_url_impl and 'fn normalize_path' in line:
        return line
    return _T1_RE.sub('canonicalize_path', line)

def apply_t1(content: str) -> str:
    """Rename free-function normalize_path->canonicalize_path; leave .normalize_path() and method def."""
    lines = content.splitlines(keepends=True)
    result = []
    in_impl = False
    impl_depth = 0
    for line in lines:
        stripped = line.strip()
        if re.match(r'impl\s+UrlCanon\b', stripped):
            in_impl = True
            impl_depth = line.count('{') - line.count('}')
        elif in_impl:
            impl_depth += line.count('{') - line.count('}')
            if impl_depth <= 0:
                in_impl = False
                impl_depth = 0
        result.append(_apply_t1_line(line, in_impl))
    return ''.join(result)

_T2_TARGET = 'track::event('
_T2_SKIP   = ('track::event_id(', 'track::event_v2(')

def _apply_t2_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
        return line
    pos = 0
    result = []
    while pos < len(line):
        if line[pos] == '"':
            end = pos + 1
            while end < len(line) and (line[end] != '"' or line[end-1] == '\\'):
                end += 1
            result.append(line[pos:end+1])
            pos = end + 1
            continue
        found = line.find(_T2_TARGET, pos)
        if found == -1:
            result.append(line[pos:])
            break
        skip = any(line[found:].startswith(s) for s in _T2_SKIP)
        if skip:
            result.append(line[pos:found + len(_T2_TARGET)])
            pos = found + len(_T2_TARGET)
            continue
        inner_start = found + len(_T2_TARGET)
        depth = 1; i = inner_start
        while i < len(line) and depth > 0:
            if line[i] == '(': depth += 1
            elif line[i] == ')': depth -= 1
            if depth > 0: i += 1
        if depth == 0:
            args = line[inner_start:i]
            result.append(line[pos:found])
            result.append(f'track::event_v2({args}, track::Flags::default())')
            pos = i + 1
        else:
            result.append(line[pos:found + len(_T2_TARGET)])
            pos = found + len(_T2_TARGET)
    return ''.join(result)

def apply_t2(content: str) -> str:
    return ''.join(_apply_t2_line(ln) for ln in content.splitlines(keepends=True))

def apply_t3(content: str) -> str:
    """Change MAX_RETRIES inside pub mod prod { ... } from 10 to 7."""
    lines = content.splitlines(keepends=True)
    result = []
    in_prod = False
    prod_depth = 0
    for line in lines:
        stripped = line.strip()
        if re.match(r'pub\s+mod\s+prod\s*\{', stripped):
            in_prod = True
            prod_depth = line.count('{') - line.count('}')
            result.append(line)
            continue
        if in_prod:
            prod_depth += line.count('{') - line.count('}')
            if prod_depth <= 0:
                in_prod = False
                prod_depth = 0
            if in_prod and 'MAX_RETRIES' in line and ': u32 = 10;' in line:
                line = line.replace(': u32 = 10;', ': u32 = 7;')
        result.append(line)
    return ''.join(result)

# -- File content generators --------------------------------------------------

def _cargo_toml() -> str:
    return (
        '[package]\n'
        'name = "orbital"\n'
        'version = "0.1.0"\n'
        'edition = "2021"\n'
        '\n'
        '[features]\n'
        'default = []\n'
        'dev = []\n'
        'staging = []\n'
        '\n'
        '[lib]\n'
        'name = "orbital"\n'
        '\n'
        '[[bin]]\n'
        'name = "orbital"\n'
        'path = "src/main.rs"\n'
    )

def _src_lib_rs() -> str:
    return (
        '//! Orbital crate root.\n'
        'pub mod config;\n'
        'pub mod error;\n'
        'pub mod metrics;\n'
        'pub mod path;\n'
        'pub mod tracking;\n'
        'pub mod limits;\n'
        '\n'
        'use path::normalize_path;\n'
        'pub use tracking::track;\n'
        'pub use path::normalize_path as crate_normalize;\n'
        '\n'
        'use tracking::{EventType, EventData};\n'
        '\n'
        '/// Canonicalize a crate-level path string.\n'
        'pub fn resolve_path(raw: &str) -> String {\n'
        '    normalize_path(raw)\n'
        '}\n'
        '\n'
        'pub fn emit_startup(label: &str) {\n'
        '    track::event(EventType::Startup, EventData::label(label));\n'
        '    track::event(EventType::Init, EventData::empty());\n'
        '    track::event(EventType::Ready, EventData::label(label));\n'
        '}\n'
    )

def _src_main_rs() -> str:
    return (
        '//! Orbital binary entry point.\n'
        'use orbital::path::{normalize_path, url::UrlCanon};\n'
        'use orbital::tracking::{track, EventType, EventData};\n'
        '\n'
        'fn main() {\n'
        '    let raw = std::env::args().nth(1).unwrap_or_default();\n'
        '    // Free function call: rename to canonicalize_path\n'
        '    let canonical = normalize_path(&raw);\n'
        '    // Method call on UrlCanon: must NOT rename\n'
        '    let canon = UrlCanon::new();\n'
        '    let url_norm = canon.normalize_path(&raw);\n'
        '    track::event(EventType::Start, EventData::label("main"));\n'
        '    track::event(EventType::Process, EventData::label(&canonical));\n'
        '    track::event(EventType::Process, EventData::label(&url_norm));\n'
        '    track::event(EventType::End, EventData::empty());\n'
        '    // event_id decoys\n'
        '    track::event_id(EventType::Start);\n'
        '    track::event_id(EventType::End);\n'
        '    println!("{canonical}");\n'
        '    println!("{url_norm}");\n'
        '}\n'
    )

def _src_error_rs() -> str:
    return (
        '//! Orbital error types.\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '#[derive(Debug)]\n'
        'pub enum OrbitalError {\n'
        '    Io(std::io::Error),\n'
        '    Parse(String),\n'
        '    Limit(String),\n'
        '}\n'
        '\n'
        'impl std::fmt::Display for OrbitalError {\n'
        '    fn fmt(&self, f: &mut std::fmt::Formatter<\'_>) -> std::fmt::Result {\n'
        '        match self {\n'
        '            Self::Io(e)    => write!(f, "io: {e}"),\n'
        '            Self::Parse(s) => write!(f, "parse: {s}"),\n'
        '            Self::Limit(s) => write!(f, "limit: {s}"),\n'
        '        }\n'
        '    }\n'
        '}\n'
        '\n'
        'impl std::error::Error for OrbitalError {}\n'
        '\n'
        'pub fn emit_error(kind: &str, msg: &str) {\n'
        '    track::event(EventType::Error, EventData::label(kind));\n'
        '    track::event(EventType::Error, EventData::label(msg));\n'
        '}\n'
    )

def _src_config_rs() -> str:
    return (
        '//! Orbital runtime configuration.\n'
        'use crate::path::{normalize_path, url::UrlCanon};\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '#[derive(Debug, Clone)]\n'
        'pub struct Config {\n'
        '    pub data_dir: String,\n'
        '    pub log_level: String,\n'
        '    pub max_workers: usize,\n'
        '}\n'
        '\n'
        'impl Config {\n'
        '    pub fn new(data_dir: &str) -> Self {\n'
        '        // Free function call: rename to canonicalize_path\n'
        '        let dir = normalize_path(data_dir);\n'
        '        Self { data_dir: dir, log_level: "info".into(), max_workers: 4 }\n'
        '    }\n'
        '    pub fn resolve_dir(&self) -> String {\n'
        '        // Method call via UrlCanon: must NOT rename\n'
        '        let canon = UrlCanon::new();\n'
        '        canon.normalize_path(&self.data_dir)\n'
        '    }\n'
        '    pub fn emit_loaded(&self) {\n'
        '        track::event(EventType::Config, EventData::label("loaded"));\n'
        '        track::event(EventType::Config, EventData::label(&self.data_dir));\n'
        '        track::event(EventType::Config, EventData::label(&self.log_level));\n'
        '    }\n'
        '}\n'
        '\n'
        '// Local fn named event: T2 decoy, must NOT be transformed\n'
        'fn event(kind: &str) -> String { format!("config-event:{kind}") }\n'
    )

def _src_metrics_rs() -> str:
    return (
        '//! Orbital metrics collection.\n'
        'use crate::path::normalize_path;\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        'pub struct MetricsCollector { pub prefix: String }\n'
        '\n'
        'impl MetricsCollector {\n'
        '    pub fn new(base_path: &str) -> Self {\n'
        '        // Free function call: rename to canonicalize_path\n'
        '        Self { prefix: normalize_path(base_path) }\n'
        '    }\n'
        '    pub fn record(&self, key: &str, value: u64) {\n'
        '        // Free function call: rename to canonicalize_path\n'
        '        let full_key = normalize_path(&format!("{}/{}", self.prefix, key));\n'
        '        track::event(EventType::Metric, EventData::label(&full_key));\n'
        '        track::event(EventType::Metric, EventData::label(key));\n'
        '        track::event(EventType::Metric, EventData::label(&value.to_string()));\n'
        '    }\n'
        '    pub fn flush(&self) {\n'
        '        // event_id decoy\n'
        '        track::event_id(EventType::Flush);\n'
        '    }\n'
        '}\n'
    )

def _src_path_mod_rs() -> str:
    return (
        '//! Path utilities for the orbital crate.\n'
        '//!\n'
        '//! The free function `normalize_path` normalizes a filesystem path string.\n'
        '//! Do not confuse with `UrlCanon::normalize_path`, which handles URL canonicalization.\n'
        '\n'
        'pub mod canonicalize;\n'
        'pub mod glob;\n'
        'pub mod indexer;\n'
        'pub mod resolve;\n'
        'pub mod url;\n'
        'pub mod walker;\n'
        'pub mod watcher;\n'
        '\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '/// Normalize a raw filesystem path: collapse `.`, `..`, and redundant separators.\n'
        '///\n'
        '/// This is the FREE FUNCTION, distinct from `UrlCanon::normalize_path`.\n'
        'pub fn normalize_path(path: &str) -> String {\n'
        '    let mut parts: Vec<&str> = Vec::new();\n'
        '    for segment in path.split(\'/\') {\n'
        '        match segment {\n'
        '            "" | "." => {}\n'
        '            ".." => { parts.pop(); }\n'
        '            s => parts.push(s),\n'
        '        }\n'
        '    }\n'
        '    if path.starts_with(\'/\') {\n'
        '        format!("/{}", parts.join("/"))\n'
        '    } else {\n'
        '        parts.join("/")\n'
        '    }\n'
        '}\n'
        '\n'
        'pub fn emit_path_op(op: &str, p: &str) {\n'
        '    track::event(EventType::Path, EventData::label(op));\n'
        '    track::event(EventType::Path, EventData::label(p));\n'
        '    track::event(EventType::Path, EventData::empty());\n'
        '}\n'
        '\n'
        '#[cfg(test)]\n'
        'mod tests {\n'
        '    use super::*;\n'
        '\n'
        '    // Test name trap: test_normalize_path must NOT be renamed\n'
        '    #[test]\n'
        '    fn test_normalize_path() {\n'
        '        // Free function call inside test body: SHOULD be renamed\n'
        '        assert_eq!(normalize_path("a/b/../c"), "a/c");\n'
        '        assert_eq!(normalize_path("/foo//bar"), "/foo/bar");\n'
        '    }\n'
        '\n'
        '    #[test]\n'
        '    fn test_basic_paths() {\n'
        '        // String literal trap: must NOT be transformed (no paren follows)\n'
        '        let op_name: &str = "normalize_path";\n'
        '        assert!(!op_name.is_empty());\n'
        '        // Comment trap: normalize_path("/tmp/foo") would return "/tmp/foo"\n'
        '        let _ = normalize_path("x/y/z");\n'
        '    }\n'
        '}\n'
    )

def _src_path_resolve_rs() -> str:
    return (
        '//! Path resolution utilities.\n'
        'use super::normalize_path;\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        'pub struct Resolver { pub base: String }\n'
        '\n'
        'impl Resolver {\n'
        '    pub fn new(base: &str) -> Self { Self { base: normalize_path(base) } }\n'
        '    pub fn resolve(&self, rel: &str) -> String {\n'
        '        normalize_path(&format!("{}/{}", self.base, rel))\n'
        '    }\n'
        '}\n'
        '\n'
        'pub fn resolve_relative(base: &str, rel: &str) -> String {\n'
        '    let r = normalize_path(&format!("{base}/{rel}"));\n'
        '    track::event(EventType::Path, EventData::label("resolve"));\n'
        '    track::event(EventType::Path, EventData::label(&r));\n'
        '    track::event(EventType::Path, EventData::empty());\n'
        '    r\n'
        '}\n'
    )

def _src_path_glob_rs() -> str:
    return (
        '//! Glob pattern matching over normalized paths.\n'
        'use super::normalize_path;\n'
        '\n'
        'pub struct GlobMatcher { pub pattern: String }\n'
        '\n'
        'impl GlobMatcher {\n'
        '    pub fn new(pattern: &str) -> Self {\n'
        '        Self { pattern: normalize_path(pattern) }\n'
        '    }\n'
        '    pub fn matches(&self, path: &str) -> bool {\n'
        '        normalize_path(path).starts_with(&self.pattern)\n'
        '    }\n'
        '}\n'
    )

def _src_path_canonicalize_rs() -> str:
    return (
        '//! Canonical path computation.\n'
        'use super::normalize_path;\n'
        'use super::url::UrlCanon;\n'
        '\n'
        'pub struct CanonicalPath(String);\n'
        '\n'
        'impl CanonicalPath {\n'
        '    pub fn new(raw: &str) -> Self {\n'
        '        // Free function call: rename to canonicalize_path\n'
        '        Self(normalize_path(raw))\n'
        '    }\n'
        '    pub fn join(&self, other: &str) -> Self {\n'
        '        Self(normalize_path(&format!("{}/{}", self.0, other)))\n'
        '    }\n'
        '}\n'
        '\n'
        'pub fn canonicalize_url_path(url_str: &str) -> String {\n'
        '    let canon = UrlCanon::new();\n'
        '    // Method calls on UrlCanon: must NOT be renamed\n'
        '    let step1 = canon.normalize_path(url_str);\n'
        '    canon.normalize_path(&step1)\n'
        '}\n'
    )

def _src_path_walker_rs() -> str:
    return (
        '//! Filesystem walker with path normalization.\n'
        'use super::normalize_path;\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        'pub struct Walker { pub root: String, pub depth: usize }\n'
        '\n'
        'impl Walker {\n'
        '    pub fn new(root: &str, depth: usize) -> Self {\n'
        '        Self { root: normalize_path(root), depth }\n'
        '    }\n'
        '    pub fn visit(&self, path: &str) -> String {\n'
        '        let norm = normalize_path(path);\n'
        '        track::event(EventType::Walk, EventData::label(&norm));\n'
        '        track::event(EventType::Walk, EventData::label(&self.root));\n'
        '        norm\n'
        '    }\n'
        '}\n'
    )

def _src_path_watcher_rs() -> str:
    return (
        '//! Filesystem watcher with path normalization.\n'
        'use super::normalize_path;\n'
        '\n'
        'pub struct Watcher { paths: Vec<String> }\n'
        '\n'
        'impl Watcher {\n'
        '    pub fn new() -> Self { Self { paths: Vec::new() } }\n'
        '    pub fn watch(&mut self, path: &str) {\n'
        '        self.paths.push(normalize_path(path));\n'
        '    }\n'
        '    pub fn unwatch(&mut self, path: &str) -> bool {\n'
        '        let norm = normalize_path(path);\n'
        '        if let Some(pos) = self.paths.iter().position(|p| p == &norm) {\n'
        '            self.paths.remove(pos); true\n'
        '        } else { false }\n'
        '    }\n'
        '}\n'
    )

def _src_path_indexer_rs() -> str:
    return (
        '//! Path indexer with multiple normalize calls.\n'
        'use super::normalize_path;\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        'pub struct Indexer { entries: std::collections::HashMap<String, usize> }\n'
        '\n'
        'impl Indexer {\n'
        '    pub fn new() -> Self { Self { entries: Default::default() } }\n'
        '    pub fn insert(&mut self, path: &str) -> usize {\n'
        '        let norm = normalize_path(path);\n'
        '        let n = self.entries.len();\n'
        '        *self.entries.entry(norm).or_insert(n)\n'
        '    }\n'
        '    pub fn lookup(&self, path: &str) -> Option<usize> {\n'
        '        let norm = normalize_path(path);\n'
        '        track::event(EventType::Index, EventData::label("lookup"));\n'
        '        track::event(EventType::Index, EventData::label(&norm));\n'
        '        self.entries.get(&norm).copied()\n'
        '    }\n'
        '    pub fn reindex(&mut self, old: &str, new_path: &str) {\n'
        '        let old_n = normalize_path(old);\n'
        '        let new_n = normalize_path(new_path);\n'
        '        if let Some(v) = self.entries.remove(&old_n) {\n'
        '            self.entries.insert(new_n, v);\n'
        '        }\n'
        '    }\n'
        '}\n'
    )

def _src_path_url_rs() -> str:
    return (
        '//! URL canonicalization. UrlCanon::normalize_path is a METHOD, not the free function.\n'
        '//! Task T1 must NOT rename the method or its call sites.\n'
        '\n'
        '#[derive(Debug, Clone, Default)]\n'
        'pub struct UrlCanon { pub scheme: String, pub host: String }\n'
        '\n'
        'impl UrlCanon {\n'
        '    pub fn new() -> Self { Self::default() }\n'
        '    pub fn with_host(host: &str) -> Self {\n'
        '        Self { scheme: "https".into(), host: host.into() }\n'
        '    }\n'
        '    /// Normalize a URL path segment (method: must NOT be renamed by T1).\n'
        '    pub fn normalize_path(&self, path: &str) -> String {\n'
        '        path.replace("//", "/").to_lowercase()\n'
        '    }\n'
        '    pub fn full_url(&self, path: &str) -> String {\n'
        '        // Method call on self: must NOT be renamed\n'
        '        let norm = self.normalize_path(path);\n'
        '        format!("{}://{}{}", self.scheme, self.host, norm)\n'
        '    }\n'
        '    pub fn is_normalized(&self, path: &str) -> bool {\n'
        '        // Method call on self: must NOT be renamed\n'
        '        self.normalize_path(path) == path\n'
        '    }\n'
        '}\n'
    )

def _src_tracking_mod_rs() -> str:
    return (
        '//! Tracking module: EventType, EventData, Flags, and the track:: namespace.\n'
        'pub mod analyzer;\n'
        'pub mod dispatcher;\n'
        'pub mod events;\n'
        'pub mod recorder;\n'
        '\n'
        '#[derive(Debug, Clone, Copy, PartialEq)]\n'
        'pub enum EventType {\n'
        '    Startup, Init, Ready, Start, Process, End,\n'
        '    Config, Error, Metric, Flush,\n'
        '    Path, Walk, Index, Track, Record, Dispatch,\n'
        '}\n'
        '\n'
        '#[derive(Debug, Clone)]\n'
        'pub struct EventData { pub label: Option<String>, pub value: Option<u64> }\n'
        '\n'
        'impl EventData {\n'
        '    pub fn empty() -> Self { Self { label: None, value: None } }\n'
        '    pub fn label(s: &str) -> Self { Self { label: Some(s.into()), value: None } }\n'
        '    pub fn value(v: u64) -> Self { Self { label: None, value: Some(v) } }\n'
        '}\n'
        '\n'
        '#[derive(Debug, Clone, Default)]\n'
        'pub struct Flags { pub async_write: bool, pub compress: bool }\n'
        '\n'
        'impl Flags {\n'
        '    pub fn default() -> Self { Self { async_write: false, compress: false } }\n'
        '}\n'
        '\n'
        'pub mod track {\n'
        '    use super::{EventType, EventData, Flags};\n'
        '    pub fn event(_kind: EventType, _data: EventData) {}\n'
        '    pub fn event_v2(_kind: EventType, _data: EventData, _flags: Flags) {}\n'
        '    pub fn event_id(_kind: EventType) -> u64 { 0 }\n'
        '}\n'
    )

def _src_tracking_events_rs() -> str:
    return (
        '//! Event emission helpers.\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '// Local fn named event: T2 decoy, must NOT be transformed\n'
        'fn event(kind: EventType) -> EventData { EventData::label(&format!("{kind:?}")) }\n'
        '\n'
        'pub fn emit_startup_sequence(label: &str) {\n'
        '    track::event(EventType::Startup, EventData::label(label));\n'
        '    track::event(EventType::Init, EventData::empty());\n'
        '    track::event_id(EventType::Startup);\n'
        '    track::event_id(EventType::Init);\n'
        '    track::event_id(EventType::Ready);\n'
        '}\n'
        '\n'
        'pub fn emit_process_events(input: &str, output: &str) {\n'
        '    track::event(EventType::Process, EventData::label(input));\n'
        '    track::event(EventType::Process, EventData::label(output));\n'
        '}\n'
        '\n'
        'pub fn emit_misc() {\n'
        '    // Comment trap: track::event(EventType::Debug, EventData::empty())\n'
        '    let _label: &str = "track::event(error, payload) — deprecated, use event_v2";\n'
        '    let _e = event(EventType::Track);\n'
        '}\n'
    )

def _src_tracking_recorder_rs() -> str:
    return (
        '//! Event recorder.\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '// T2 decoy: local fn event\n'
        'fn event(kind: EventType, label: &str) -> String { format!("{kind:?}:{label}") }\n'
        '\n'
        'pub struct Recorder { pub session: String }\n'
        '\n'
        'impl Recorder {\n'
        '    pub fn new(session: &str) -> Self { Self { session: session.into() } }\n'
        '    pub fn record_start(&self) {\n'
        '        track::event(EventType::Start, EventData::label(&self.session));\n'
        '        track::event_id(EventType::Start);\n'
        '    }\n'
        '    pub fn record_progress(&self, step: &str) {\n'
        '        track::event(EventType::Process, EventData::label(step));\n'
        '        track::event(EventType::Process, EventData::label(&self.session));\n'
        '        track::event_id(EventType::Process);\n'
        '    }\n'
        '    pub fn record_end(&self) {\n'
        '        track::event(EventType::End, EventData::label(&self.session));\n'
        '    }\n'
        '    pub fn record_error(&self, msg: &str) {\n'
        '        track::event(EventType::Error, EventData::label(msg));\n'
        '    }\n'
        '    pub fn describe(&self) -> String { event(EventType::Record, &self.session) }\n'
        '}\n'
    )

def _src_tracking_dispatcher_rs() -> str:
    return (
        '//! Event dispatcher.\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '// T2 decoy: local fn event\n'
        'fn event(to: &str) -> String { format!("dispatch-to:{to}") }\n'
        '\n'
        'pub struct Dispatcher { pub target: String }\n'
        '\n'
        'impl Dispatcher {\n'
        '    pub fn new(target: &str) -> Self { Self { target: target.into() } }\n'
        '    pub fn dispatch(&self, kind: EventType, payload: &str) {\n'
        '        track::event(kind, EventData::label(payload));\n'
        '        track::event(kind, EventData::label(&self.target));\n'
        '    }\n'
        '    pub fn dispatch_batch(&self, kinds: &[(EventType, &str)]) {\n'
        '        for (kind, payload) in kinds {\n'
        '            track::event(*kind, EventData::label(payload));\n'
        '        }\n'
        '    }\n'
        '    pub fn dispatch_error(&self, msg: &str) {\n'
        '        track::event(EventType::Error, EventData::label(msg));\n'
        '    }\n'
        '    pub fn describe_route(&self) -> String { event(&self.target) }\n'
        '}\n'
    )

def _src_tracking_analyzer_rs() -> str:
    return (
        '//! Event analyzer.\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        '// T2 decoy: local fn event\n'
        'fn event(label: &str) -> EventData { EventData::label(label) }\n'
        '\n'
        'pub struct Analyzer;\n'
        '\n'
        'impl Analyzer {\n'
        '    pub fn analyze(&self, kind: EventType, data: &EventData) {\n'
        '        track::event(kind, event("analyze-start"));\n'
        '        track::event(kind, data.clone());\n'
        '        track::event_id(kind);\n'
        '    }\n'
        '    pub fn summarize(&self, events: &[(EventType, &str)]) -> usize {\n'
        '        let mut count = 0;\n'
        '        for (k, label) in events {\n'
        '            track::event(*k, EventData::label(label));\n'
        '            count += 1;\n'
        '        }\n'
        '        count\n'
        '    }\n'
        '}\n'
    )

def _src_limits_mod_rs() -> str:
    return (
        '//! Limits module: per-environment configuration blocks.\n'
        'pub mod config1;\n'
        'pub mod config2;\n'
        'pub mod config3;\n'
        'pub mod config4;\n'
        'pub mod config5;\n'
        'pub mod config6;\n'
        'pub mod config7;\n'
        'pub mod config8;\n'
        '\n'
        'use crate::tracking::{track, EventType, EventData};\n'
        '\n'
        'pub fn emit_limits_loaded(block: u32) {\n'
        '    track::event(EventType::Config, EventData::label("limits-loaded"));\n'
        '    track::event(EventType::Config, EventData::value(block as u64));\n'
        '    track::event(EventType::Ready, EventData::empty());\n'
        '}\n'
    )

def _gen_limits_file(n: int) -> str:
    """Generate src/limits/configN.rs (~3200 lines) with dev/staging/prod MAX_RETRIES blocks."""
    lines = [
        f'//! Orbital limits configuration block {n}.',
        f'//! Each environment (dev/staging/prod) defines its own retry and queue limits.',
        '',
    ]
    # 100 structs x ~16 lines = ~1600 lines
    for i in range(1, 101):
        lines += [
            f'/// Limit group {n}_{i:03d}.',
            f'#[derive(Debug, Clone, PartialEq)]',
            f'pub struct LimitGroup{n}x{i:03d} {{',
            f'    pub max_connections: u32,',
            f'    pub max_requests: u32,',
            f'    pub timeout_ms: u64,',
            f'    pub retry_budget: u32,',
            f'    pub burst_capacity: u32,',
            f'}}',
            '',
            f'impl LimitGroup{n}x{i:03d} {{',
            f'    pub fn new() -> Self {{',
            f'        Self {{ max_connections: {n+i}, max_requests: {(n+i)*10}, timeout_ms: {(n+i)*100}, retry_budget: {n+i}, burst_capacity: {(n+i)*2} }}',
            f'    }}',
            f'    pub fn is_valid(&self) -> bool {{ self.max_connections > 0 && self.timeout_ms > 0 }}',
            f'}}',
            '',
        ]
    # 50 constants x ~3 lines = ~150 lines
    for i in range(1, 51):
        lines += [
            f'/// Configured capacity constant cfg{n}_cap{i:02d}.',
            f'pub const CAPACITY_{n}_{i:02d}: u32 = {n * 100 + i};',
            '',
        ]
    # dev/staging/prod blocks (T3 targets)
    lines += [
        '',
        f'/// Environment-specific retry configuration for limits block {n}.',
        '',
        f'/// Development environment limits (permissive for fast iteration).',
        f'pub mod dev {{',
        f'    /// Maximum retry attempts in the development environment.',
        f'    pub const MAX_RETRIES: u32 = 3;',
        f'    /// Maximum queue depth in dev.',
        f'    pub const MAX_QUEUE_DEPTH: u32 = 50;',
        f'}}',
        '',
        f'/// Staging environment limits (mirrors prod topology).',
        f'pub mod staging {{',
        f'    /// Maximum retry attempts in staging.',
        f'    pub const MAX_RETRIES: u32 = 5;',
        f'    /// Maximum queue depth in staging.',
        f'    pub const MAX_QUEUE_DEPTH: u32 = 200;',
        f'}}',
        '',
        f'/// Production environment limits (conservative for reliability).',
        f'pub mod prod {{',
        f'    /// Maximum retry attempts in production.',
        f'    pub const MAX_RETRIES: u32 = 10;',
        f'    /// Maximum queue depth in production.',
        f'    pub const MAX_QUEUE_DEPTH: u32 = 1000;',
        f'}}',
        '',
    ]
    # Post-filler: 80 more structs x ~18 lines = ~1440 lines  (total ~3370)
    for i in range(101, 181):
        lines += [
            f'/// Post-limit helper struct {n}_{i:03d}.',
            f'#[derive(Debug, Default)]',
            f'pub struct PostHelper{n}x{i:03d} {{',
            f'    pub value: u64,',
            f"    pub label: &'static str,",
            f'}}',
            '',
            f'impl PostHelper{n}x{i:03d} {{',
            f"    pub fn new(v: u64, l: &'static str) -> Self {{ Self {{ value: v, label: l }} }}",
            f'    pub fn describe(&self) -> String {{ format!("{{}}:{{}}", self.label, self.value) }}',
            f'    pub fn scaled(&self, f: u64) -> u64 {{ self.value.saturating_mul(f) }}',
            f'    pub fn is_zero(&self) -> bool {{ self.value == 0 }}',
            f'    pub fn increment(&mut self) {{ self.value += 1; }}',
            f'    pub fn reset(&mut self) {{ self.value = 0; }}',
            f'}}',
            '',
        ]
    return '\n'.join(lines) + '\n'

# -- File map + manifest generation -------------------------------------------

def _all_files() -> dict:
    files = {
        'Cargo.toml':                   _cargo_toml(),
        'src/lib.rs':                   _src_lib_rs(),
        'src/main.rs':                  _src_main_rs(),
        'src/error.rs':                 _src_error_rs(),
        'src/config.rs':                _src_config_rs(),
        'src/metrics.rs':               _src_metrics_rs(),
        'src/path/mod.rs':              _src_path_mod_rs(),
        'src/path/resolve.rs':          _src_path_resolve_rs(),
        'src/path/glob.rs':             _src_path_glob_rs(),
        'src/path/canonicalize.rs':     _src_path_canonicalize_rs(),
        'src/path/walker.rs':           _src_path_walker_rs(),
        'src/path/watcher.rs':          _src_path_watcher_rs(),
        'src/path/indexer.rs':          _src_path_indexer_rs(),
        'src/path/url.rs':              _src_path_url_rs(),
        'src/tracking/mod.rs':          _src_tracking_mod_rs(),
        'src/tracking/events.rs':       _src_tracking_events_rs(),
        'src/tracking/recorder.rs':     _src_tracking_recorder_rs(),
        'src/tracking/dispatcher.rs':   _src_tracking_dispatcher_rs(),
        'src/tracking/analyzer.rs':     _src_tracking_analyzer_rs(),
        'src/limits/mod.rs':            _src_limits_mod_rs(),
    }
    for n in range(1, 9):
        files[f'src/limits/config{n}.rs'] = _gen_limits_file(n)
    return files

def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()

def _write_manifest(manifest_dir: pathlib.Path, task: str, files: dict) -> None:
    lines = [f'# Expected sha256 manifest for task {task}', '# Format: sha256  relpath', '']
    for relpath, content in sorted(files.items()):
        lines.append(f'{_sha256(content)}  {relpath}')
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f'{task}.expected.sha256').write_text('\n'.join(lines) + '\n')
    print(f'  wrote {task}.expected.sha256 ({len(files)} files)')

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--output-dir', metavar='DIR', help='write orbital/ crate into this directory')
    ap.add_argument('--emit-manifests', metavar='DIR', help='write tN.expected.sha256 manifests into this directory')
    args = ap.parse_args()
    if not args.output_dir and not args.emit_manifests:
        ap.print_help(); sys.exit(1)
    base_files = _all_files()
    if args.output_dir:
        out = pathlib.Path(args.output_dir)
        for relpath, content in base_files.items():
            p = out / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        print(f'orbital fixture: {len(base_files)} files written to {out}')
    if args.emit_manifests:
        mdir = pathlib.Path(args.emit_manifests)
        print('Computing T1 manifest...'); _write_manifest(mdir, 't1', {k: apply_t1(v) for k, v in base_files.items()})
        print('Computing T2 manifest...'); _write_manifest(mdir, 't2', {k: apply_t2(v) for k, v in base_files.items()})
        print('Computing T3 manifest...'); _write_manifest(mdir, 't3', {k: apply_t3(v) for k, v in base_files.items()})
        print(f'Manifests written to {mdir}')

if __name__ == '__main__':
    main()
