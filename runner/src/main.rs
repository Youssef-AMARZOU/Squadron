use serde_json::{json, Value};
use std::env;
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const DEFAULT_TIMEOUT_MS: u64 = 15_000;
const MIN_TIMEOUT_MS: u64 = 100;
const MAX_TIMEOUT_MS: u64 = 600_000;
const DEFAULT_MAX_OUTPUT: usize = 64 * 1024;
const DEFAULT_MAX_READ: usize = 256 * 1024;

fn main() {
    let root_raw = match env::args().nth(1) {
        Some(value) => value,
        None => {
            emit(&json!({"ok": false, "error": "missing workspace root argument"}));
            return;
        }
    };
    let root = normalize(Path::new(&root_raw));

    let mut input = String::new();
    if let Err(error) = io::stdin().read_to_string(&mut input) {
        emit(&json!({"ok": false, "error": format!("failed to read stdin: {error}")}));
        return;
    }
    let request: Value = match serde_json::from_str(&input) {
        Ok(value) => value,
        Err(error) => {
            emit(&json!({"ok": false, "error": format!("invalid JSON request: {error}")}));
            return;
        }
    };

    let op = request.get("op").and_then(Value::as_str).unwrap_or("");
    let outcome = match op {
        "exec" => op_exec(&root, &request),
        "read" => op_read(&root, &request),
        "write" => op_write(&root, &request),
        "list" => op_list(&root, &request),
        "stat" => op_stat(&root, &request),
        other => Err(format!("unknown op \"{other}\"")),
    };

    match outcome {
        Ok(result) => emit(&json!({"ok": true, "result": result})),
        Err(error) => emit(&json!({"ok": false, "error": error})),
    }
}

fn emit(value: &Value) {
    let mut stdout = io::stdout();
    let _ = writeln!(stdout, "{}", value);
    let _ = stdout.flush();
}

fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn path_key(path: &Path) -> String {
    let text = path.to_string_lossy().to_string();
    if cfg!(windows) {
        text.to_lowercase()
    } else {
        text
    }
}

fn within(root: &Path, target: &Path) -> bool {
    let root_key = path_key(root);
    let target_key = path_key(target);
    if target_key == root_key {
        return true;
    }
    match target_key.strip_prefix(&root_key) {
        Some(rest) => rest.starts_with('\\') || rest.starts_with('/'),
        None => false,
    }
}

fn resolve(root: &Path, raw: &str) -> Result<PathBuf, String> {
    let candidate = Path::new(raw);
    if candidate.is_absolute() || (cfg!(windows) && has_drive_prefix(raw)) {
        let mut out = PathBuf::new();
        for component in normalize(candidate).components() {
            match component {
                Component::CurDir => {}
                Component::ParentDir => {
                    out.pop();
                }
                other => out.push(other.as_os_str()),
            }
        }
        if within(root, &out) {
            return Ok(out);
        }
        return Err("path escapes the workspace root".to_string());
    }

    let mut out = root.to_path_buf();
    let mut depth: usize = 0;
    for component in candidate.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if depth == 0 {
                    return Err("path escapes the workspace root".to_string());
                }
                depth -= 1;
                out.pop();
            }
            Component::Normal(value) => {
                depth += 1;
                out.push(value);
            }
            _ => return Err("unsupported path component".to_string()),
        }
    }
    Ok(out)
}

fn has_drive_prefix(raw: &str) -> bool {
    let bytes = raw.as_bytes();
    bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':'
}

// /S makes cmd strip exactly the outer quotes we add, so arbitrary command
// strings with their own quotes survive intact.
#[cfg(windows)]
fn shell_command(command: &str) -> Command {
    use std::os::windows::process::CommandExt;
    let mut shell = Command::new("cmd");
    shell
        .raw_arg("/S")
        .raw_arg("/C")
        .raw_arg(format!("\"{command}\""));
    shell
}

#[cfg(not(windows))]
fn shell_command(command: &str) -> Command {
    let mut shell = Command::new("sh");
    shell.arg("-c").arg(command);
    shell
}

fn need_str<'a>(request: &'a Value, key: &str) -> Result<&'a str, String> {
    request
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("\"{key}\" must be a string"))
}

fn clip(text: String, max: usize) -> String {
    let bytes = text.as_bytes();
    if bytes.len() <= max {
        return text;
    }
    let half = max / 2;
    let head = String::from_utf8_lossy(&bytes[..half]).into_owned();
    let tail = String::from_utf8_lossy(&bytes[bytes.len() - half..]).into_owned();
    let total = bytes.len();
    let omitted = total - max;
    format!("{head}\n...[{total} bytes total, {omitted} omitted]...\n{tail}")
}

fn op_read(root: &Path, request: &Value) -> Result<Value, String> {
    let path = resolve(root, need_str(request, "path")?)?;
    let max_bytes = request
        .get("max_bytes")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_READ as u64) as usize;
    let metadata = fs::symlink_metadata(&path).map_err(|e| format!("read failed: {e}"))?;
    if metadata.is_dir() {
        return Err(format!("{} is a directory", path.display()));
    }
    let file = fs::File::open(&path).map_err(|e| format!("open failed: {e}"))?;
    let mut taken = file.take(max_bytes as u64 + 1);
    let mut buffer = Vec::new();
    taken
        .read_to_end(&mut buffer)
        .map_err(|e| format!("read failed: {e}"))?;
    let truncated = buffer.len() > max_bytes;
    if truncated {
        buffer.truncate(max_bytes);
    }
    let content = String::from_utf8_lossy(&buffer).into_owned();
    Ok(json!({
        "path": path.display().to_string(),
        "bytes": metadata.len(),
        "truncated": truncated,
        "content": clip(content, max_bytes),
    }))
}

fn op_write(root: &Path, request: &Value) -> Result<Value, String> {
    let path = resolve(root, need_str(request, "path")?)?;
    let content = need_str(request, "content")?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create directories failed: {e}"))?;
    }
    fs::write(&path, content).map_err(|e| format!("write failed: {e}"))?;
    Ok(json!({
        "path": path.display().to_string(),
        "bytes": content.len(),
    }))
}

fn entry_kind(file_type: &fs::FileType) -> &'static str {
    if file_type.is_dir() {
        "dir"
    } else if file_type.is_file() {
        "file"
    } else if file_type.is_symlink() {
        "symlink"
    } else {
        "other"
    }
}

fn op_list(root: &Path, request: &Value) -> Result<Value, String> {
    let raw = request.get("path").and_then(Value::as_str).unwrap_or(".");
    let path = resolve(root, raw)?;
    let read_dir = fs::read_dir(&path).map_err(|e| format!("list failed: {e}"))?;
    let mut entries = Vec::new();
    for entry in read_dir {
        let entry = entry.map_err(|e| format!("list failed: {e}"))?;
        let file_type = entry.file_type().map_err(|e| format!("list failed: {e}"))?;
        let name = entry.file_name().to_string_lossy().to_string();
        let size = if file_type.is_file() {
            json!(entry.metadata().map(|m| m.len()).unwrap_or(0))
        } else {
            json!(null)
        };
        entries.push(json!({
            "name": name,
            "kind": entry_kind(&file_type),
            "size": size,
        }));
    }
    entries.sort_by_key(|entry| {
        let kind = entry["kind"].as_str().unwrap_or("").to_string();
        let name = entry["name"].as_str().unwrap_or("").to_string();
        (kind, name)
    });
    Ok(json!({
        "path": path.display().to_string(),
        "entries": entries,
    }))
}

fn op_stat(root: &Path, request: &Value) -> Result<Value, String> {
    let path = resolve(root, need_str(request, "path")?)?;
    match fs::symlink_metadata(&path) {
        Ok(metadata) => Ok(json!({
            "path": path.display().to_string(),
            "exists": true,
            "kind": entry_kind(&metadata.file_type()),
            "size": metadata.len(),
        })),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(json!({
            "path": path.display().to_string(),
            "exists": false,
        })),
        Err(error) => Err(format!("stat failed: {error}")),
    }
}

fn op_exec(root: &Path, request: &Value) -> Result<Value, String> {
    let command = need_str(request, "command")?;
    let cwd_raw = request.get("cwd").and_then(Value::as_str).unwrap_or(".");
    let cwd = resolve(root, cwd_raw)?;
    if !cwd.is_dir() {
        return Err(format!("cwd does not exist: {}", cwd.display()));
    }

    let timeout_ms = request
        .get("timeout_ms")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_TIMEOUT_MS)
        .clamp(MIN_TIMEOUT_MS, MAX_TIMEOUT_MS);
    let max_output = request
        .get("max_output")
        .and_then(Value::as_u64)
        .unwrap_or(DEFAULT_MAX_OUTPUT as u64) as usize;

    let mut shell = shell_command(command);

    let unique = format!("sqn-{}-{}", std::process::id(), Instant::now().elapsed().as_nanos());
    let out_path = env::temp_dir().join(format!("{unique}-out.txt"));
    let err_path = env::temp_dir().join(format!("{unique}-err.txt"));
    let out_file = fs::File::create(&out_path).map_err(|e| format!("temp file failed: {e}"))?;
    let err_file = fs::File::create(&err_path).map_err(|e| format!("temp file failed: {e}"))?;

    shell
        .current_dir(&cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::from(out_file))
        .stderr(Stdio::from(err_file));

    let started = Instant::now();
    let mut child = shell.spawn().map_err(|e| format!("spawn failed: {e}"))?;

    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {
                if started.elapsed() >= Duration::from_millis(timeout_ms) {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(Duration::from_millis(15));
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("wait failed: {error}"));
            }
        }
    };

    let stdout = String::from_utf8_lossy(&fs::read(&out_path).unwrap_or_default()).into_owned();
    let stderr = String::from_utf8_lossy(&fs::read(&err_path).unwrap_or_default()).into_owned();
    let _ = fs::remove_file(&out_path);
    let _ = fs::remove_file(&err_path);

    let timed_out = status.is_none();
    let exit_code = status.and_then(|s| s.code());
    Ok(json!({
        "command": command,
        "cwd": cwd.display().to_string(),
        "exit": exit_code,
        "timed_out": timed_out,
        "stdout": clip(stdout, max_output),
        "stderr": clip(stderr, max_output),
        "duration_ms": started.elapsed().as_millis() as u64,
    }))
}
