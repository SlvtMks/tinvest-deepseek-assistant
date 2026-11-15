.PHONY: help install install-dev env run demo ask web test lint fmt cov docker-build docker-run clean

PY ?= python3
VENV ?= .venv

help:  ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Установить зависимости в venv
	$(PY) -m venv $(VENV)
	. $(VENV)/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

install-dev: install  ## + dev-зависимости (pytest, ruff)
	. $(VENV)/bin/activate && pip install -r requirements-dev.txt

env:  ## Создать .env из примера
	cp -n .env.example .env && echo "Откройте .env и вставьте токены"

run:  ## Интерактивный CLI-чат
	. $(VENV)/bin/activate && $(PY) -m tinvest_ai

demo:  ## CLI-чат на демо-данных (без ключей)
	. $(VENV)/bin/activate && DEMO_MODE=true $(PY) -m tinvest_ai

ask:  ## Один вопрос: make ask Q="покажи портфель"
	. $(VENV)/bin/activate && $(PY) -m tinvest_ai --ask "$(Q)"

web:  ## Streamlit-интерфейс на http://localhost:8501
	. $(VENV)/bin/activate && streamlit run streamlit_app.py

test:  ## Запустить тесты
	. $(VENV)/bin/activate && $(PY) -m pytest -q

cov:  ## Тесты с покрытием
	. $(VENV)/bin/activate && $(PY) -m pytest --cov=tinvest_ai --cov-report=term-missing

lint:  ## Проверка стиля и типичных ошибок
	. $(VENV)/bin/activate && ruff check .

fmt:  ## Автоформат
	. $(VENV)/bin/activate && ruff format . && ruff check --fix .

docker-build:  ## Собрать образ
	docker build -t tinvest-deepseek-assistant .

docker-run:  ## Запустить образ (нужен .env рядом)
	docker run --rm -p 8501:8501 --env-file .env tinvest-deepseek-assistant

clean:  ## Удалить кэши
	rm -rf .pytest_cache .ruff_cache __pycache__ tinvest_ai/__pycache__ tests/__pycache__ htmlcov .coverage
