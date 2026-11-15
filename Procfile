# Для деплоя через Nixpacks (Railway) без Dockerfile.
# Если у сервиса выбран builder = Dockerfile (railway.json), этот файл не используется.
web: streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false --browser.gatherUsageStats=false
