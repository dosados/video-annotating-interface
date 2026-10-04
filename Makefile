PYTHON ?= python
.PHONY: build test
build:
	$(PYTHON) -m PyInstaller --clean --noconfirm video-annotating-interface.spec

test:
	$(PYTHON) -m pytest -q
	node tests/test_ui.cjs
