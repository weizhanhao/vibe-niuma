FROM python:3.11-slim

# 走清华镜像（国内 ECS 直连 pypi.org 25kB/s 装一半就超时；
# build 时可以 --build-arg PIP_INDEX_URL=... 覆盖）
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=$PIP_INDEX_URL

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "demo_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
