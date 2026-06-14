# FileTransfer
Transfer files between different devices without login

## Docker configuration

Set the max upload size with `MAX_FILE_SIZE`, and the stored file retention period with `FILE_RETENTION_DAYS`:

```bash
docker run -d --name filetransfer -p 8080:8080 -e MAX_FILE_SIZE=5GB -e FILE_RETENTION_DAYS=7 graydon96/filetransfer:latest
```

Supported size formats include `500MB`, `2GB`, and raw bytes such as `1073741824`. `FILE_RETENTION_DAYS` is a number of days.
