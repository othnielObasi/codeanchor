.PHONY: help install test test-quick demo validate ui-patch ui-verify clean

help:
	@echo "codeanchor / TraceMemory Codex adapter"
	@echo ""
	@echo "  make install    install dependencies"
	@echo "  make demo       run the demo (no credentials needed)  <- START HERE"
	@echo "  make test       full suite with a capability summary"
	@echo "  make validate   check schema against a REAL ~/.codex session"
	@echo "  make ui-patch   add the Codex Recovery tab to TraceMemory's UI"
	@echo ""
	@echo "Optional: TRACEMEMORY_API_PATH=<repo>/services/api enables the"
	@echo "ContextHealthService integration tests."

install:
	pip install pydantic pytest fastapi --break-system-packages

demo:
	@python3 bin/codeanchor demo

test:
	@python3 scripts/report_capabilities.py

test-quick:
	@python3 -m pytest tests/ -q

validate:
	@python3 scripts/validate_against_real_rollout.py $(ARGS)

ui-patch:
	@python3 scripts/patch_ui_codex_tab.py $(REPO)

ui-verify:
	@node scripts/verify_ui_patch.js $(REPO)

clean:
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache
