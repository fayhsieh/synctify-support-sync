# 轉換 HTTP microservice 容器
# build context 必須是 repo 根目錄（app.py 會 import 同層的 converter/）：
#   docker build -f service/Dockerfile -t synctify-converter .
FROM python:3.12-slim

WORKDIR /app

# 先裝依賴（利用 layer cache）
COPY service/requirements.txt /app/service/requirements.txt
RUN pip install --no-cache-dir -r /app/service/requirements.txt

# 轉換器（映射邏輯單一真實來源）與 service 一起帶進來，維持 sibling 結構
COPY converter/ /app/converter/
COPY service/ /app/service/

# 非 root 執行
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8800

# 健康檢查（slim 沒有 curl，用 python 打 /health）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/health').status==200 else 1)"

# 選用認證：設 CONVERTER_API_KEY 後 /convert 需帶 Authorization: Bearer <key>
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8800"]
