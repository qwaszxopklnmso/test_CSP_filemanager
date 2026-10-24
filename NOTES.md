# NOTES（给我自己看的备忘录）

> **声明：本项目仅用于测试与练习，禁止用于正式比赛或生产环境。**

> 最后更新：2026-10-24。记录环境、启动方式、踩过的坑，避免下次重复摸索。

## 1. 项目状态

- 项目：CSP 第二轮文件回收系统（Flask + SQLite + Jinja2）。
- 需求文档：`intro.md`。
- 代码：`app.py`、`config.py`、`templates/`、`static/style.css`。
- 测试脚本：`/tmp/test_csp.py`（WSL 内，非项目文件，67 项端到端检查）。
- 最近一次完整测试：**67/67 通过**，服务端为 **Windows 原生 Python** 运行；含中文数据包文件名、下载文件名、3 组以上测试点、非连续编号，以及在线预览/语法高亮/XSS 转义、题目名称展示、自定义文件选择器与交互脚本校验。

## 2. 环境事实（重要）

- 当前在 WSL2 中操作，工作目录 `/mnt/e/CSPhtmltst` 对应 Windows 侧 `E:\CSPhtmltst`。
- **不要用 Microsoft Store 的 Python**。`where python` 里的以下路径都是 Store/别名，别用：
  - `C:\Users\DIY\AppData\Local\Microsoft\WindowsApps\python.exe`
  - `C:\Users\DIY\AppData\Local\Python\bin\python.exe`（py -0p 的 3.14，也是 Microsoft 安装）
- **使用 Windows 原生 Python：`D:\Python3.12.0\python.exe`**（Python 3.12.0，非 Microsoft）。
- 已用该解释器安装 Flask：`D:\Python3.12.0\python.exe -m pip install -r E:\CSPhtmltst\requirements.txt`，当前 Flask 3.1.3 / Werkzeug 3.1.9。
- WSL 侧的 `python3` 只是拿来跑测试脚本用的（Flask 装在 user site，`--break-system-packages`），不用于对外服务。

## 3. 端口与当前运行实例

- Windows 的 **8000 端口被占用**（PID 11696，进程名 `Manager`，路径未知）。WSL2 的 localhost 转发会让 WSL 里也显示 8000 被占用，所以项目统一用 **8010**。
- 当前 Windows 服务端进程：**PID 2208**（可能会变，以 `netstat -ano | findstr :8010` 为准）。
- 监听：`0.0.0.0:8010`
  - 本机：`http://127.0.0.1:8010`
  - 局域网：`http://192.168.110.14:8010`（Windows 防火墙可能拦，需要放行）
- 日志：`E:\CSPhtmltst\win_server.out.log`（stderr 也重定向进去了，没有 err.log）。
- 数据库当前为干净状态：2 个示例考生，0 条提交，4 个空题位。

## 4. 在 Windows 启动 / 停止（关键）

### 启动（推荐，已验证稳定）

WSL 里直接 `nohup ... &` 或 `Start-Process -RedirectStandardOutput` 都不稳：bash 工具会等管道、超时后进程可能被带走。用 VBS 隐藏启动最可靠。

在 `E:\CSPhtmltst\_run_hidden.vbs` 写入：

```vbscript
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "E:\CSPhtmltst"
sh.Environment("PROCESS")("CSP_PORT") = "8010"
sh.Run "cmd /c D:\Python3.12.0\python.exe app.py > win_server.out.log 2>&1", 0, False
```

然后执行并删掉启动器：

```bash
cmd.exe /c 'wscript.exe E:\CSPhtmltst\_run_hidden.vbs'
rm -f /mnt/e/CSPhtmltst/_run_hidden.vbs
```

说明：`sh.Run(..., 0, False)` 隐藏窗口且不等待；`cmd /c ... > log 2>&1` 负责重定向。Python 路径无空格，所以不用加引号。

### 停止

```bash
cmd.exe /c "netstat -ano | findstr :8010"
cmd.exe /c "taskkill /PID <PID> /F"
```

### 其他启动方式（备选）

```bash
cmd.exe /c 'cd /d E:\CSPhtmltst && set CSP_PORT=8010 && D:\Python3.12.0\python.exe app.py'
```

前台运行，占用终端，仅调试用。

## 5. 坑与规避

1. **WSL 的 `pkill -f start_csp.py` 会杀掉自己**：当前 bash 的命令行里也包含该字符串。用 `pkill -f 'start_csp[.]py'` 或按 PID/taskkill。
2. **WSL → cmd.exe 的引号会被吃**：`cmd.exe /c "python -c \"import app\""` 传到 Windows 变成坏引号，报 `SyntaxError: unterminated string literal`。规避：把 Python 代码写成临时 `.py` 文件再执行，例如：
   ```bash
   printf 'import app\nprint("db ready")\n' > /mnt/e/CSPhtmltst/_init_check.py
   cmd.exe /c 'cd /d E:\CSPhtmltst && D:\Python3.12.0\python.exe _init_check.py'
   rm /mnt/e/CSPhtmltst/_init_check.py
   ```
3. **PowerShell Start-Process + RedirectStandardOutput 从 bash 工具调用会挂 30s**，且实测服务会莫名退出。别再用了，直接上 VBS。
4. **`cmd.exe /c "tasklist /FI \"PID eq N\""`** 引号同样会坏；查进程用 `powershell.exe -NoProfile -Command 'Get-Process -Id N'`。
5. **Windows 原生 curl 输出带引号**：`cmd.exe /c "curl ... -w \"%{http_code}\""` 输出形如 `"200"`，正常。
6. **WSL localhost 转发**：Windows 监听 8010 后，WSL 里 `curl 127.0.0.1:8010` 也能通；测试脚本就是走这个。

## 6. 测试与重置数据

### 跑测试

```bash
cd /mnt/e/CSPhtmltst
python3 /tmp/test_csp.py     # 默认 BASE=http://127.0.0.1:8010
```

覆盖：登录/越权、CSRF、限速、cpp 上传/覆盖/下载、非 cpp 拒绝、完整题目数据包上传/解析/下载/删除、中文文件名、管理员导入/重置/启停、提交筛选与打包、截止锁定、登录/提交开关等。

注意：测试会写脏数据库（新增 HL-J0010/0011、提交、题目数据包、改设置）。测试结束会恢复开关，但**不会删测试数据**。

### 重置为干净演示状态

```bash
# 1. 先停服务（按上面 PID 方式）
rm -rf /mnt/e/CSPhtmltst/data /mnt/e/CSPhtmltst/win_server.out.log
# 2. 用 Windows Python 初始化（会建库、四个题位、admin、两个示例考生）
printf 'import app\nprint("db ready")\n' > /mnt/e/CSPhtmltst/_init_check.py
cmd.exe /c 'cd /d E:\CSPhtmltst && D:\Python3.12.0\python.exe _init_check.py'
rm /mnt/e/CSPhtmltst/_init_check.py
# 3. 重新用 VBS 启动
```

`data/` 已在 `.gitignore` 中；删掉可完全重置。

## 7. 账号与关键配置

- 示例选手：`HL-J0001 / 123456`、`HL-J0002 / 123456`。
- 管理员：`admin / admin12345`（首次登录后务必改）。
- 考号正则：`config.EXAM_ID_PATTERN = r"^HL-J\d{4}$"`，登录时统一转大写。
- 上传上限：cpp 10 MB，题目数据包 200 MB，全局请求 210 MB（`config.MAX_DATA_ZIP_SIZE` / `MAX_CONTENT_LENGTH`）。
- 预览上限：`config.PREVIEW_MAX_BYTES = 1 MB`，超出只预览前 1 MB 并提示。
- 时间判断：服务器本地时间，`%Y-%m-%dT%H:%M`；截止后服务端拒绝上传，不只靠前端禁用。
- 环境变量：`CSP_HOST`（默认 0.0.0.0）、`CSP_PORT`（默认 8000）。
- 密钥：`data/secret_key` 首次启动生成，备份 `data/` 即可保留会话。

## 8. 实现要点（改代码前看这里）

- 路由与全部逻辑都在 `app.py`，单文件；建库 `init_db()` 在 import 时执行，所以 `import app` 即可初始化。
- 数据表：`students`、`admins`、`problems`、`submissions`、`settings`。
- `submissions` 上有 `UNIQUE(student_id, problem_code)`，配合 `ON CONFLICT ... DO UPDATE` 实现“保留最后一次提交”。
- 提交文件落盘路径：`data/submissions/<考号>/<考号>_<题号>.cpp`，先写 `.tmp_*` 再 `os.replace`，原子替换。
- 题目数据包：单文件 `data/problems/contest_data.zip`；settings 里存 `data_zip_name`（原始文件名）和 `data_zip_updated`。上传统一写到临时文件，校验后再 `os.replace`。
- 数据包结构（CSP 常规）：根目录一个 PDF + 四个题目文件夹，文件夹内是 `题目名0x.in` / `题目名0x.ans`。0x 常见 01–03，也可能更多/其他编号；系统只按文件名主干配对，不限制测试点数量、不要求连续编号。`inspect_data_package()` 只读 zip 中央目录，不落盘解压，检查根目录、PDF、文件夹数量、in/ans 配对，异常只警告不阻止上传。
- 选手下载：`GET /data/download`（需登录）；管理员检查下载：`GET /admin/problems/data/download`；上传 `POST /admin/problems/data/zip`；删除 `POST /admin/problems/data/zip/delete`。
- 已移除旧的“每题一个 zip”逻辑（problems.zip_name 不再使用，旧库多出的列无害）。
- 在线预览：`load_source_preview()` 读取源码（utf-8-sig → gb18030 → utf-8 replace），`highlight_cpp_line()` 做行级 C++ 词法高亮（支持跨行块注释），所有原文先 HTML 转义，模板里用 `|safe` 输出高亮结果。token 类名 `tok-kw/type/str/com/num/pre/fn`，样式在 `style.css` 的 Code preview 段。
- 预览路由：选手 `GET /submission/<code>/preview`（只能看自己的），管理员 `GET /admin/submissions/<sid>/preview`；模板 `templates/preview.html`，含行号、复制、下载。
- 页面特效：渐变流动背景/顶栏、卡片入场与悬停、状态呼吸点、按钮渐变与水波纹、数字滚动、表格悬停强调；已适配 `prefers-reduced-motion`。不要依赖 CDN。
- 文件选择：`static/app.js` 增强 `.file-drop`（点击/拖拽、选中文件名、拖拽高亮），并处理 `.btn` 水波纹与 `.stat-num` 数字滚动；新增文件输入统一用 `.file-drop` 结构。
- 题目展示：界面只显示 `problems.title`，不再显示 T1–T4 徽标；内部路由/数据库仍用 T1–T4。题目名称在后台「题目资料」修改。
- CSRF：`before_request` 统一校验所有 POST，模板用 `csrf_token` 注入隐藏字段；登录成功后 `session.clear()` 会重置 token，下一页自动重建。
- 会话键：学生会话 `student_id/student_exam_id`，管理员 `admin_id/admin_username`，两者共用 Flask 签名 Cookie，但同一浏览器登录学生会把管理员登出（可接受）。
- 密码：`werkzeug.security` 哈希，绝不明文。
- 管理后台忘记密码的兜底：停服 → 删除 `admins` 表所有行 → 重启（`init_db` 在 admins 为空时重建 admin/admin12345）。考生数据不受影响。

## 9. 待办 / 正式部署前

- 当前是 Flask 开发服务器，正式比赛建议 `waitress-serve --listen=0.0.0.0:8010 app:app`（Windows）或 gunicorn（Linux）。`waitress` 还没装。
- HTTPS 未配，`SESSION_COOKIE_SECURE` 未开；内网可接受。
- 登录限速是进程内内存字典，多 worker 会各自计数。
- 局域网访问需要放行 Windows 防火墙 8010 端口（目前只验证了本机 127.0.0.1）。
- 没有“提交后自动评测”，纯文件回收；没有查重/代码相似度。
- `data/` 定期备份；比赛结束先打包下载全部提交再做任何操作。
