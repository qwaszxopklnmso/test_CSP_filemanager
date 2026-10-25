import csv
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.wsgi import ClosingIterator

import config

os.makedirs(config.DATA_DIR, exist_ok=True)
os.makedirs(config.PROBLEMS_DIR, exist_ok=True)
os.makedirs(config.SUBMISSIONS_DIR, exist_ok=True)


def load_secret_key():
    if os.path.exists(config.SECRET_KEY_PATH):
        with open(config.SECRET_KEY_PATH, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    with open(config.SECRET_KEY_PATH, "w", encoding="utf-8") as fh:
        fh.write(key)
    try:
        os.chmod(config.SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(config.DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    problem_code TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    ip TEXT,
    UNIQUE(student_id, problem_code),
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "contest_start": "",
    "contest_end": "",
    "submissions_enabled": "1",
    "login_enabled": "1",
    "data_zip_name": "",
    "data_zip_updated": "",
}


def init_db():
    db = sqlite3.connect(config.DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    now = now_str()

    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    for code in config.PROBLEM_CODES:
        db.execute(
            "INSERT OR IGNORE INTO problems (code, title, updated_at) VALUES (?, ?, ?)",
            (code, config.DEFAULT_PROBLEM_TITLES.get(code, code), now),
        )

    if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
            (config.DEFAULT_ADMIN_USERNAME, generate_password_hash(config.DEFAULT_ADMIN_PASSWORD)),
        )

    seeded = db.execute("SELECT value FROM settings WHERE key='demo_seeded'").fetchone()
    if seeded is None:
        if db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
            for exam_id, password, name in config.DEMO_STUDENTS:
                db.execute(
                    "INSERT INTO students (exam_id, name, password_hash, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
                    (exam_id, name, generate_password_hash(password), now),
                )
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('demo_seeded', '1')")

    db.commit()
    db.close()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def submission_status(settings=None):
    """Return (is_open, state, message). state: running/not_started/ended/disabled."""
    settings = settings or get_settings()
    now = datetime.now()
    if settings.get("submissions_enabled") != "1":
        return False, "disabled", "提交通道已关闭"

    start = parse_dt(settings.get("contest_start"))
    end = parse_dt(settings.get("contest_end"))

    if start and now < start:
        return False, "not_started", "比赛尚未开始，提交暂未开放"
    if end and now > end:
        return False, "ended", "比赛已结束，提交已锁定"
    if end:
        return True, "running", "提交进行中，截止时间：" + end.strftime("%Y-%m-%d %H:%M")
    return True, "running", "提交进行中（未设置截止时间）"


def get_csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_hex(32)
        session["_csrf"] = token
    return token


@app.context_processor
def inject_globals():
    return {
        "csrf_token": get_csrf_token(),
        "problem_codes": config.PROBLEM_CODES,
        "exam_id_hint": config.EXAM_ID_HINT,
    }


@app.template_filter("fmt_dt")
def fmt_dt(value):
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


@app.template_filter("fmt_size")
def fmt_size(value):
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "—"
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / 1024 / 1024)
    if size >= 1024:
        return "%.1f KB" % (size / 1024)
    return "%d B" % size


_login_failures = {}
_login_lock = threading.Lock()
LOGIN_LIMIT = 10
LOGIN_WINDOW = 60
_last_login_sweep = 0.0


def _sweep_login_failures(now):
    """丢弃整条过期的失败记录，防止字典按伪造 IP 无限增长。"""
    global _last_login_sweep
    if now - _last_login_sweep < LOGIN_WINDOW:
        return
    _last_login_sweep = now
    for key in list(_login_failures):
        fresh = [t for t in _login_failures[key] if now - t < LOGIN_WINDOW]
        if fresh:
            _login_failures[key] = fresh
        else:
            del _login_failures[key]


def login_blocked(key):
    now = time.time()
    with _login_lock:
        _sweep_login_failures(now)
        attempts = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW]
        _login_failures[key] = attempts
        return len(attempts) >= LOGIN_LIMIT


def record_login_failure(key):
    with _login_lock:
        _login_failures.setdefault(key, []).append(time.time())


def clear_login_failures(key):
    with _login_lock:
        _login_failures.pop(key, None)


def client_ip():
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        expected = session.get("_csrf")
        if not token or not expected or not secrets.compare_digest(token, expected):
            abort(400, description="CSRF 校验失败，请刷新页面后重试")


def current_student():
    sid = session.get("student_id")
    if not sid:
        return None
    row = get_db().execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if row is None or not row["enabled"]:
        session.pop("student_id", None)
        session.pop("student_exam_id", None)
        return None
    return row


def student_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_student() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def safe_next_target(target):
    """只允许站内相对路径，防止开放重定向。"""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    return target


def current_admin():
    aid = session.get("admin_id")
    if not aid:
        return None
    return get_db().execute("SELECT * FROM admins WHERE id=?", (aid,)).fetchone()


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_admin() is None:
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def valid_exam_id(exam_id):
    return bool(re.fullmatch(config.EXAM_ID_PATTERN, exam_id or ""))


def normalize_exam_id(raw):
    return (raw or "").strip().upper()


def generate_password():
    return str(secrets.randbelow(900000) + 100000)


def submission_path(exam_id, stored_name):
    return os.path.join(config.SUBMISSIONS_DIR, exam_id, stored_name)


def problem_title_for(code):
    row = get_db().execute("SELECT title FROM problems WHERE code=?", (code,)).fetchone()
    return row["title"] if row else code


def write_atomic(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".tmp_" + secrets.token_hex(8))
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def data_zip_path():
    return os.path.join(config.PROBLEMS_DIR, config.DATA_ZIP_FILENAME)


def inspect_data_package(path):
    """Analyze a CSP-style data zip without extracting it."""
    info = {"root": "", "pdfs": [], "folders": [], "warnings": [], "file_count": 0}
    try:
        with zipfile.ZipFile(path) as zf:
            names = []
            for item in zf.infolist():
                if item.is_dir():
                    continue
                name = item.filename.replace("\\", "/").lstrip("/")
                if not name:
                    continue
                parts = name.split("/")
                if parts[0] == "__MACOSX" or parts[-1].startswith("."):
                    continue
                names.append(name)
    except (zipfile.BadZipFile, OSError):
        info["warnings"].append("压缩包无法读取或已损坏。")
        return info

    if not names:
        info["warnings"].append("压缩包内没有文件。")
        return info

    tops = {name.split("/")[0] for name in names}
    if len(tops) == 1 and all("/" in name for name in names):
        info["root"] = next(iter(tops))
        prefix = info["root"] + "/"
        rel = [name[len(prefix):] for name in names]
    else:
        rel = names
        if len(tops) > 1:
            info["warnings"].append("压缩包根目录不是单一文件夹。")

    info["file_count"] = len(rel)
    info["pdfs"] = sorted(
        name for name in rel if "/" not in name and name.lower().endswith(".pdf")
    )

    folder_files = {}
    for name in rel:
        parts = name.split("/")
        if len(parts) >= 2 and parts[0]:
            folder_files.setdefault(parts[0], []).append("/".join(parts[1:]))

    folders = []
    for folder, files in sorted(folder_files.items()):
        flat = [name for name in files if "/" not in name]
        ins = sorted(name for name in flat if name.lower().endswith(".in"))
        ans = sorted(name for name in flat if name.lower().endswith(".ans"))
        in_stems = {os.path.splitext(name)[0] for name in ins}
        ans_stems = {os.path.splitext(name)[0] for name in ans}
        folders.append(
            {
                "name": folder,
                "ins": len(ins),
                "ans": len(ans),
                "cases": len(in_stems & ans_stems),
                "missing_ans": sorted(in_stems - ans_stems),
                "missing_in": sorted(ans_stems - in_stems),
            }
        )
    info["folders"] = folders

    if not info["pdfs"]:
        info["warnings"].append("未在根目录找到题目 PDF 文件。")
    if len(folders) != len(config.PROBLEM_CODES):
        info["warnings"].append(
            "检测到 %d 个题目文件夹，正常应为 %d 个。" % (len(folders), len(config.PROBLEM_CODES))
        )
    for folder in folders:
        if folder["ins"] == 0 or folder["ans"] == 0:
            info["warnings"].append("题目文件夹「%s」缺少 .in 或 .ans 数据。" % folder["name"])
        if folder["missing_ans"]:
            sample = "、".join(folder["missing_ans"][:5])
            if len(folder["missing_ans"]) > 5:
                sample += " 等"
            info["warnings"].append("「%s」缺少答案文件：%s" % (folder["name"], sample))
        if folder["missing_in"]:
            sample = "、".join(folder["missing_in"][:5])
            if len(folder["missing_in"]) > 5:
                sample += " 等"
            info["warnings"].append("「%s」缺少输入文件：%s" % (folder["name"], sample))
    return info


_zip_info_cache = {"key": None, "info": None, "lock": threading.Lock()}


def get_data_package():
    path = data_zip_path()
    if not os.path.exists(path):
        return None
    try:
        stat = os.stat(path)
        cache_key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = None
    settings = get_settings()
    with _zip_info_cache["lock"]:
        if cache_key is not None and _zip_info_cache["key"] == cache_key:
            info = _zip_info_cache["info"]
        else:
            info = inspect_data_package(path)
            if cache_key is not None:
                _zip_info_cache["key"] = cache_key
                _zip_info_cache["info"] = info
    return {
        "name": settings.get("data_zip_name") or config.DATA_ZIP_FILENAME,
        "updated": settings.get("data_zip_updated") or "",
        "size": os.path.getsize(path),
        "info": info,
    }


CPP_KEYWORDS = frozenset(
    """
    alignas alignof and and_eq asm auto bitand bitor bool break case catch char
    char8_t char16_t char32_t class compl concept const consteval constexpr
    constinit const_cast continue co_await co_return co_yield decltype default
    delete do double dynamic_cast else enum explicit export extern false float
    for friend goto if inline int long mutable namespace new noexcept not not_eq
    nullptr operator or or_eq private protected public register
    reinterpret_cast requires return short signed sizeof static static_assert
    static_cast struct switch template this thread_local throw true try typedef
    typeid typename union unsigned using virtual void volatile wchar_t while
    xor xor_eq
    """.split()
)

CPP_TYPES = frozenset(
    """
    size_t ssize_t ptrdiff_t int8_t int16_t int32_t int64_t uint8_t uint16_t
    uint32_t uint64_t string wstring vector map multimap set multiset
    unordered_map unordered_set queue priority_queue stack deque pair tuple
    array list bitset istream ostream stringstream fstream ifstream ofstream
    cin cout cerr clog endl
    """.split()
)


def escape_html_text(value):
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _tok(cls, text):
    return '<span class="tok-%s">%s</span>' % (cls, escape_html_text(text))


NUMBER_RE = re.compile(
    r"(?:0[xX][0-9a-fA-F']+|0[bB][01']+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:[uUlLfF]*)"
)


def highlight_cpp_line(line, in_block_comment):
    """Return (html, still_in_block_comment) for one line of C++ source."""
    if not in_block_comment and line.lstrip().startswith("#"):
        return _tok("pre", line), False

    out = []
    i = 0
    length = len(line)

    if in_block_comment:
        end = line.find("*/")
        if end == -1:
            return _tok("com", line), True
        out.append(_tok("com", line[: end + 2]))
        i = end + 2
        in_block_comment = False

    while i < length:
        if line.startswith("//", i):
            out.append(_tok("com", line[i:]))
            break

        if line.startswith("/*", i):
            end = line.find("*/", i + 2)
            if end == -1:
                out.append(_tok("com", line[i:]))
                in_block_comment = True
                break
            out.append(_tok("com", line[i : end + 2]))
            i = end + 2
            continue

        ch = line[i]
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            while j < length:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(_tok("str", line[i:j]))
            i = j
            continue

        if ch.isdigit():
            match = NUMBER_RE.match(line, i)
            if match:
                out.append(_tok("num", match.group(0)))
                i = match.end()
                continue

        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < length and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            k = j
            while k < length and line[k] in " \t":
                k += 1
            if word in CPP_KEYWORDS:
                out.append(_tok("kw", word))
            elif word in CPP_TYPES:
                out.append(_tok("type", word))
            elif k < length and line[k] == "(":
                out.append(_tok("fn", word))
            else:
                out.append(escape_html_text(word))
            i = j
            continue

        out.append(escape_html_text(ch))
        i += 1

    return "".join(out), in_block_comment


def load_source_preview(path):
    with open(path, "rb") as fh:
        data = fh.read(config.PREVIEW_MAX_BYTES + 1)
    truncated = len(data) > config.PREVIEW_MAX_BYTES
    if truncated:
        data = data[: config.PREVIEW_MAX_BYTES]

    text = None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if text is None and truncated:
        # 截断可能切在 UTF-8 多字节字符中间：退掉末尾几个字节再试，
        # 避免误判成 GBK 而整页乱码
        for cut in range(1, 4):
            try:
                text = data[: len(data) - cut].decode("utf-8-sig")
                break
            except UnicodeDecodeError:
                continue
    if text is None:
        try:
            text = data.decode("gb18030")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if text.endswith("\n") and lines and lines[-1] == "":
        lines.pop()

    state = False
    highlighted = []
    for line in lines:
        html, state = highlight_cpp_line(line, state)
        highlighted.append(html)

    return {
        "lines": highlighted,
        "code_text": text,
        "truncated": truncated,
        "line_count": len(lines),
        "byte_size": len(data),
    }


# ---------------------------------------------------------------------------
# Student routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if current_student() is not None:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_student() is not None:
        return redirect(url_for("dashboard"))

    next_target = safe_next_target(request.values.get("next"))

    if request.method == "POST":
        settings = get_settings()
        if settings.get("login_enabled") != "1":
            flash("登录通道已关闭，请联系监考老师。", "error")
            return render_template("login.html", next_target=next_target), 403

        key = "student:" + client_ip()
        if login_blocked(key):
            flash("尝试次数过多，请 1 分钟后再试。", "error")
            return render_template("login.html", next_target=next_target), 429

        exam_id = normalize_exam_id(request.form.get("exam_id"))
        password = request.form.get("password") or ""
        if not exam_id or not password:
            flash("请输入考号与密码。", "error")
            return render_template("login.html", next_target=next_target), 400

        if exam_id == config.DEFAULT_ADMIN_USERNAME.upper():
            flash("管理员账号请从右上角「管理员入口」登录。", "error")
            return render_template("login.html", next_target=next_target), 400

        student = get_db().execute("SELECT * FROM students WHERE exam_id=?", (exam_id,)).fetchone()
        if student is None or not check_password_hash(student["password_hash"], password):
            record_login_failure(key)
            flash("考号或密码错误。", "error")
            return render_template("login.html", next_target=next_target), 401
        if not student["enabled"]:
            flash("该账号已被禁用，请联系监考老师。", "error")
            return render_template("login.html", next_target=next_target), 403

        clear_login_failures(key)
        session.clear()
        session["student_id"] = student["id"]
        session["student_exam_id"] = student["exam_id"]
        flash("登录成功，欢迎 %s。" % student["exam_id"], "success")
        return redirect(next_target or url_for("dashboard"))

    return render_template("login.html", next_target=next_target)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("已退出登录。", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@student_required
def dashboard():
    student = current_student()
    settings = get_settings()
    is_open, state, message = submission_status(settings)
    db = get_db()
    problems = db.execute("SELECT * FROM problems ORDER BY code").fetchall()
    subs = {
        row["problem_code"]: row
        for row in db.execute("SELECT * FROM submissions WHERE student_id=?", (student["id"],)).fetchall()
    }
    rows = []
    for problem in problems:
        rows.append(
            {
                "problem": problem,
                "submission": subs.get(problem["code"]),
            }
        )
    submitted_count = len(subs)
    return render_template(
        "dashboard.html",
        student=student,
        rows=rows,
        is_open=is_open,
        state=state,
        message=message,
        settings=settings,
        data_package=get_data_package(),
        submitted_count=submitted_count,
        total_count=len(rows),
        now=now_str(),
    )


@app.route("/submit/<code>", methods=["POST"])
@student_required
def submit_file(code):
    student = current_student()
    code = (code or "").upper()
    if code not in config.PROBLEM_CODES:
        abort(404)

    is_open, _state, message = submission_status()
    if not is_open:
        flash(message + "，无法提交。", "error")
        return redirect(url_for("dashboard"))

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("请选择要提交的 .cpp 文件。", "error")
        return redirect(url_for("dashboard"))

    original_name = os.path.basename(upload.filename).strip()
    if not original_name.lower().endswith(".cpp"):
        flash("只允许提交 .cpp 源文件。", "error")
        return redirect(url_for("dashboard"))

    data = upload.read(config.MAX_CPP_SIZE + 1)
    if not data:
        flash("不能提交空文件。", "error")
        return redirect(url_for("dashboard"))
    if len(data) > config.MAX_CPP_SIZE:
        flash("文件超过大小上限（%d MB）。" % (config.MAX_CPP_SIZE // 1024 // 1024), "error")
        return redirect(url_for("dashboard"))

    exam_id = student["exam_id"]
    stored_name = "%s_%s.cpp" % (exam_id, code)
    target = submission_path(exam_id, stored_name)
    write_atomic(target, data)

    digest = hashlib.sha256(data).hexdigest()
    submitted_at = now_str()
    db = get_db()
    db.execute(
        """
        INSERT INTO submissions
            (student_id, problem_code, original_name, stored_name, size, sha256, submitted_at, ip)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, problem_code) DO UPDATE SET
            original_name=excluded.original_name,
            stored_name=excluded.stored_name,
            size=excluded.size,
            sha256=excluded.sha256,
            submitted_at=excluded.submitted_at,
            ip=excluded.ip
        """,
        (student["id"], code, original_name, stored_name, len(data), digest, submitted_at, client_ip()),
    )
    db.commit()
    flash("「%s」提交成功：%s（%s）" % (problem_title_for(code), original_name, submitted_at), "success")
    return redirect(url_for("dashboard"))


_data_download_last = {}
_data_download_lock = threading.Lock()


def data_download_allowed(student_id):
    now = time.time()
    with _data_download_lock:
        last = _data_download_last.get(student_id, 0)
        if now - last < config.DATA_DOWNLOAD_COOLDOWN:
            return False
        _data_download_last[student_id] = now
        return True


@app.route("/data/download")
@student_required
def download_data_package():
    student = current_student()
    package = get_data_package()
    if package is None:
        flash("题目数据压缩包尚未上传，请联系监考老师。", "error")
        return redirect(url_for("dashboard"))
    if not data_download_allowed(student["id"]):
        flash("下载太频繁，请 %d 秒后再试。" % config.DATA_DOWNLOAD_COOLDOWN, "error")
        return redirect(url_for("dashboard"))
    return send_file(
        data_zip_path(),
        as_attachment=True,
        download_name=package["name"],
        mimetype="application/zip",
    )


@app.route("/submission/<code>/download")
@student_required
def download_own_submission(code):
    student = current_student()
    code = (code or "").upper()
    if code not in config.PROBLEM_CODES:
        abort(404)
    row = get_db().execute(
        "SELECT * FROM submissions WHERE student_id=? AND problem_code=?", (student["id"], code)
    ).fetchone()
    if row is None:
        abort(404)
    path = submission_path(student["exam_id"], row["stored_name"])
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=row["stored_name"])


@app.route("/submission/<code>/preview")
@student_required
def preview_own_submission(code):
    student = current_student()
    code = (code or "").upper()
    if code not in config.PROBLEM_CODES:
        abort(404)
    row = get_db().execute(
        "SELECT * FROM submissions WHERE student_id=? AND problem_code=?", (student["id"], code)
    ).fetchone()
    if row is None:
        abort(404)
    path = submission_path(student["exam_id"], row["stored_name"])
    if not os.path.exists(path):
        abort(404)
    preview = load_source_preview(path)
    return render_template(
        "preview.html",
        problem_title=problem_title_for(code),
        filename=row["stored_name"],
        subtitle="考号 %s · 提交于 %s" % (student["exam_id"], row["submitted_at"][:16]),
        back_url=url_for("dashboard"),
        download_url=url_for("download_own_submission", code=code),
        **preview,
    )


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if current_admin() is not None:
        return redirect(url_for("admin_dashboard"))

    next_target = safe_next_target(request.values.get("next"))

    if request.method == "POST":
        key = "admin:" + client_ip()
        if login_blocked(key):
            flash("尝试次数过多，请 1 分钟后再试。", "error")
            return render_template("admin_login.html", next_target=next_target), 429

        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            flash("请输入管理员账号与密码。", "error")
            return render_template("admin_login.html", next_target=next_target), 400

        admin = get_db().execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        if admin is None or not check_password_hash(admin["password_hash"], password):
            record_login_failure(key)
            flash("管理员账号或密码错误。", "error")
            return render_template("admin_login.html", next_target=next_target), 401

        clear_login_failures(key)
        session.clear()
        session["admin_id"] = admin["id"]
        session["admin_username"] = admin["username"]
        flash("管理员登录成功。", "success")
        return redirect(next_target or url_for("admin_dashboard"))

    return render_template("admin_login.html", next_target=next_target)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    flash("管理员已退出登录。", "success")
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# Admin pages
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    settings = get_settings()
    is_open, state, message = submission_status(settings)
    total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    enabled_students = db.execute("SELECT COUNT(*) FROM students WHERE enabled=1").fetchone()[0]
    total_submissions = db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    per_problem = {
        row["problem_code"]: row["c"]
        for row in db.execute(
            "SELECT problem_code, COUNT(*) AS c FROM submissions GROUP BY problem_code"
        ).fetchall()
    }
    problems = db.execute("SELECT * FROM problems ORDER BY code").fetchall()
    problem_stats = []
    for problem in problems:
        submitted = per_problem.get(problem["code"], 0)
        problem_stats.append(
            {
                "code": problem["code"],
                "title": problem["title"],
                "submitted": submitted,
                "missing": max(enabled_students - submitted, 0),
            }
        )
    return render_template(
        "admin_dashboard.html",
        total_students=total_students,
        enabled_students=enabled_students,
        total_submissions=total_submissions,
        problem_stats=problem_stats,
        data_package=get_data_package(),
        is_open=is_open,
        state=state,
        message=message,
        settings=settings,
        admin=current_admin(),
    )


def parse_import_text(text):
    entries = []
    errors = []
    seen = set()
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        cells = next(csv.reader([line]), [])
        cells = [c.strip() for c in cells]
        if len(cells) < 2:
            cells = re.split(r"\s+", line)
        if not cells:
            continue
        exam_id = normalize_exam_id(cells[0])
        if exam_id.lower() in ("exam_id", "examid", "考号"):
            continue
        password = cells[1] if len(cells) > 1 else ""
        name = " ".join(cells[2:]).strip() if len(cells) > 2 else ""
        if not valid_exam_id(exam_id):
            errors.append("第 %d 行：考号格式不正确「%s」" % (line_no, cells[0]))
            continue
        if len(name) > config.MAX_NAME_LENGTH:
            errors.append("第 %d 行：姓名超过 %d 个字符「%s」" % (line_no, config.MAX_NAME_LENGTH, exam_id))
            continue
        if len(password) < config.MIN_PASSWORD_LENGTH:
            errors.append("第 %d 行：密码至少 %d 位" % (line_no, config.MIN_PASSWORD_LENGTH))
            continue
        if exam_id in seen:
            errors.append("第 %d 行：文件内考号重复「%s」" % (line_no, exam_id))
            continue
        seen.add(exam_id)
        entries.append((exam_id, password, name))
    return entries, errors


@app.route("/admin/students", methods=["GET", "POST"])
@admin_required
def admin_students():
    db = get_db()
    if request.method == "POST":
        exam_id = normalize_exam_id(request.form.get("exam_id"))
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        generated = False
        if not valid_exam_id(exam_id):
            flash("考号格式不正确，应为 %s 形式。" % config.EXAM_ID_HINT, "error")
            return redirect(url_for("admin_students"))
        if len(name) > config.MAX_NAME_LENGTH:
            flash("姓名不能超过 %d 个字符。" % config.MAX_NAME_LENGTH, "error")
            return redirect(url_for("admin_students"))
        if not password:
            password = generate_password()
            generated = True
        if len(password) < config.MIN_PASSWORD_LENGTH:
            flash("密码至少 %d 位。" % config.MIN_PASSWORD_LENGTH, "error")
            return redirect(url_for("admin_students"))
        try:
            db.execute(
                "INSERT INTO students (exam_id, name, password_hash, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
                (exam_id, name, generate_password_hash(password), now_str()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("考号 %s 已存在。" % exam_id, "error")
            return redirect(url_for("admin_students"))
        if generated:
            flash("已新增 %s，自动生成密码：%s（请立即告知选手）" % (exam_id, password), "success")
        else:
            flash("已新增考号 %s。" % exam_id, "success")
        return redirect(url_for("admin_students"))

    students = db.execute("SELECT * FROM students ORDER BY exam_id").fetchall()
    subs = db.execute("SELECT student_id, problem_code FROM submissions").fetchall()
    submitted_map = {}
    for row in subs:
        submitted_map.setdefault(row["student_id"], set()).add(row["problem_code"])
    rows = []
    for student in students:
        rows.append({"student": student, "submitted": submitted_map.get(student["id"], set())})
    problems = db.execute("SELECT code, title FROM problems ORDER BY code").fetchall()
    return render_template(
        "admin_students.html", rows=rows, problems=problems, admin=current_admin()
    )


@app.route("/admin/students/import", methods=["POST"])
@admin_required
def admin_students_import():
    upload = request.files.get("file")
    text = request.form.get("text") or ""
    if upload is not None and upload.filename:
        raw = upload.read()
        text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        flash("请上传 CSV 文件或粘贴导入内容。", "error")
        return redirect(url_for("admin_students"))

    entries, errors = parse_import_text(text)
    db = get_db()
    existing = {row["exam_id"] for row in db.execute("SELECT exam_id FROM students").fetchall()}
    created = 0
    for exam_id, password, name in entries:
        if exam_id in existing:
            errors.append("考号已存在：%s" % exam_id)
            continue
        db.execute(
            "INSERT INTO students (exam_id, name, password_hash, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
            (exam_id, name, generate_password_hash(password), now_str()),
        )
        existing.add(exam_id)
        created += 1
    db.commit()

    flash("导入完成：成功 %d 条，失败 %d 条。" % (created, len(errors)), "success" if created else "error")
    for message in errors[:10]:
        flash(message, "error")
    if len(errors) > 10:
        flash("其余 %d 条错误未显示。" % (len(errors) - 10), "error")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/generate", methods=["POST"])
@admin_required
def admin_students_generate():
    template = (request.form.get("template") or "").strip().upper()
    if not template:
        flash("请输入考号模板。", "error")
        return redirect(url_for("admin_students"))
    if len(template) > 40 or not re.fullmatch(r"[A-Z0-9_-]*%+[A-Z0-9_-]*", template):
        flash("考号模板只能包含字母、数字、-、_ 和 % 占位符。", "error")
        return redirect(url_for("admin_students"))
    runs = re.findall(r"%+", template)
    if len(runs) != 1:
        flash("考号模板必须包含且只包含一段连续的 % 占位符。", "error")
        return redirect(url_for("admin_students"))
    width = len(runs[0])
    if width > 8:
        flash("考号模板的 % 占位符最多 8 位。", "error")
        return redirect(url_for("admin_students"))
    prefix, suffix = template.split(runs[0], 1)

    try:
        start = int(request.form.get("start", ""))
        end = int(request.form.get("end", ""))
        step = int(request.form.get("step") or "1")
    except (TypeError, ValueError):
        flash("起始编号、结束编号和步长必须是整数。", "error")
        return redirect(url_for("admin_students"))
    if start < 0 or end < start or step < 1:
        flash("范围需满足 0 ≤ 起始编号 ≤ 结束编号，步长 ≥ 1。", "error")
        return redirect(url_for("admin_students"))
    if len(str(end)) > width:
        flash(f"结束编号 {end} 超出模板的 {width} 位编号宽度。", "error")
        return redirect(url_for("admin_students"))
    count = (end - start) // step + 1
    if count > 2000:
        flash("一次最多生成 2000 个考号。", "error")
        return redirect(url_for("admin_students"))

    password = request.form.get("password") or ""
    auto_password = not password
    if auto_password:
        password = generate_password()
    elif len(password) < config.MIN_PASSWORD_LENGTH:
        flash(f"统一初始密码至少 {config.MIN_PASSWORD_LENGTH} 位。", "error")
        return redirect(url_for("admin_students"))

    ids = []
    for number in range(start, end + 1, step):
        exam_id = f"{prefix}{number:0{width}d}{suffix}"
        if exam_id == config.DEFAULT_ADMIN_USERNAME.upper():
            flash("考号模板不能生成与管理员账号相同的考号。", "error")
            return redirect(url_for("admin_students"))
        if len(exam_id) > 32 or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,31}", exam_id):
            flash(f"生成的考号格式不安全或过长：{exam_id}", "error")
            return redirect(url_for("admin_students"))
        ids.append(exam_id)

    db = get_db()
    existing = {row["exam_id"] for row in db.execute("SELECT exam_id FROM students").fetchall()}
    created = []
    skipped = 0
    password_hash = generate_password_hash(password)
    created_at = now_str()
    for exam_id in ids:
        if exam_id in existing:
            skipped += 1
            continue
        db.execute(
            "INSERT INTO students (exam_id, name, password_hash, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
            (exam_id, "", password_hash, created_at),
        )
        existing.add(exam_id)
        created.append(exam_id)
    db.commit()

    if created:
        flash(f"批量生成完成：新增 {len(created)} 个考号，跳过 {skipped} 个已存在。", "success")
        flash(f"生成范围：{created[0]} ~ {created[-1]}。", "success")
        if auto_password:
            flash(f"统一初始密码：{password}（请立即告知选手）。", "success")
        else:
            flash("已使用你填写的统一初始密码。", "success")
    else:
        flash("没有新增考号：生成的考号都已存在。", "error")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/<int:sid>/password", methods=["POST"])
@admin_required
def admin_reset_password(sid):
    password = request.form.get("password") or ""
    if len(password) < config.MIN_PASSWORD_LENGTH:
        flash("密码至少 %d 位。" % config.MIN_PASSWORD_LENGTH, "error")
        return redirect(url_for("admin_students"))
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if student is None:
        abort(404)
    db.execute("UPDATE students SET password_hash=? WHERE id=?", (generate_password_hash(password), sid))
    db.commit()
    flash("已重置 %s 的密码。" % student["exam_id"], "success")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/<int:sid>/toggle", methods=["POST"])
@admin_required
def admin_toggle_student(sid):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if student is None:
        abort(404)
    new_state = 0 if student["enabled"] else 1
    db.execute("UPDATE students SET enabled=? WHERE id=?", (new_state, sid))
    db.commit()
    flash("%s 已%s。" % (student["exam_id"], "启用" if new_state else "禁用"), "success")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_delete_student(sid):
    db = get_db()
    student = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
    if student is None:
        abort(404)
    db.execute("DELETE FROM submissions WHERE student_id=?", (sid,))
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    db.commit()
    folder = os.path.join(config.SUBMISSIONS_DIR, student["exam_id"])
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    flash("已删除考生 %s 及其提交文件。" % student["exam_id"], "success")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/delete-batch", methods=["POST"])
@admin_required
def admin_delete_students_batch():
    ids = []
    for raw in request.form.getlist("student_ids"):
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid > 0:
            ids.append(sid)
    ids = sorted(set(ids))
    if not ids:
        flash("请先勾选要删除的考生。", "error")
        return redirect(url_for("admin_students"))

    db = get_db()
    deleted = []
    for sid in ids:
        student = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
        if student is None:
            continue
        db.execute("DELETE FROM submissions WHERE student_id=?", (sid,))
        db.execute("DELETE FROM students WHERE id=?", (sid,))
        deleted.append(student["exam_id"])
    db.commit()

    for exam_id in deleted:
        folder = os.path.join(config.SUBMISSIONS_DIR, exam_id)
        if os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)

    if deleted:
        shown = "、".join(deleted[:10])
        if len(deleted) > 10:
            shown += " 等 %d 人" % len(deleted)
        flash("已删除 %d 名考生及其提交文件：%s。" % (len(deleted), shown), "success")
    else:
        flash("没有可删除的考生。", "error")
    return redirect(url_for("admin_students"))


@app.route("/admin/problems")
@admin_required
def admin_problems():
    rows = get_db().execute("SELECT * FROM problems ORDER BY code").fetchall()
    return render_template(
        "admin_problems.html",
        rows=rows,
        data_package=get_data_package(),
        admin=current_admin(),
    )


@app.route("/admin/problems/<code>/title", methods=["POST"])
@admin_required
def admin_problem_title(code):
    code = (code or "").upper()
    if code not in config.PROBLEM_CODES:
        abort(404)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("题目名称不能为空。", "error")
        return redirect(url_for("admin_problems"))
    if len(title) > config.MAX_PROBLEM_TITLE_LENGTH:
        flash("题目名称不能超过 %d 个字符。" % config.MAX_PROBLEM_TITLE_LENGTH, "error")
        return redirect(url_for("admin_problems"))
    get_db().execute("UPDATE problems SET title=?, updated_at=? WHERE code=?", (title, now_str(), code))
    get_db().commit()
    flash("题目名称已更新为「%s」。" % title, "success")
    return redirect(url_for("admin_problems"))


@app.route("/admin/problems/data/zip", methods=["POST"])
@admin_required
def admin_data_zip_upload():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        flash("请选择题目数据压缩包。", "error")
        return redirect(url_for("admin_problems"))
    original_name = os.path.basename(upload.filename).strip()
    if not original_name.lower().endswith(".zip"):
        flash("题目数据包必须是 .zip 文件。", "error")
        return redirect(url_for("admin_problems"))

    tmp = os.path.join(config.PROBLEMS_DIR, ".upload_" + secrets.token_hex(8))
    try:
        upload.save(tmp)
        size = os.path.getsize(tmp)
        if size == 0:
            flash("压缩包为空。", "error")
            return redirect(url_for("admin_problems"))
        if size > config.MAX_DATA_ZIP_SIZE:
            flash(
                "题目数据包不能超过 %d MB。" % (config.MAX_DATA_ZIP_SIZE // 1024 // 1024),
                "error",
            )
            return redirect(url_for("admin_problems"))
        if not zipfile.is_zipfile(tmp):
            flash("文件不是有效的 zip 压缩包。", "error")
            return redirect(url_for("admin_problems"))

        os.replace(tmp, data_zip_path())
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    set_setting("data_zip_name", original_name)
    set_setting("data_zip_updated", now_str())
    get_db().commit()

    info = inspect_data_package(data_zip_path())
    flash("题目数据包已上传：%s（%s）" % (original_name, fmt_size(size)), "success")
    if info["folders"]:
        detail = "、".join(
            "%s(%d 组)" % (folder["name"], min(folder["ins"], folder["ans"]))
            for folder in info["folders"]
        )
        flash("检测到题目文件夹：" + detail, "success")
    for warning in info["warnings"][:6]:
        flash("结构检查：" + warning, "warn")
    return redirect(url_for("admin_problems"))


@app.route("/admin/problems/data/download")
@admin_required
def admin_data_zip_download():
    package = get_data_package()
    if package is None:
        abort(404)
    return send_file(
        data_zip_path(),
        as_attachment=True,
        download_name=package["name"],
        mimetype="application/zip",
    )


@app.route("/admin/problems/data/zip/delete", methods=["POST"])
@admin_required
def admin_data_zip_delete():
    path = data_zip_path()
    if os.path.exists(path):
        os.remove(path)
    set_setting("data_zip_name", "")
    set_setting("data_zip_updated", "")
    get_db().commit()
    flash("题目数据压缩包已删除。", "success")
    return redirect(url_for("admin_problems"))


def submission_query(args):
    db = get_db()
    clauses = []
    params = []
    q = (args.get("q") or "").strip()
    problem = (args.get("problem") or "").strip().upper()
    if q:
        clauses.append("(s.exam_id LIKE ? OR s.name LIKE ?)")
        like = "%" + q.upper() + "%"
        params.extend([like, "%" + q + "%"])
    if problem in config.PROBLEM_CODES:
        clauses.append("sub.problem_code = ?")
        params.append(problem)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.execute(
        """
        SELECT sub.*, s.exam_id, s.name, p.title AS problem_title
        FROM submissions sub
        JOIN students s ON s.id = sub.student_id
        JOIN problems p ON p.code = sub.problem_code
        %s
        ORDER BY s.exam_id, sub.problem_code
        """ % where,
        params,
    ).fetchall()
    return rows, q, problem


@app.route("/admin/submissions")
@admin_required
def admin_submissions():
    _cleanup_stale_bundles()
    rows, q, problem = submission_query(request.args)
    problems = get_db().execute("SELECT code, title FROM problems ORDER BY code").fetchall()
    return render_template(
        "admin_submissions.html",
        rows=rows,
        q=q,
        problem=problem,
        problems=problems,
        admin=current_admin(),
    )


@app.route("/admin/submissions/<int:sid>/download")
@admin_required
def admin_submission_download(sid):
    row = get_db().execute(
        """
        SELECT sub.*, s.exam_id FROM submissions sub
        JOIN students s ON s.id = sub.student_id WHERE sub.id=?
        """,
        (sid,),
    ).fetchone()
    if row is None:
        abort(404)
    path = submission_path(row["exam_id"], row["stored_name"])
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=row["stored_name"])


@app.route("/admin/submissions/<int:sid>/preview")
@admin_required
def admin_submission_preview(sid):
    row = get_db().execute(
        """
        SELECT sub.*, s.exam_id, s.name, p.title AS problem_title
        FROM submissions sub
        JOIN students s ON s.id = sub.student_id
        JOIN problems p ON p.code = sub.problem_code
        WHERE sub.id=?
        """,
        (sid,),
    ).fetchone()
    if row is None:
        abort(404)
    path = submission_path(row["exam_id"], row["stored_name"])
    if not os.path.exists(path):
        abort(404)
    preview = load_source_preview(path)
    return render_template(
        "preview.html",
        problem_title=row["problem_title"],
        filename=row["stored_name"],
        subtitle="考号 %s · %s · 提交于 %s"
        % (row["exam_id"], row["name"] or "未填写姓名", row["submitted_at"][:16]),
        back_url=url_for("admin_submissions"),
        download_url=url_for("admin_submission_download", sid=sid),
        **preview,
    )


def _remove_bundle(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _cleanup_stale_bundles():
    """清理历史残留的打包临时文件（响应中断时 call_on_close 可能没跑）。"""
    now = time.time()
    try:
        entries = os.listdir(config.DATA_DIR)
    except OSError:
        return
    for name in entries:
        if not name.startswith(".bundle_"):
            continue
        path = os.path.join(config.DATA_DIR, name)
        try:
            if now - os.path.getmtime(path) > 3600:
                os.remove(path)
        except OSError:
            continue


@app.route("/admin/submissions/download-all")
@admin_required
def admin_submission_download_all():
    rows, _q, _problem = submission_query(request.args)
    if not rows:
        flash("当前筛选条件下没有可下载的提交。", "error")
        return redirect(url_for("admin_submissions", **request.args))
    fd, bundle_path = tempfile.mkstemp(prefix=".bundle_", suffix=".zip", dir=config.DATA_DIR)
    os.close(fd)
    included = 0
    try:
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for row in rows:
                path = submission_path(row["exam_id"], row["stored_name"])
                if not os.path.exists(path):
                    continue
                bundle.write(path, arcname="%s/%s" % (row["exam_id"], row["stored_name"]))
                included += 1
        if included == 0:
            os.remove(bundle_path)
            flash("当前筛选条件下没有可下载的提交。", "error")
            return redirect(url_for("admin_submissions", **request.args))
    except BaseException:
        _remove_bundle(bundle_path)
        raise

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = open(bundle_path, "rb")
    response = send_file(
        fh,
        mimetype="application/zip",
        as_attachment=True,
        download_name="submissions_%s.zip" % stamp,
    )

    def _close_and_remove():
        # 先关句柄再删文件（Windows 上打开中的文件无法删除）。
        # 包在 ClosingIterator 里，任何 WSGI 服务器发完响应都会调用 close()。
        try:
            fh.close()
        finally:
            _remove_bundle(bundle_path)

    response.response = ClosingIterator(response.response, _close_and_remove)
    return response


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    db = get_db()
    if request.method == "POST":
        start_raw = (request.form.get("contest_start") or "").strip()
        end_raw = (request.form.get("contest_end") or "").strip()
        start = parse_dt(start_raw)
        end = parse_dt(end_raw)
        if start_raw and start is None:
            flash("开始时间格式不正确。", "error")
            return redirect(url_for("admin_settings"))
        if end_raw and end is None:
            flash("截止时间格式不正确。", "error")
            return redirect(url_for("admin_settings"))
        if start and end and start > end:
            flash("截止时间必须晚于开始时间。", "error")
            return redirect(url_for("admin_settings"))

        set_setting("contest_start", start.strftime("%Y-%m-%dT%H:%M") if start else "")
        set_setting("contest_end", end.strftime("%Y-%m-%dT%H:%M") if end else "")
        set_setting("submissions_enabled", "1" if request.form.get("submissions_enabled") else "0")
        set_setting("login_enabled", "1" if request.form.get("login_enabled") else "0")
        db.commit()
        flash("比赛设置已保存。", "success")
        return redirect(url_for("admin_settings"))

    settings = get_settings()
    _is_open, state, message = submission_status(settings)
    return render_template(
        "admin_settings.html",
        settings=settings,
        state=state,
        message=message,
        admin=current_admin(),
    )


@app.route("/admin/password", methods=["POST"])
@admin_required
def admin_change_password():
    admin = current_admin()
    old_password = request.form.get("old_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not check_password_hash(admin["password_hash"], old_password):
        flash("当前密码不正确。", "error")
        return redirect(url_for("admin_settings"))
    if len(new_password) < config.MIN_PASSWORD_LENGTH:
        flash("新密码至少 %d 位。" % config.MIN_PASSWORD_LENGTH, "error")
        return redirect(url_for("admin_settings"))
    if new_password != confirm:
        flash("两次输入的新密码不一致。", "error")
        return redirect(url_for("admin_settings"))
    get_db().execute(
        "UPDATE admins SET password_hash=? WHERE id=?",
        (generate_password_hash(new_password), admin["id"]),
    )
    get_db().commit()
    flash("管理员密码已修改。", "success")
    return redirect(url_for("admin_settings"))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "请求无效")), 400


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="页面或文件不存在"), 404


@app.errorhandler(413)
def too_large(_error):
    return render_template("error.html", code=413, message="上传内容超过服务器允许的大小"), 413


@app.errorhandler(500)
def server_error(_error):
    return render_template("error.html", code=500, message="服务器内部错误，请稍后重试或联系管理员"), 500


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'",
    )
    return response


init_db()


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
