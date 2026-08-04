.DEFAULT_GOAL := help

.PHONY: help install install-backend install-frontend dev-backend dev-frontend build check test test-fuzz test-coverage run macos-app

help:
	@echo "make install           安装前后端依赖"
	@echo "make dev-backend       启动 FastAPI（127.0.0.1:8000）"
	@echo "make dev-frontend      启动 Vite（127.0.0.1:5173）"
	@echo "make build             构建前端生产包"
	@echo "make check             检查前后端代码"
	@echo "make test              运行完整后端测试"
	@echo "make test-fuzz         运行可复现 Fuzz 测试"
	@echo "make test-coverage     运行测试并执行关键代码 98% 覆盖率门禁"
	@echo "make run               构建并启动个人视频工具"
	@echo "make macos-app         构建可双击启动的 macOS 应用"

install: install-backend install-frontend

install-backend:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -e 'backend[test]'

install-frontend:
	npm --prefix frontend install

dev-backend:
	backend/.venv/bin/uvicorn --app-dir backend app.main:app --host 127.0.0.1 --port 8000 --reload

dev-frontend:
	npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173

build:
	npm --prefix frontend run build

check:
	backend/.venv/bin/python -m compileall -q backend/app
	npm --prefix frontend run check

test:
	PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests

test-fuzz:
	PYTHONPATH=backend backend/.venv/bin/python -m unittest backend.tests.test_input_fuzz backend.tests.test_safety_fuzz -v

test-coverage:
	PYTHONPATH=backend backend/.venv/bin/python -m coverage erase
	PYTHONPATH=backend backend/.venv/bin/python -m coverage run --branch --source=app -m unittest discover -s backend/tests
	backend/.venv/bin/python backend/scripts/check_critical_coverage.py --fail-under 98
	backend/.venv/bin/python -m coverage report -m

run:
	@test -x backend/.venv/bin/python
	@command -v npm >/dev/null
	npm --prefix frontend run build
	backend/.venv/bin/python backend/run.py

macos-app:
	@test -x backend/.venv/bin/python
	@command -v npm >/dev/null
	@command -v clang >/dev/null
	npm --prefix frontend run build
	chmod +x scripts/build_macos_app.sh
	./scripts/build_macos_app.sh
