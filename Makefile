.PHONY: test-export

test-export:
	python -m export_pipeline.cli export --channel prom --format xml
	@FOUND=$$(find exports -maxdepth 1 -name 'priceua_*.xml' -print -quit); \
	if [ -z "$$FOUND" ]; then \
		echo "priceua export file not found" >&2; exit 1; \
	else \
		echo "XML created: $$FOUND"; \
	fi
