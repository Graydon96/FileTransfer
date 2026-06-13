# FileTransfer
Transfer files between different devices

## Docker configuration

Set the max upload size with `MAX_FILE_SIZE`:

```bash
docker run -d --name filetransfer -p 8080:8080 -e MAX_FILE_SIZE=5GB graydon96/filetransfer:latest
```

Supported formats include `500MB`, `2GB`, and raw bytes such as `1073741824`.
