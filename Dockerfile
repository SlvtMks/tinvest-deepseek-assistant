# ---------- Базовый образ ----------
FROM python:3.12-slim

# Не создавать .pyc, не буферизовать stdout (важно для логов на Railway),
# не кэшировать пакеты внутрь слоёв.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8501

WORKDIR /app

# ca-certificates — TLS для реестров, curl — healthcheck, git — запасной способ
# установки SDK Т-Инвестиций (см. шаг с pip ниже).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# Зависимости отдельным слоем — быстрее пересборка.
COPY requirements.txt requirements-sdk.txt ./
# Основной путь: официальный SDK из реестра Т-Банка (t-tech-investments).
# Если реестр недоступен/сертификат не принимается — ставим тот же SDK из
# GitHub-зеркала (импорт tinkoff.invest; слой sdk.py поддерживает оба варианта).
RUN python -m pip install --upgrade pip \
    && (python -m pip install --no-cache-dir -r requirements.txt \
        || (echo ">>> реестр Т-Банка недоступен, ставлю SDK из GitHub" \
            && python -m pip install --no-cache-dir openai python-dotenv rich streamlit \
            && python -m pip install --no-cache-dir "tinkoff-investments @ git+https://github.com/RussianInvestments/invest-python.git"))

# Код приложения.
COPY . .

# Непривилегированный пользователь.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/_stcore/health" || exit 1

# Shell-форма нужна, чтобы ${PORT} подставился из окружения Railway.
CMD streamlit run streamlit_app.py \
        --server.address=0.0.0.0 \
        --server.port=${PORT} \
        --server.headless=true \
        --server.enableCORS=false \
        --server.enableXsrfProtection=false \
        --browser.gatherUsageStats=false
