PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help venv install test lint fmt build clean

help:
	@echo "make venv    创建虚拟环境"
	@echo "make install 安装开发依赖"
	@echo "make test    运行单测"
	@echo "make lint    ruff 检查"
	@echo "make fmt     ruff 自动修复"
	@echo "make build   构建 wheel/sdist"

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"

test:
	$(BIN)/python -m pytest

lint:
	$(BIN)/ruff check src tests

fmt:
	$(BIN)/ruff check --fix src tests

build:
	$(BIN)/python -m build

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
