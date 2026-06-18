# FileTransfer

**Language:** [English](#english) | [中文](#中文)

## English

Transfer files between different devices without login via different network

### For normal use

```bash
python main.py
```

### Docker configuration

Set the max upload size with `MAX_FILE_SIZE`, and the stored file retention period with `FILE_RETENTION_DAYS`:

Supported size formats include `500MB`, `2GB`, and raw bytes such as `1073741824`. `FILE_RETENTION_DAYS` is a number of days.

```bash
docker run -d --name filetransfer -p 8080:8080 -v ./conf:/data/conf -v ./uploads:/data/uploads -e MAX_FILE_SIZE=5GB -e FILE_RETENTION_DAYS=7 graydon96/filetransfer:latest
```

## 中文

FileTransfer 可以在不同网络的设备之间免登录传输文件。

### 普通方式运行

```bash
python main.py
```

### Docker 配置

可以通过 `MAX_FILE_SIZE` 设置最大上传文件大小，通过 `FILE_RETENTION_DAYS` 设置已存储文件的保留天数：

支持的大小格式包括 `500MB`、`2GB`，也可以直接填写字节数，例如 `1073741824`。`FILE_RETENTION_DAYS` 的值为天数。

```bash
docker run -d --name filetransfer -p 8080:8080 -v ./conf:/data/conf -v ./uploads:/data/uploads -e MAX_FILE_SIZE=5GB -e FILE_RETENTION_DAYS=7 graydon96/filetransfer:latest
```
