# CSP 第二轮文件回收系统

> **声明：本项目仅用于测试与练习，不建议用于正式比赛或生产环境。**

一个基于 Flask + SQLite 的 CSP 第二轮文件回收练习项目：选手登录后提交四道题的 C++ 源文件、下载题目数据包并在线预览代码；管理员负责考生、题目资料、提交记录和比赛时间管理。

## 功能

### 选手端

- 考号 + 密码登录，登录失败限速。
- 下载一份 CSP 格式的题目数据压缩包（题目 PDF + 四题测试数据）。
- 上传 `.cpp` 源文件，截止前可反复覆盖，系统只保留最后一次提交。
- 查看提交时间、文件名、大小与 SHA-256。
- 在线预览代码：C++ 语法高亮、行号、复制、下载；源码经过 HTML 转义，防止脚本注入。
- 截止时间到达或管理员关闭通道后，服务端拒绝上传。

### 管理端

- 考生管理：新增、CSV 批量导入、按模板批量生成考号、重置密码、启用/禁用、单个删除、勾选后批量删除。
- 题目资料：编辑题目名称，上传/替换/删除/下载完整题目数据包，自动检查压缩包结构。
- 提交管理：按考号或题目筛选，单条下载、在线预览、打包下载。
- 比赛设置：开始/截止时间、登录与提交开关、修改管理员密码。

## 技术栈

- Python 3.10+
- Flask 3.x
- SQLite
- Jinja2
- 原生 CSS / JavaScript，无外网 CDN 依赖

## 快速开始

### Windows

```bat
git clone https://github.com/qwaszxopklnmso/test_CSP_filemanager.git
cd test_CSP_filemanager
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set CSP_PORT=8010
python app.py
```

请使用真实的 CPython 安装，不要使用 Microsoft Store 的 Python 别名。

也可以直接双击 `run_server.bat`：它会打开可见的控制台窗口，实时输出启动信息和每次访问日志；关闭窗口即停止服务。

### Linux / macOS

```bash
git clone https://github.com/qwaszxopklnmso/test_CSP_filemanager.git
cd test_CSP_filemanager
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
CSP_PORT=8010 python app.py
```

默认监听 `0.0.0.0:8000`，可通过环境变量修改：

```bash
CSP_HOST=127.0.0.1 CSP_PORT=8010 python app.py
```

启动后访问 `http://127.0.0.1:8010`。首次启动会自动创建 `data/`、SQLite 数据库、会话密钥和示例账号。

### 批量生成考号

在后台「考生管理」中填写模板、起止编号、步长和统一初始密码，即可批量创建连续考号。例如：

- 模板：`HL-J%%%%X`
- 范围：`0` 到 `100`
- 生成：`HL-J0000X`、`HL-J0001X`、…、`HL-J0100X`

模板中连续的 `%` 表示编号位数；已存在的考号会自动跳过，单次最多生成 2000 个。

### 登录入口

| 入口 | 地址 | 账号类型 |
| --- | --- | --- |
| 选手登录 | `/login` | 考号，例如 `HL-J0001` |
| 管理员登录 | `/admin/login` | 管理员账号，例如 `admin` |

管理员账号不能从选手登录页登录，请使用页面右上角的「管理员入口」。

### 测试账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin12345` |
| 选手 | `HL-J0001` | `123456` |
| 选手 | `HL-J0002` | `123456` |

> 这些账号仅用于本地练习，部署到任何共享环境前必须修改。

## 题目数据包格式

管理员上传**一个** `.zip` 文件，结构如下：

```text
CSP-S2-题目数据.zip
└─ CSP-S2/
   ├─ 题目.pdf
   ├─ 题目一/
   │  ├─ 题目一01.in
   │  ├─ 题目一01.ans
   │  └─ ...
   ├─ 题目二/
   ├─ 题目三/
   └─ 题目四/
```

- 压缩包内应只有一个根文件夹。
- 根目录放一份题目 PDF。
- 根目录下放四个题目文件夹。
- 每个文件夹内放 `题目名0x.in` / `题目名0x.ans`。
- `0x` 通常为 01–03，也支持更多或非连续编号；系统按实际文件数量统计，不限制测试点个数。

结构异常不会阻止上传，但后台会列出警告。

## 目录结构

```text
.
├── README.md                  # 面向使用者的项目说明（本文件）
├── LICENSE                    # MIT 协议
├── intro.md                   # 需求说明书
├── NOTES.md                   # 开发者内部备忘录（本机环境、启动、踩坑记录）
├── app.py                     # Flask 入口与路由
├── config.py                  # 配置项
├── requirements.txt           # Python 依赖
├── run_server.bat             # Windows 可见控制台启动脚本
├── static/
│   ├── style.css              # 页面样式与动效
│   └── app.js                 # 文件拖拽、水波纹、数字滚动
├── templates/                 # Jinja2 模板
└── data/                      # 运行时生成，已被 .gitignore 排除
    ├── app.db
    ├── secret_key
    ├── problems/
    └── submissions/
```

## app.py 与 config.py 的区别

- `app.py`：程序入口与业务逻辑。负责创建 Flask 应用、初始化数据库、定义所有路由、登录鉴权、文件上传/下载、数据包解析、在线预览和后台操作。
- `config.py`：只放配置常量。包括路径、考号规则、上传大小限制、内部题位、初始题目名称、默认管理员和示例考生、监听地址与端口。

简单说：`config.py` 回答“参数是多少”，`app.py` 回答“程序怎么运行”。修改端口、上限或默认账号时改 `config.py`；修改页面行为或接口时改 `app.py`。

## 配置

主要配置位于 `config.py`：

- `CSP_HOST` / `CSP_PORT`：监听地址与端口。
- `MAX_CPP_SIZE`：单个 cpp 文件上限，默认 10 MB。
- `PREVIEW_MAX_BYTES`：在线预览上限，默认 1 MB。
- `MAX_DATA_ZIP_SIZE`：题目数据包上限，默认 200 MB。
- `PROBLEM_CODES` / `DEFAULT_PROBLEM_TITLES`：四个内部题位与初始名称。
- `DEMO_STUDENTS`：首次初始化时创建的示例考生。

运行时数据（数据库、上传文件、密钥、日志）不会提交到 Git。

## 测试状态

开发过程中已在 Windows 原生 Python 上完成 **67/67** 项端到端检查，覆盖登录、上传/覆盖、数据包解析、在线预览、XSS 转义、后台管理、截止锁定与 CSRF 防护。自动化测试脚本未包含在本仓库中。

## 安全说明

- 本项目仅用于测试与练习，不建议暴露到公网或用于正式比赛。
- 所有 POST 请求校验 CSRF Token。
- 上传文件使用服务端重命名，下载按数据库记录定位，避免路径注入。
- 源码预览先做 HTML 转义，再渲染高亮。
- 会话 Cookie 设置 `HttpOnly`、`SameSite=Lax`。
- 请勿把 `data/`、`secret_key`、日志或真实考生信息提交到仓库。

## 文档说明

- `README.md`：面向使用者，介绍功能、启动方式和配置。
- `intro.md`：完整需求说明书。
- `NOTES.md`：开发者内部备忘录，包含本机环境、启动命令和调试记录，不属于使用文档。

## License

[MIT](LICENSE) © 2026 qwaszxopklnm
