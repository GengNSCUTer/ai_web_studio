# 部署时必须传入审核过的 Python 镜像 digest；不可使用浮动 latest。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
COPY egress_proxy.py /opt/aiws/egress_proxy.py
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /out
ENTRYPOINT []
