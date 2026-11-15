"""Точка входа Streamlit (Railway / Docker / локально).

    streamlit run streamlit_app.py

Streamlit требует, чтобы модуль запуска лежал в корне — поэтому логика
в ``tinvest_ai/webapp.py``, а здесь только вызов.
"""

from __future__ import annotations

from tinvest_ai.webapp import main

main()
