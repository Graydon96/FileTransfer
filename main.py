"""
局域网文件传输工具 - 后端服务
启动方式: python main.py
访问地址: http://localhost:8080 (或局域网IP:8080)
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File as FastAPIFile, Form, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.exceptions import HTTPException
import uvicorn

# ========== 配置 ==========
UPLOAD_DIR = Path(__file__).parent / "uploads"
CONF_DIR = Path(__file__).parent / "conf"
DEVICE_NAMES_FILE = CONF_DIR / "device_names.json"
FILE_RETENTION_DAYS = 3          # 文件保留天数
FILE_RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", str(FILE_RETENTION_DAYS)))
HOST = "0.0.0.0"                # 监听所有网卡（局域网可访问）
PORT = 8080


def _parse_size(value: str | None, default: int) -> int:
    """解析大小配置，支持 500MB、2GB 或纯字节数。"""
    if not value:
        return default

    text = value.strip().upper().replace(" ", "")
    units = {
        "KB": 1024,
        "K": 1024,
        "MB": 1024 ** 2,
        "M": 1024 ** 2,
        "GB": 1024 ** 3,
        "G": 1024 ** 3,
    }
    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            return int(float(text[:-len(suffix)]) * multiplier)
    return int(float(text))


def _format_size(size: int) -> str:
    """格式化大小用于日志、错误消息和前端展示。"""
    if size >= 1024 ** 3:
        value = size / (1024 ** 3)
        return f"{value:g} GB"
    if size >= 1024 ** 2:
        value = size / (1024 ** 2)
        return f"{value:g} MB"
    if size >= 1024:
        value = size / 1024
        return f"{value:g} KB"
    return f"{size} B"


MAX_FILE_SIZE = _parse_size(os.getenv("MAX_FILE_SIZE"), 2 * 1024 * 1024 * 1024)
MAX_FILE_SIZE_LABEL = _format_size(MAX_FILE_SIZE)
FILE_RETENTION_DAYS_LABEL = f"{FILE_RETENTION_DAYS}天"


# ========== 全局状态 ==========
connected_clients: dict[str, WebSocket] = {}   # client_id -> websocket
client_info: dict[str, dict] = {}              # client_id -> {device_id, name, alias, browser, ip, connect_time}

# 文件元数据存储：filename -> {target_device_id, target_name, sender_device_id, sender_name, created_at}
file_metadata: dict[str, dict] = {}
device_names: dict[str, str] = {}


def _metadata_filename_to_upload_name(meta_path: Path) -> str:
    """Return the upload filename represented by a metadata JSON file."""
    suffix = ".json"
    if meta_path.name.endswith(suffix):
        return meta_path.name[:-len(suffix)]
    return meta_path.stem


def _resolve_upload_file(filename: str) -> Path:
    """Resolve a user-supplied upload filename without leaving UPLOAD_DIR."""
    if not filename or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    upload_root = UPLOAD_DIR.resolve()
    filepath = (UPLOAD_DIR / filename).resolve()
    try:
        filepath.relative_to(upload_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if filepath.parent != upload_root:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return filepath


def _download_filename(stored_name: str) -> str:
    """Strip the target/timestamp prefix from stored upload names."""
    match = re.match(r"^.+_\d{8}_\d{6}_(.+)$", stored_name)
    return match.group(1) if match else stored_name


def _load_file_metadata():
    """从磁盘加载已有的文件元数据"""
    meta_dir = UPLOAD_DIR / ".metadata"
    if not meta_dir.exists():
        return
    for f in meta_dir.iterdir():
        if f.suffix == ".json":
            filename = _metadata_filename_to_upload_name(f)
            try:
                with open(f, "r", encoding="utf-8") as mf:
                    file_metadata[filename] = json.load(mf)
            except Exception:
                pass


def _save_file_metadata():
    """将文件元数据持久化到磁盘"""
    meta_dir = UPLOAD_DIR / ".metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    for filename, meta in file_metadata.items():
        meta_file = meta_dir / f"{filename}.json"
        with open(meta_file, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False)


def _delete_file_metadata(filename: str):
    """删除文件元数据"""
    meta_file = UPLOAD_DIR / ".metadata" / f"{filename}.json"
    if meta_file.exists():
        try:
            meta_file.unlink()
        except Exception:
            pass
    file_metadata.pop(filename, None)


def _load_device_names():
    """从 JSON 文件加载自定义设备名。"""
    if not DEVICE_NAMES_FILE.exists():
        return
    try:
        with open(DEVICE_NAMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    if not isinstance(data, dict):
        return

    for raw_id, raw_name in data.items():
        device_id = _normalize_device_id(str(raw_id))
        name = _clean_device_alias(str(raw_name))
        if device_id and name:
            device_names[device_id] = name


def _save_device_names():
    """将自定义设备名保存到 JSON 文件。"""
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEVICE_NAMES_FILE, "w", encoding="utf-8") as f:
        json.dump(device_names, f, ensure_ascii=False, indent=2)


def _clean_device_alias(value: str) -> str:
    """清理用户自定义设备名。"""
    text = " ".join("".join(ch for ch in (value or "").strip() if ch.isprintable()).split())
    return text[:30]


def _get_device_alias(device_id: str) -> str:
    """优先使用自定义设备名，否则使用自动生成别名。"""
    custom_name = device_names.get(device_id)
    if custom_name:
        return _get_unique_device_name(custom_name, device_id)
    return _generate_device_alias(device_id)


def _refresh_online_device_name(device_id: str):
    """刷新当前在线设备中同一稳定 device_id 的展示名。"""
    for info in client_info.values():
        if info.get("device_id") == device_id:
            alias = _get_device_alias(device_id)
            info["alias"] = alias
            info["name"] = _build_device_name(alias, info.get("browser", "未知浏览器"), info.get("ip", "unknown"))


def _get_client_id_by_ip(ip: str) -> str | None:
    """通过 IP 反查当前连接的 client_id"""
    for cid, info in client_info.items():
        if info["ip"] == ip:
            return cid
    return None


def _get_client_info_by_device_id(device_id: str) -> dict | None:
    """通过稳定 device_id 查找当前在线设备信息"""
    for info in client_info.values():
        if info.get("device_id") == device_id:
            return info
    return None


def _is_authorized(device_id: str, filename: str, client_id: str = "") -> bool:
    """检查某客户端是否有权限访问指定文件（发送方或接收方均可）"""
    meta = file_metadata.get(filename)
    if meta is None:
        # 没有元数据的旧文件，默认允许访问（兼容已有数据）
        return True

    if device_id and (meta.get("sender_device_id") == device_id or
                      meta.get("target_device_id") == device_id):
        return True

    # 兼容旧元数据：旧版本只记录临时 client_id。
    return bool(client_id and (meta.get("sender_client_id") == client_id or
                               meta.get("target_client_id") == client_id))


def _extract_sender_ip(request: Request) -> str:
    """提取请求来源 IP"""
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def cleanup_old_files():
    """清理超过保留期限的文件"""
    if not UPLOAD_DIR.exists():
        return
    now = time.time()
    max_age = FILE_RETENTION_DAYS * 86400  # 秒
    for f in UPLOAD_DIR.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > max_age:
            try:
                f.unlink()
                print(f"[清理] 已删除过期文件: {f.name}")
            except Exception as e:
                print(f"[清理] 删除失败 {f.name}: {e}")


async def _broadcast_device_list():
    """向所有在线设备广播当前设备列表"""
    devices = [
        {
            "id": cid,
            "device_id": info.get("device_id", cid),
            "name": info["name"],
            "alias": info.get("alias", info["name"]),
            "browser": info.get("browser", "未知浏览器"),
            "ip": info["ip"],
        }
        for cid, info in client_info.items()
    ]
    payload = {"type": "device_list", "devices": devices}
    await _broadcast_to_all(payload)


async def _broadcast_to_all(payload: dict):
    """向所有在线 WebSocket 发送消息"""
    msg = json.dumps(payload, ensure_ascii=False)
    for ws in list(connected_clients.values()):
        try:
            await ws.send_text(msg)
        except Exception:
            pass


def _get_unique_device_name(base: str, device_id: str = "") -> str:
    """确保同一网络下设备名唯一，自动加序号"""
    used = {
        info.get("alias", info["name"])
        for info in client_info.values()
        if info.get("device_id") != device_id
    }
    name = base
    counter = 1
    while name in used and len(connected_clients) > 0:
        name = f"{base}#{counter}"
        counter += 1
    return name


DEVICE_ADJECTIVES = [
    "明快", "安静", "灵巧", "温柔", "勇敢", "清爽", "闪亮", "可靠",
    "轻盈", "敏捷", "快乐", "沉稳", "聪明", "暖心", "从容", "鲜活",
]

DEVICE_FRUITS = [
    "苹果", "香蕉", "橙子", "梨子", "桃子", "芒果", "葡萄", "草莓",
    "柚子", "樱桃", "西瓜", "菠萝", "荔枝", "蓝莓", "柠檬", "石榴",
]


def _detect_browser(user_agent: str) -> str:
    """从 User-Agent 粗略识别浏览器类型。"""
    ua = (user_agent or "").lower()
    if "edg/" in ua:
        return "Edge"
    if "opr/" in ua or "opera" in ua:
        return "Opera"
    if "firefox/" in ua:
        return "Firefox"
    if "samsungbrowser/" in ua:
        return "Samsung Internet"
    if "chrome/" in ua or "crios/" in ua:
        return "Chrome"
    if "safari/" in ua:
        return "Safari"
    return "未知浏览器"


def _clean_label(value: str, fallback: str, max_length: int = 40) -> str:
    """清理客户端上报的短标签，避免超长或控制字符进入设备名。"""
    text = "".join(ch for ch in (value or "").strip() if ch.isprintable())
    return (text or fallback)[:max_length]


def _generate_device_alias(device_id: str) -> str:
    """生成“形容词 + 水果”的设备别名。"""
    digest = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
    seed = int(digest[:12], 16)
    adjective = DEVICE_ADJECTIVES[seed % len(DEVICE_ADJECTIVES)]
    fruit = DEVICE_FRUITS[(seed // len(DEVICE_ADJECTIVES)) % len(DEVICE_FRUITS)]
    return _get_unique_device_name(f"{adjective}的{fruit}", device_id)


def _normalize_device_id(value: str) -> str:
    """清理并限制客户端持久化的 device_id。"""
    text = "".join(ch for ch in (value or "").strip() if ch.isalnum() or ch in "-_")
    return text[:80] or uuid.uuid4().hex


def _build_device_name(alias: str, browser: str, ip: str) -> str:
    """拼出列表中展示的完整设备名。"""
    return f"{alias} · {browser} · {ip}"


def _safe_filename_label(value: str) -> str:
    """将设备显示名转换为可用于文件名前缀的文本。"""
    invalid_chars = '<>:"/\\|?*'
    safe = "".join("_" if ch in invalid_chars or ord(ch) < 32 else ch for ch in value)
    return safe.strip(" .")[:120] or "未知设备"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时创建目录、加载元数据并定期清理"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    _load_device_names()
    _load_file_metadata()  # 恢复持久化的文件元数据
    cleanup_old_files()

    async def periodic_cleanup():
        while True:
            await asyncio.sleep(3600)
            cleanup_old_files()

    task = asyncio.create_task(periodic_cleanup())
    yield
    task.cancel()


# ========== FastAPI 应用 ==========
app = FastAPI(title="文件传输工具", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """返回前端页面"""
    with open(os.path.join(os.path.dirname(__file__), "templates/index.html"), "r", encoding="utf-8") as f:
        return (
            f.read()
            .replace("__MAX_FILE_SIZE_LABEL__", MAX_FILE_SIZE_LABEL)
            .replace("__FILE_RETENTION_DAYS_LABEL__", FILE_RETENTION_DAYS_LABEL)
        )


@app.post("/device-name")
async def set_device_name(request: Request, device_id: str = Form(""), device_name: str = Form("")):
    """设置或清除当前稳定设备 ID 的自定义名称。"""
    stable_id = _normalize_device_id(device_id) if device_id.strip() else ""
    if not stable_id:
        return {"ok": False, "error": "缺少 device_id"}

    name = _clean_device_alias(device_name)
    if not name:
        device_names.pop(stable_id, None)
    else:
        device_names[stable_id] = name

    _save_device_names()
    _refresh_online_device_name(stable_id)
    await _broadcast_device_list()
    alias = _get_device_alias(stable_id)
    info = _get_client_info_by_device_id(stable_id) or {}
    display_name = info.get("name") or _build_device_name(alias, "未知浏览器", "unknown")
    return {
        "ok": True,
        "device_id": stable_id,
        "custom_name": device_names.get(stable_id, ""),
        "alias": alias,
        "device_name": display_name,
    }


# ========== WebSocket - 设备连接与消息 ==========
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点：处理设备注册、发现、消息"""
    await websocket.accept()

    client_id = str(uuid.uuid4())[:8]
    client_ip = str(websocket.client.host) if websocket.client else "unknown"
    device_id = ""
    browser = "未知浏览器"
    try:
        data = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        parsed = json.loads(data)
        device_id = _normalize_device_id(parsed.get("device_id", ""))
        user_agent = parsed.get("user_agent") or websocket.headers.get("user-agent", "")
        browser = _clean_label(parsed.get("browser", "") or _detect_browser(user_agent), "未知浏览器")
    except Exception:
        device_id = uuid.uuid4().hex
        browser = _detect_browser(websocket.headers.get("user-agent", ""))

    device_alias = _get_device_alias(device_id)
    device_name = _build_device_name(device_alias, browser, client_ip)

    connected_clients[client_id] = websocket
    client_info[client_id] = {
        "device_id": device_id,
        "name": device_name,
        "alias": device_alias,
        "browser": browser,
        "ip": client_ip,
        "connect_time": datetime.now().isoformat(),
    }

    print(f"[设备] 已连接: {device_name} (ID:{client_id})")

    # 将 client_id 发送给客户端（用于后续 API 请求的权限校验）
    await websocket.send_text(json.dumps({
        "type": "registered",
        "client_id": client_id,
        "device_id": device_id,
        "custom_name": device_names.get(device_id, ""),
        "alias": device_alias,
        "my_name": device_name,
    }))

    # 广播：有新设备上线
    await _broadcast_device_list()

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "message":
                    target_client_id = (msg.get("target_device") or "").strip()
                    target_ws = connected_clients.get(target_client_id)
                    if not target_ws:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "目标设备不在线，消息未发送",
                        }, ensure_ascii=False))
                        continue

                    payload = {
                        "type": "message",
                        "from_name": client_info[client_id]["name"],
                        "to_name": client_info.get(target_client_id, {}).get("name", ""),
                        "content": msg.get("content", ""),
                        "timestamp": datetime.now().strftime("%H:%M"),
                    }
                    message_text = json.dumps(payload, ensure_ascii=False)
                    await target_ws.send_text(message_text)
                    if target_client_id != client_id:
                        await websocket.send_text(message_text)

                elif msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.pop(client_id, None)
        removed_name = client_info.pop(client_id, {}).get("name", "")
        print(f"[设备] 已断开: {removed_name}")
        await _broadcast_device_list()


# ========== HTTP - 文件上传与列表 ==========
@app.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = FastAPIFile(...),
    target_device: str = Form(None),
    sender_id: str = Form(""),          # 兼容旧前端：发送方的 WebSocket client_id
    sender_device_id: str = Form(""),   # 稳定设备 ID，用于刷新/重连后的权限校验
):
    """上传文件"""
    sender_ip = _extract_sender_ip(request)

    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)

    if size > MAX_FILE_SIZE:
        return {"error": f"文件过大，最大支持 {MAX_FILE_SIZE_LABEL}"}

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_name = file.filename or "unknown"
    safe_original = "".join(c if c.isalnum() or c in "._- " else "_" for c in original_name)[:100]

    target_client_id = target_device or ""
    sender_cid = sender_id.strip() or _get_client_id_by_ip(sender_ip) or ""
    sender_stable_id = _normalize_device_id(sender_device_id) if sender_device_id.strip() else ""
    sender_info = (_get_client_info_by_device_id(sender_stable_id) if sender_stable_id else None) or client_info.get(sender_cid, {})
    target_info = client_info.get(target_client_id, {})
    target_stable_id = target_info.get("device_id", "")
    sender_label = sender_info.get("name", "未知设备")
    target_label = target_info.get("name", target_client_id or "未知设备")
    safe_target_label = _safe_filename_label(target_label)

    filename = f"{safe_target_label}_{now_str}_{safe_original}"
    filepath = UPLOAD_DIR / filename

    if filepath.exists():
        name_part, ext = os.path.splitext(filename)
        filename = f"{name_part}_{uuid.uuid4().hex[:6]}{ext}"
        filepath = UPLOAD_DIR / filename

    try:
        with open(filepath, "wb") as out_file:
            shutil.copyfileobj(file.file, out_file)
    except Exception as e:
        return {"error": f"保存失败: {str(e)}"}

    # ── 记录文件元数据（权限控制） ──
    file_metadata[filename] = {
        "target_device_id": target_stable_id,
        "target_client_id": target_client_id if target_info else "",
        "target_name": target_label,
        "sender_device_id": sender_stable_id or sender_info.get("device_id", ""),
        "sender_client_id": sender_cid,
        "sender_name": sender_label,
        "created_at": datetime.now().isoformat(),
    }
    _save_file_metadata()

    await _broadcast_to_all({
        "type": "new_file",
        "filename": filename,
        "size": size,
        "from_name": sender_label,
        "to_name": target_label,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })

    return {"ok": True, "filename": filename, "size": size}


@app.get("/files")
async def list_files(request: Request, device_id: str = "", client_id: str = ""):
    """列出当前用户有权访问的文件（发送方或接收方的文件）"""
    sender_ip = _extract_sender_ip(request)
    cid = client_id.strip() or _get_client_id_by_ip(sender_ip) or ""
    stable_id = _normalize_device_id(device_id) if device_id.strip() else ""

    now = time.time()
    max_age = FILE_RETENTION_DAYS * 86400

    files = []
    for f in sorted(UPLOAD_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not f.is_file():
            continue
        age = now - f.stat().st_mtime
        if age > max_age:
            continue
        # ── 权限过滤：只显示该用户有权访问的文件 ──
        if (stable_id or cid) and not _is_authorized(stable_id, f.name, cid):
            continue

        size_bytes = f.stat().st_size
        meta = file_metadata.get(f.name, {})
        is_sent = (stable_id and meta.get("sender_device_id") == stable_id) or (cid and meta.get("sender_client_id") == cid)
        is_received = (stable_id and meta.get("target_device_id") == stable_id) or (cid and meta.get("target_client_id") == cid)
        files.append({
            "name": f.name,
            "size": size_bytes,
            "time": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "expires_at": (datetime.fromtimestamp(f.stat().st_mtime) + timedelta(days=FILE_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M"),
            "role": "sent" if is_sent else ("received" if is_received else None),
        })

    return {"files": files}


@app.get("/download/{filename:path}")
async def download_file(request: Request, filename: str, device_id: str = "", client_id: str = ""):
    """下载文件（仅发送方或接收方可访问）"""
    sender_ip = _extract_sender_ip(request)
    cid = client_id.strip() or _get_client_id_by_ip(sender_ip) or ""
    stable_id = _normalize_device_id(device_id) if device_id.strip() else ""

    # ── 权限检查 ──
    if not _is_authorized(stable_id, filename, cid):
        raise HTTPException(status_code=403, detail="无权访问此文件（仅发送方和接收方可下载）")

    filepath = _resolve_upload_file(filename)
    if not filepath.exists():
        return {"error": "文件不存在"}
    original = _download_filename(filename)
    return FileResponse(filepath, filename=original, media_type="application/octet-stream")


@app.post("/delete/{filename:path}")
async def delete_file(request: Request, filename: str, device_id: str = "", client_id: str = ""):
    """删除文件（仅发送方或接收方可操作）"""
    sender_ip = _extract_sender_ip(request)
    cid = client_id.strip() or _get_client_id_by_ip(sender_ip) or ""
    stable_id = _normalize_device_id(device_id) if device_id.strip() else ""

    # ── 权限检查 ──
    if not _is_authorized(stable_id, filename, cid):
        raise HTTPException(status_code=403, detail="无权删除此文件")

    filepath = _resolve_upload_file(filename)
    if not filepath.exists():
        return {"error": "文件不存在"}
    try:
        filepath.unlink()
        _delete_file_metadata(filename)
        _save_file_metadata()
        await _broadcast_to_all({"type": "file_deleted", "filename": filename})
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "online_devices": len(connected_clients),
        "storage_files": len([f for f in UPLOAD_DIR.iterdir() if f.is_file()]),
    }


# ========== 启动入口 ==========
if __name__ == "__main__":
    print("=" * 60)
    print("  [FileTransfer] 局域网文件传输工具")
    print("=" * 60)

    local_ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    print(f"\n  [Local]   http://localhost:{PORT}")
    print(f"  [LAN]     http://{local_ip}:{PORT}")
    print(f"\n  [Storage] {UPLOAD_DIR.absolute()}")
    print(f"  [TTL]     {FILE_RETENTION_DAYS} days")
    print(f"  [MaxSize] {MAX_FILE_SIZE_LABEL}")

    if local_ip != "127.0.0.1":
        print(f"\n  [Public IP]")
        print(f"     Router port-forward: {PORT} -> {local_ip}")
        print(f"     Public URL: http://<your-IP>:{PORT}")

    print("\n  Ctrl+C to stop\n")
    print("=" * 60)

    uvicorn.run(app, host=HOST, port=PORT)
