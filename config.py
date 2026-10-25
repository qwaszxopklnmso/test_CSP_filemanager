import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")
PROBLEMS_DIR = os.path.join(DATA_DIR, "problems")
SUBMISSIONS_DIR = os.path.join(DATA_DIR, "submissions")
SECRET_KEY_PATH = os.path.join(DATA_DIR, "secret_key")

EXAM_ID_PATTERN = r"^HL-J\d{4}[A-Z]?$"
EXAM_ID_HINT = "HL-J0001"

MIN_PASSWORD_LENGTH = 6
MAX_CPP_SIZE = 10 * 1024 * 1024
PREVIEW_MAX_BYTES = 1024 * 1024
MAX_DATA_ZIP_SIZE = 200 * 1024 * 1024
MAX_CONTENT_LENGTH = MAX_DATA_ZIP_SIZE + 10 * 1024 * 1024
DATA_ZIP_FILENAME = "contest_data.zip"

PROBLEM_CODES = ["T1", "T2", "T3", "T4"]
DEFAULT_PROBLEM_TITLES = {
    "T1": "第一题",
    "T2": "第二题",
    "T3": "第三题",
    "T4": "第四题",
}

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin12345"

DEMO_STUDENTS = [
    ("HL-J0001", "123456", "示例选手一"),
    ("HL-J0002", "123456", "示例选手二"),
]

HOST = os.environ.get("CSP_HOST", "0.0.0.0")
PORT = int(os.environ.get("CSP_PORT", "8000"))
# 仅当部署在可信反向代理（nginx 等）后面时设 CSP_TRUST_PROXY=1，此时才读取
# X-Forwarded-For；否则客户端可伪造该头绕过登录限速。
TRUST_PROXY_HEADERS = os.environ.get("CSP_TRUST_PROXY", "") == "1"

MAX_NAME_LENGTH = 64
MAX_PROBLEM_TITLE_LENGTH = 100
DATA_DOWNLOAD_COOLDOWN = 10
