FROM python:3.12-slim

LABEL org.opencontainers.image.title="项目库统计程序"
LABEL org.opencontainers.image.description="实验室项目库 · 月末多人协作统计"

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai \
    PROJLIB_HOST=0.0.0.0 \
    PROJLIB_PORT=8775 \
    PROJLIB_DATA_DIR=/data

WORKDIR /app

# 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 源码
COPY app/ /app/app/
COPY web/ /app/web/

# 说明：镜像不内置任何项目库数据，新实例的项目库为空，由管理员在「导入项目库」自行导入。

# 数据卷（数据库、导出文件都在这里）
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8775

HEALTHCHECK --interval=30s --timeout=5s --start-period=8s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PROJLIB_PORT','8775')+'/api/health', timeout=3)" || exit 1

CMD ["python", "-X", "utf8", "app/server.py"]
