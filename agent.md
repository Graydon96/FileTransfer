# 项目说明

## 项目概览

这是一个局域网文件传输工具，后端使用 FastAPI 和 Uvicorn，前端是单个 HTML 页面。用户在浏览器中打开服务地址后，可以发现在线设备、选择目标设备、上传文件、下载文件、删除文件，并通过 WebSocket 接收设备列表、文件通知和聊天消息。

项目当前已初始化为 Git 仓库，并配置了 GitHub 远程仓库：

- 远程仓库：`https://github.com/Graydon96/FileTransfer.git`
- 当前分支：`main`
- Docker 镜像名：`graydon96/filetransfer:latest`

## 目录结构

```text
.
├── main.py              # FastAPI 后端主程序
├── templates/
│   └── index.html       # 前端单页页面，包含 HTML/CSS/JavaScript
├── uploads/             # 运行时上传文件目录，已被 .gitignore 忽略
├── requirements.txt     # Python 依赖
├── Dockerfile           # Docker 镜像构建文件
├── .dockerignore        # Docker 构建忽略文件，目前为空
├── .gitignore           # Git 忽略规则
├── start.bat            # Windows 一键启动脚本
├── README.md            # 简短项目介绍
└── LICENSE              # 开源许可证
```

## 后端说明

后端入口是 `main.py`。

核心配置位于文件顶部：

```python
UPLOAD_DIR = Path(__file__).parent / "uploads"
FILE_RETENTION_DAYS = 3
FILE_RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", str(FILE_RETENTION_DAYS)))
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
HOST = "0.0.0.0"
PORT = 8080
```

含义：

- `UPLOAD_DIR`：上传文件保存目录。普通运行时是项目根目录下的 `uploads`；Docker 中因为 `WORKDIR /data`，路径是 `/data/uploads`。
- `FILE_RETENTION_DAYS`：文件保留天数，默认 3 天；可通过环境变量 `FILE_RETENTION_DAYS` 覆盖。
- `MAX_FILE_SIZE`：单文件最大 2GB。
- `HOST`：监听所有网卡，方便局域网访问。
- `PORT`：服务端口 8080。

主要接口：

- `GET /`：返回 `templates/index.html`。
- `WS /ws`：设备注册、在线设备列表广播、聊天消息、心跳。
- `POST /upload`：上传文件。
- `GET /files`：列出当前客户端有权访问的文件。
- `GET /download/{filename}`：下载文件。
- `POST /delete/{filename}`：删除文件。
- `GET /health`：健康检查。

## 文件存储与权限

上传文件会保存到 `UPLOAD_DIR`，文件名格式大致为：

```text
目标设备名_年月日_时分秒_原文件名
```

同时会在 `uploads/.metadata/` 下保存对应 JSON 元数据，用于记录：

- 目标客户端 ID
- 目标设备名
- 发送方客户端 ID
- 发送方设备名
- 创建时间

文件访问权限逻辑在 `_is_authorized()` 中：

- 如果文件有元数据，则只有发送方或接收方可以访问。
- 如果文件没有元数据，则默认允许访问，用于兼容旧文件。

过期清理逻辑：

- 服务启动时会执行一次 `cleanup_old_files()`。
- 服务运行后每小时执行一次清理。
- 超过 `FILE_RETENTION_DAYS` 的普通文件会被删除。

## 前端说明

前端文件是 `templates/index.html`，没有单独的构建流程。页面内直接包含 CSS 和 JavaScript。

主要功能：

- 打开页面时通过 `prompt()` 输入设备名称。
- 通过 WebSocket 连接 `/ws` 注册设备。
- 维护在线设备列表。
- 支持选择目标设备后上传文件。
- 使用 `XMLHttpRequest` 上传文件，以便显示上传进度。
- 支持文件列表刷新、下载、删除。
- 支持简单聊天消息广播。

注意：当前前端和后端文件中的部分中文显示为乱码，应该是历史编码问题导致的。功能代码仍可运行，但后续维护时建议谨慎处理编码，避免无意中扩大乱码范围。

## 依赖说明

`requirements.txt` 当前内容：

```text
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.6
```

其中 `uvicorn[standard]` 很重要，因为项目使用 WebSocket。若只安装基础 `uvicorn`，容器日志会出现类似提示：

```text
WARNING: No supported WebSocket library detected.
```

## 本地运行

推荐方式：

```powershell
cd "D:\Ai Project\filetransfer"
pip install -r requirements.txt
python main.py
```

启动后访问：

```text
http://localhost:8080
```

也可以运行 Windows 脚本：

```powershell
.\start.bat
```

`start.bat` 会检查 Python、安装依赖并启动 `main.py`。

## Docker 运行

构建镜像：

```powershell
docker build -t graydon96/filetransfer:latest .
```

直接运行：

```powershell
docker run -d --name filetransfer -p 8080:8080 graydon96/filetransfer:latest
```

挂载上传目录并自定义保留天数：

```powershell
docker run -d --name filetransfer `
  -p 8080:8080 `
  -v "D:\FileTransferUploads:/data/uploads" `
  -e FILE_RETENTION_DAYS=7 `
  graydon96/filetransfer:latest
```

因为 `Dockerfile` 中设置了：

```dockerfile
WORKDIR /data
```

所以容器内上传目录是：

```text
/data/uploads
```

查看日志：

```powershell
docker logs -f filetransfer
```

停止并删除容器：

```powershell
docker stop filetransfer
docker rm filetransfer
```

## Docker Hub

镜像标签使用：

```text
graydon96/filetransfer:latest
```

推送前需要登录 Docker Hub：

```powershell
docker login
docker push graydon96/filetransfer:latest
```

## Git 与忽略规则

`.gitignore` 已忽略：

- Python 缓存
- 虚拟环境
- 构建产物
- 测试缓存
- `.env` 本地配置
- `uploads/*`
- 日志文件
- 编辑器配置
- 系统生成文件

重要：`uploads/` 目录被忽略，上传文件不会进入 Git 仓库。

当前 `.dockerignore` 是空文件。建议后续补充至少以下内容，避免把 Git 元数据、缓存和上传文件打进镜像：

```text
.git
__pycache__/
*.pyc
uploads/
.env
.venv/
venv/
```

## 已知注意点

- 当前 `main.py` 和 `templates/index.html` 中有中文乱码，但 Python 语法检查通过，Docker 容器也可以正常启动。
- `FILE_RETENTION_DAYS` 通过环境变量读取，但如果传入非数字字符串，程序启动时会抛出 `ValueError`。
- WebSocket 消息目前是广播模式，聊天消息不是严格点对点。
- 文件权限依赖内存中的客户端 ID 和磁盘中的元数据。服务重启后，在线客户端 ID 会重新生成，旧文件权限体验可能受影响。
- `.dockerignore` 目前为空，构建镜像时可能把不必要文件带入构建上下文。

## 常用维护命令

语法检查：

```powershell
python -m py_compile main.py
```

查看 Git 状态：

```powershell
git status --short --branch
```

构建 Docker 镜像：

```powershell
docker build -t graydon96/filetransfer:latest .
```

本地测试容器：

```powershell
docker run --rm -p 8080:8080 `
  -v "D:\FileTransferUploads:/data/uploads" `
  -e FILE_RETENTION_DAYS=7 `
  graydon96/filetransfer:latest
```
