# video-hub 全栈镜像: FastAPI 管理台 + yt-dlp/ffmpeg 处理 + sau(patchright) 多平台投递
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Asia/Shanghai

# ffmpeg: 黑段修剪/抽帧/字幕烧录; fonts-noto-cjk: 中文字幕字形; 其余为 chromium 无头运行库
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-noto-cjk curl ca-certificates tzdata \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
      libatspi2.0-0 libx11-6 libxcb1 libxext6 libxi6 libxtst6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 主环境
COPY requirements.txt ./
RUN python -m venv .venv \
    && .venv/bin/pip install --no-cache-dir -r requirements.txt

# sau 投递引擎 + patchright chromium(国内可设 PLAYWRIGHT_DOWNLOAD_HOST 镜像)
COPY vendor/sau ./vendor/sau
RUN python -m venv vendor/sau/.venv \
    && vendor/sau/.venv/bin/pip install --no-cache-dir -e ./vendor/sau \
    && vendor/sau/.venv/bin/patchright install chromium

COPY pipeline ./pipeline
COPY web ./web
COPY mcp_server.py cli.py ./

# biliup 二进制不随镜像(平台架构相关): 以卷挂载 bin/biliup
EXPOSE 8600
VOLUME ["/app/data", "/app/config", "/app/vendor/sau/cookies", "/app/bin"]

CMD [".venv/bin/python", "cli.py", "serve", "--host", "0.0.0.0", "--port", "8600"]
